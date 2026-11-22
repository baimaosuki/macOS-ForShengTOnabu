// SPDX-License-Identifier: GPL-2.0-only
#include "OriginalUsbXhciABI.h"
#include "NabuInputHardware.h"
#include "NabuUsb2Core.h"
#include "NabuUsbDma.h"
#include "NabuUsbTransfer.h"
#include <IOKit/IOBufferMemoryDescriptor.h>
#include "NabuUsbRole.h"
#include "NabuRpmh.h"
#include <IOKit/IOPlatformExpert.h>
#include <libkern/c++/OSArray.h>
#include <libkern/c++/OSData.h>
#include <kern/thread.h>
#include <mach/kmod.h>
extern "C" {
int armcpu_spi_attach(uint32_t,bool,void (*)(void *),void *,uint32_t *,uint64_t *);
int armcpu_spi_mask(uint32_t,bool);
kern_return_t OSKextLoadKextWithIdentifier(const char *);
extern kmod_info_t g_kernel_kmod_info;
// Exact 26C5035r native factory/callback bindings. Keep Apple's DMACommand
// subclass, mapper sessions, segment limits and teardown; change only the
// coherency contract passed to its factory.
IODMACommand *nabu_usb_dma_with_service(IODMACommand::SegmentFunction,
    bool (*)(OSObject *,IODMACommand::Segment64,uint32_t,bool),IOService *,
    IODMACommand::MappingOptions,void *);
bool nabu_usb_dma_aligned_mapper(OSObject *,IODMACommand::Segment64,uint32_t,bool);
unsigned int pmap_cache_attributes(unsigned int);
int pmap_valid_page(unsigned int);
vm_offset_t ml_vtophys(vm_offset_t);
vm_map_address_t phystokv_range(uint64_t,vm_size_t *);
vm_size_t ml_nofault_copy(vm_offset_t,vm_offset_t,vm_size_t);
extern uint64_t gPhysBase,gPhysSize;
void nabu_usb_static_init(void) __asm__("__GLOBAL__sub_I_NabuUsbHost.cpp");
}
namespace {
constexpr uint32_t irq=165,sid=0x140;
struct Hardware {
    IOBufferMemoryDescriptor *dmaTable=nullptr;
    uint32_t dmaBank=0,dmaSlot=0,dmaGlobalPages=0;
    bool dmaReady=false;
    nabu::RegisterWindow gcc,phy,scratch,core,smmu,rsc,dbPointer,db,rcal;
    nabu::RegisterWindow &region(nabu::UsbBlock b){
        switch(b){case nabu::UsbBlock::Gcc:return gcc;case nabu::UsbBlock::Phy:return phy;
        case nabu::UsbBlock::Scratch:return scratch;case nabu::UsbBlock::Core:return core;}
        __builtin_unreachable();
    }
    uint32_t read(nabu::UsbBlock b,uint32_t off){return region(b).read(off);}
    void write(nabu::UsbBlock b,uint32_t off,uint32_t v){region(b).write(off,v);}
    void delayUs(unsigned us){IODelay(us);}
    void checkpoint(const char *phase){IOLog("NABU USB: init enter=%s\n",phase);}
    bool map(){return gcc.map(0x100000,0x100000)&&phy.map(0x88e2000,0x1000)&&
        scratch.map(0xa6f8800,0x400)&&rcal.map(0x7801f8,4)&&core.map(0xa600000,0x10000)&&smmu.map(0x15000000,0x100000);}
    bool rails(){
        if(!dbPointer.map(0xc3f000c,8)||!rsc.map(0x18220000,0x4000))return false;
        uint32_t address=dbPointer.read(0),bytes=dbPointer.read(4);
        if(address<0x80000000 || bytes<144 || bytes>0x40000 || !db.map(address,bytes))return false;
        // DB is immutable reserved memory. Copy volatile bytes before parsing.
        auto *copy=static_cast<uint8_t *>(IOMalloc(bytes));if(!copy)return false;
        const volatile uint8_t *src=reinterpret_cast<volatile uint8_t *>(db.words);
        for(unsigned i=0;i<bytes;++i)copy[i]=src[i];
        struct TcsIo {nabu::RegisterWindow &w;uint32_t read(uint32_t o){return w.read(o);}
            void write(uint32_t o,uint32_t v){w.write(o,v);}void delayUs(unsigned us){IODelay(us);}} io{rsc};
        nabu::RpmhTcs<TcsIo> tcs(io);
        const char *names[]={"ldoa5","ldoa12","ldoa2"};const uint32_t millivolts[]={880,1800,3072};
        bool ok=true;
        for(unsigned i=0;i<3 && ok;++i){
            uint32_t resource=0;ok=nabu::cmdDbAddress(copy,bytes,names[i],resource);
            if(ok)ok=tcs.send(0,resource,millivolts[i])&&tcs.send(0,resource+8,7)&&tcs.send(0,resource+4,1);
            IOLog("NABU USB: RPMh %s addr=%08x mV=%u complete=%u\n",names[i],resource,millivolts[i],ok);
        }
        uint32_t xo=0;
        if(ok)ok=nabu::cmdDbAddress(copy,bytes,"xo.lvl",xo)&&tcs.send(0,xo,3);
        IOLog("NABU USB: RPMh XO addr=%08x vote=3 complete=%u\n",xo,ok);
        IOFree(copy,bytes);return ok;
    }
    bool dma(){
        namespace sm=nabu_usb::smmu;
        auto read=[this](uint32_t o){return smmu.read(o);};
        sm::Info info{};if(!sm::read_info(read,&info) || ((info.idr2>>4)&15)<1)return false;
        auto plan=nabu::usbIdentityPlan(read,info,sid);
        if(!plan.valid){IOLog("NABU USB: SMMU refused %s\n",plan.error);return false;}
        if(plan.index==2 || plan.bank==1){IOLog("NABU USB: refused UFS reserved slot/CB\n");return false;}
        IOLog("NABU USB: SMMU plan slot=%u CB=%u old_sctlr=%08x cbar=%08x cba2r=%08x M=0 unreferenced=1\n",
            plan.index,plan.bank,plan.sctlr,plan.cbar,plan.cba2r);
        const uint32_t ufsSmr=smmu.read(sm::kSmrBase+8),ufsRoute=smmu.read(sm::kS2crBase+8);
        const uint32_t ufsCb=sm::context_offset(info.global_pages,1);
        const uint32_t ufsRegisters[]={0,0x10,0x20,0x24,0x28,0x2c,0x30,0x38,0x3c};
        uint32_t ufsBefore[9];for(unsigned i=0;i<9;++i)ufsBefore[i]=smmu.read(ufsCb+ufsRegisters[i]);
        dmaTable=IOBufferMemoryDescriptor::withOptions(IOOptionBits(kIODirectionInOut)|IOOptionBits(kIOMemoryPhysicallyContiguous),4096,4096);
        if(!dmaTable || dmaTable->prepare()!=kIOReturnSuccess)return false;
        auto *table=static_cast<uint64_t *>(dmaTable->getBytesNoCopy());
        uint64_t span=0,pa=dmaTable->getPhysicalSegment(0,&span,0);
        if(!table || !pa || (pa&4095) || span<4096 || pa>=(1ull<<36))return false;
        for(unsigned i=0;i<512;++i)table[i]=nabu::usbIdentityBlock(i);
        for(uintptr_t at=reinterpret_cast<uintptr_t>(table);at<reinterpret_cast<uintptr_t>(table)+4096;at+=64)
            asm volatile("dc cvac, %0"::"r"(at):"memory");
        asm volatile("dsb sy":::"memory");
        const auto current=nabu::usbIdentityPlan(read,info,sid);
        if(!current.valid || current.index!=plan.index || current.bank!=plan.bank ||
            current.sctlr!=plan.sctlr || current.cbar!=plan.cbar || current.cba2r!=plan.cba2r) {
            IOLog("NABU USB: SMMU candidate changed before programming\n");return false;
        }
        uint32_t cb=sm::context_offset(info.global_pages,plan.bank);
        // Disable only USB's exact stream; UFS and all other firmware mappings survive.
        smmu.write(sm::kSmrBase+4*plan.index,0);
        smmu.write(cb,0);
        if(smmu.read(cb)!=0){IOLog("NABU USB: SMMU context disable readback failed CB=%u\n",plan.bank);return false;}
        smmu.write(sm::kCba2rBase+4*plan.bank,1);
        smmu.write(sm::kCbarBase+4*plan.bank,(1u<<16)|(15u<<12)|(3u<<8));
        smmu.write(cb+0x10,1u|(7u<<15)); // 36-bit output, matching SM8150 DRAM
        smmu.write(cb+0x30,28u|(1u<<8)|(1u<<10)|(3u<<12)|(1u<<23));
        smmu.write(cb+0x20,uint32_t(pa));smmu.write(cb+0x24,uint32_t(pa>>32));
        smmu.write(cb+0x28,0);smmu.write(cb+0x2c,0);
        smmu.write(cb+0x38,nabu::usbDmaMair);smmu.write(cb+0x3c,0);
        smmu.write(cb+0x610,0);smmu.write(cb+0x7f0,0);
        if(!smmu.wait(cb+0x7f4,1,0,10000))return false;
        smmu.write(cb+0x58,smmu.read(cb+0x58));
        smmu.write(cb,1u|2u|4u|(1u<<5)|(1u<<12));
        if(!(smmu.read(cb)&1) || smmu.read(cb+0x20)!=uint32_t(pa) || smmu.read(cb+0x38)!=nabu::usbDmaMair)return false;
        smmu.write(sm::kS2crBase+4*plan.index,plan.bank);
        smmu.write(sm::kSmrBase+4*plan.index,sm::kSmrValid|sid);
        bool ok=smmu.read(sm::kSmrBase+4*plan.index)==(sm::kSmrValid|sid) && smmu.read(sm::kS2crBase+4*plan.index)==plan.bank;
        bool ufsPreserved=smmu.read(sm::kSmrBase+8)==ufsSmr && smmu.read(sm::kS2crBase+8)==ufsRoute;
        for(unsigned i=0;i<9;++i)ufsPreserved=ufsPreserved && smmu.read(ufsCb+ufsRegisters[i])==ufsBefore[i];
        IOLog("NABU USB: UFS slot2=%08x route=%08x CB1 preserved=%u USB slot=%u CB=%u\n",ufsSmr,ufsRoute,ufsPreserved,plan.index,plan.bank);
        if(!ufsPreserved)return false;
        dmaBank=plan.bank;dmaSlot=plan.index;dmaGlobalPages=info.global_pages;dmaReady=ok;
        IOLog("NABU USB: SID=%x slot=%u context=%u identity=%u table=%llx GFSR=%x output=Normal-NC-inner-shareable table_walk=WBWA coherence=unverified\n",sid,plan.index,plan.bank,ok,pa,smmu.read(sm::kGfsr));
        return ok;
    }
} hardware;
} // namespace
class NabuXhciNub;
NabuXhciNub *active=nullptr;bool lineAttached=false;
struct UsbIrqCounters {uint32_t arrivals=0,forwarded=0,inactive=0,masks=0,unmasks=0,errors=0,lastStatus=0,inFlight=0;} usbIrqs;
int usbMask(bool masked) {
    __atomic_fetch_add(masked?&usbIrqs.masks:&usbIrqs.unmasks,1,__ATOMIC_RELAXED);
    const int status=armcpu_spi_mask(irq,masked);
    __atomic_store_n(&usbIrqs.lastStatus,uint32_t(status),__ATOMIC_RELAXED);
    if(status)__atomic_fetch_add(&usbIrqs.errors,1,__ATOMIC_RELAXED);
    return status;
}
void usbQuiesceInterrupt() {
    // Match IOInterruptController: thread-context disable/unregister waits for
    // the primary action; a level filter disabling itself must never wait.
    if(!IOService::getPlatform()->atInterruptLevel())
        while(__atomic_load_n(&usbIrqs.inFlight,__ATOMIC_SEQ_CST))IODelay(1);
}
class NabuXhciNub : public IOService {
    OSDeclareDefaultStructors(NabuXhciNub)
    IOInterruptAction action=nullptr;OSObject *target=nullptr;void *refcon=nullptr;uint32_t enabled=0;
    static void primary(void *) {
        __atomic_fetch_add(&usbIrqs.inFlight,1,__ATOMIC_SEQ_CST);
        __atomic_fetch_add(&usbIrqs.arrivals,1,__ATOMIC_RELAXED);
        auto *self=__atomic_load_n(&active,__ATOMIC_SEQ_CST);
        const auto handler=self?__atomic_load_n(&self->action,__ATOMIC_ACQUIRE):nullptr;
        if(!self || !__atomic_load_n(&self->enabled,__ATOMIC_ACQUIRE) || !handler) {
            // armcpu_spi_attach enables the level line before it returns.
            // An asserted source during registration must be masked here,
            // otherwise ArmCpu's IRQ drain can never return to registration.
            __atomic_fetch_add(&usbIrqs.inactive,1,__ATOMIC_RELAXED);usbMask(true);
            __atomic_fetch_sub(&usbIrqs.inFlight,1,__ATOMIC_SEQ_CST);return;
        }
        __atomic_fetch_add(&usbIrqs.forwarded,1,__ATOMIC_RELAXED);
        // IO[Filter]InterruptEventSource owns level masking/re-enable after
        // its filter accepts the IRQ. Do not unconditionally re-enable here.
        handler(self->target,self->refcon,self,0);
        __atomic_fetch_sub(&usbIrqs.inFlight,1,__ATOMIC_SEQ_CST);
    }
public:
    bool interruptEnabled() const {return __atomic_load_n(&enabled,__ATOMIC_ACQUIRE)!=0;}
    IOReturn getInterruptType(int source,int *type) override {
        if(source || !type)return kIOReturnNoInterrupt;*type=kIOInterruptTypeLevel;return kIOReturnSuccess;
    }
    IOReturn registerInterrupt(int source,OSObject *owner,IOInterruptAction handler,void *context) override {
        if(source || !handler)return kIOReturnNoInterrupt;
        if(__atomic_load_n(&action,__ATOMIC_ACQUIRE))return kIOReturnNoResources;
        target=owner;refcon=context;__atomic_store_n(&enabled,0,__ATOMIC_RELEASE);
        __atomic_store_n(&action,handler,__ATOMIC_RELEASE);__atomic_store_n(&active,this,__ATOMIC_SEQ_CST);
        if(!lineAttached){
            uint32_t config=0;uint64_t route=0;
            auto r=armcpu_spi_attach(irq,false,primary,nullptr,&config,&route);
            if(r){__atomic_store_n(&active,nullptr,__ATOMIC_SEQ_CST);usbQuiesceInterrupt();__atomic_store_n(&action,nullptr,__ATOMIC_RELEASE);return r;}lineAttached=true;
            IOLog("NABU USB: GIC intid=%u config=%08x router=%llx type=level\n",irq,config,route);
        }
        return usbMask(true);
    }
    IOReturn unregisterInterrupt(int source) override {
        if(source)return kIOReturnNoInterrupt;__atomic_store_n(&enabled,0,__ATOMIC_RELEASE);auto r=usbMask(true);
        __atomic_store_n(&active,nullptr,__ATOMIC_SEQ_CST);usbQuiesceInterrupt();
        __atomic_store_n(&action,nullptr,__ATOMIC_RELEASE);target=nullptr;refcon=nullptr;return r;
    }
    IOReturn enableInterrupt(int source) override {
        if(source||!__atomic_load_n(&action,__ATOMIC_ACQUIRE))return kIOReturnNoInterrupt;
        usbQuiesceInterrupt();
        __atomic_store_n(&enabled,1,__ATOMIC_RELEASE);const auto status=usbMask(false);
        if(status)__atomic_store_n(&enabled,0,__ATOMIC_RELEASE);return status;
    }
    IOReturn disableInterrupt(int source) override {
        if(source)return kIOReturnNoInterrupt;__atomic_store_n(&enabled,0,__ATOMIC_RELEASE);
        const auto status=usbMask(true);usbQuiesceInterrupt();return status;
    }
};
OSDefineMetaClassAndStructors(NabuXhciNub,IOService)
struct UsbRegisterIo {
    volatile uint32_t *words;
    uint32_t read32(uint32_t off) const {return words[off/4];}
    void write32(uint32_t off,uint32_t v) {words[off/4]=v;}
    void readBarrier() {asm volatile("dmb oshld":::"memory");}
    void writeBarrier() {asm volatile("dmb oshst":::"memory");}
    void complete() {asm volatile("dsb sy":::"memory");}
};
static_assert(kIOMapCacheMask==0xf00 && kIOMapWriteCombineCache==0x400,"USB cache ABI");
namespace {
nabu::UsbTransferTrace usbTransfers;
struct UsbTraceIo {
    uint32_t reg(unsigned off) const {return hardware.core.read(off);}
    uint32_t status() const {const unsigned cap=reg(0)&255;return cap>=0x20 && cap<=0x80?reg(cap+4):~0u;}
    unsigned cache(uint64_t pa) const {return pmap_cache_attributes(unsigned(pa>>PAGE_SHIFT));}
    void readBarrier() const {
#if defined(__aarch64__)
        asm volatile("dmb oshld":::"memory");
#else
        __atomic_thread_fence(__ATOMIC_ACQUIRE);
#endif
    }
    bool read(uint64_t pa,void *output,unsigned bytes) const {
        // Do not trust a DMA pointer enough to fault the kernel. Bound every
        // sample to DRAM, split at XNU physmap aperture/page boundaries and
        // use the native no-fault accessor. No manual gVirtBase arithmetic.
        if(!bytes || bytes>128 || pa<gPhysBase || pa-gPhysBase>=gPhysSize || bytes>gPhysSize-(pa-gPhysBase))return false;
        // Normal-NC is not an acquire operation. Order the observed event
        // cycle/status before sampling the device's preceding DATA IN writes.
        readBarrier();
        auto *destination=static_cast<uint8_t *>(output);
        while(bytes) {
            if(!pmap_valid_page(unsigned(pa>>PAGE_SHIFT)))return false;
            vm_size_t span=PAGE_SIZE-(pa&PAGE_MASK);if(span>bytes)span=bytes;
            const auto address=phystokv_range(pa,&span);
            if(!address || !span || span>bytes || ml_nofault_copy(address,reinterpret_cast<vm_offset_t>(destination),span)!=span)return false;
            readBarrier();
            destination+=span;pa+=span;bytes-=unsigned(span);
        }
        return true;
    }
};
bool usbRegisterOffset(void *base,unsigned offset,unsigned &result) {
    // Native AppleUSBXHCI owns a different mapping from hardware.core.
    // Resolve the real register PA; never compare unrelated kernel VAs.
    const auto address=reinterpret_cast<vm_offset_t>(base)+offset;
    const uint64_t pa=ml_vtophys(address);
    if(pa<0xa600000 || pa>=0xa610000)return false;
    result=unsigned(pa-0xa600000);return true;
}
void drainTransfers() {
    nabu::UsbTraceRecord record;
    for(unsigned i=0;i<64 && usbTransfers.take(record);++i) {
        IOLog("NABU USB: transfer-trace seq=%u kind=%u addr=%llx related=%llx tag=%x STS=%08x cache=%x valid=%x trb=%08x,%08x,%08x,%08x sample=%08x,%08x,%08x,%08x\n",
            record.sequence,unsigned(record.kind),record.address,record.related,record.tag,record.status,record.cache,record.valid,
            record.trb.word[0],record.trb.word[1],record.trb.word[2],record.trb.word[3],record.bytes[0],record.bytes[1],record.bytes[2],record.bytes[3]);
    }
    static unsigned previous=0;const unsigned lost=usbTransfers.dropped();
    if(lost!=previous){IOLog("NABU USB: transfer-trace dropped=%u (bounded nonblocking capture)\n",lost);previous=lost;}
}
}
namespace {void reportRuntime(const nabu::UsbHostSnapshot &);}
class NabuUsbXhci : public AppleUSBXHCI {
    OSDeclareDefaultStructors(NabuUsbXhci)
public:
    bool configureDmaCache() {
        static_assert(sizeof(AppleUSBXHCI)==nabu::UsbControllerCachePolicy27::instanceBytes,"26C5035r cache fields");
        struct Fields {
            uint8_t *object;
            uint32_t read(unsigned off){uint32_t value;__builtin_memcpy(&value,object+off,sizeof(value));return value;}
            void write(unsigned off,uint32_t value){__builtin_memcpy(object+off,&value,sizeof(value));}
        } fields{reinterpret_cast<uint8_t *>(this)};
        nabu::UsbControllerCachePolicy27 policy;const bool ok=policy.install(fields);
        IOLog("NABU USB: controller-cache dma=%08x->%08x client=%08x->%08x CPU=Normal-NC SMMU=Normal-NC verified=%u\n",
            policy.oldDma,policy.dma,policy.oldClient,policy.client,ok);
        return ok;
    }
    uint32_t desiredPower() override {return 3;}
    uint32_t getBufferOptions(uint32_t options) const override {
        const auto result=nabu::usbBufferOptions(AppleUSBXHCI::getBufferOptions(options));
        static uint32_t reports=0;
        if(__atomic_fetch_add(&reports,1,__ATOMIC_RELAXED)<8)
            IOLog("NABU USB: client-cache requested=%08x effective=%08x policy=Normal-NC\n",options,result);
        return result;
    }
    IOBufferMemoryDescriptor *createIOBuffer(uint32_t options,uint64_t size) override {
        // Preserve native allocation address masks/alignment and capabilities.
        const uint32_t nc=nabu::usbBufferOptions(options);
        auto *buffer=AppleUSBXHCI::createIOBuffer(nc,size);
        static uint32_t reports=0;const auto sample=__atomic_fetch_add(&reports,1,__ATOMIC_RELAXED);
        if(sample<8 || !buffer) {
            uint64_t span=0;const uint64_t pa=buffer?buffer->getPhysicalSegment(0,&span,0):0;
            IOLog("NABU USB: buffer bytes=%llu options=%08x PA=%llx span=%llu cpu_cache=%x policy=Normal-NC allocated=%u\n",
                size,nc,pa,span,pa?pmap_cache_attributes(unsigned(pa>>PAGE_SHIFT)):~0u,buffer!=nullptr);
        }
        return buffer;
    }
    IODMACommand *getDMACommand(uint32_t,uint32_t) override {
        // Native 26C5035r uses OutputHost64 + alignedMapper for all caller
        // options, but hard-codes kMapped. kNonCoherent makes prepare/complete
        // clean/invalidate borrowed buffers as well as any bounce buffers.
        auto *command=nabu_usb_dma_with_service(kIODMACommandOutputHost64,
            nabu_usb_dma_aligned_mapper,this,IODMACommand::kNonCoherent,nullptr);
        static uint32_t reports=0;const auto sample=__atomic_fetch_add(&reports,1,__ATOMIC_RELAXED);
        if(sample<4 || !command)IOLog("NABU USB: DMACommand mapping=noncoherent prepare=clean complete=invalidate allocated=%u\n",command!=nullptr);
        return command;
    }
    IOReturn lowerOnePowerStateTo(unsigned long state) override {
        // Capture the actual transition, rather than inferring halt from a
        // 250ms poll after native recovery has already re-enabled RS.
        nabu::Usb2Core<Hardware> core(hardware);const auto before=core.snapshot();
        const auto result=AppleUSBXHCI::lowerOnePowerStateTo(state);const auto after=core.snapshot();
        static nabu::UsbPowerReportPolicy reports;
        if(reports.sample(unsigned(state),uint32_t(result),after.command,after.status)) {
            IOLog("NABU USB: power target=%lu result=%08x before_CMD=%08x before_STS=%08x after_CMD=%08x after_STS=%08x\n",
                state,result,before.command,before.status,after.command,after.status);
            if(result!=kIOReturnSuccess)reportRuntime(after);
        }
        return result;
    }
    uint32_t regRead32(void *base,uint32_t off) override {
        UsbRegisterIo io{reinterpret_cast<volatile uint32_t *>(base)};
        const auto value=nabu::usbReadRegister32(io,off);
        unsigned location;
        if(off==4 && usbRegisterOffset(base,off,location) && location==(hardware.core.read(0)&255)+4) {
            UsbTraceIo trace;usbTransfers.status(trace,value);
        }
        return value;
    }
    void regWrite32(void *base,uint32_t off,uint32_t value) override {
        UsbRegisterIo io{reinterpret_cast<volatile uint32_t *>(base)};
        unsigned location;
        if(usbRegisterOffset(base,off,location)) {
            io.writeBarrier();UsbTraceIo trace;usbTransfers.write32(trace,location,value);
        }
        nabu::usbWriteRegister32(io,off,value);
    }
    uint64_t regRead64(void *base,uint32_t off) override {
        UsbRegisterIo io{reinterpret_cast<volatile uint32_t *>(base)};
        return nabu::usbReadRegister64(io,off);
    }
    void regWrite64(void *base,uint32_t off,uint64_t value) override {
        UsbRegisterIo io{reinterpret_cast<volatile uint32_t *>(base)};
        unsigned location;
        if(usbRegisterOffset(base,off,location)) {
            io.writeBarrier();UsbTraceIo trace;usbTransfers.write64(trace,location,value);
        }
        nabu::usbWriteRegister64(io,off,value);
    }
};
OSDefineMetaClassAndStructors(NabuUsbXhci,AppleUSBXHCI)
namespace {
NabuUsbXhci *host=nullptr;NabuXhciNub *nub=nullptr;
void reportRuntime(const nabu::UsbHostSnapshot &s) {
    auto count=[](const uint32_t *p){return __atomic_load_n(p,__ATOMIC_RELAXED);};
    IOLog("NABU USB: runtime CMD=%08x STS=%08x halt_pending=%u PORT0=%08x PORT1=%08x PLS=%u speed=%u\n",
        s.command,s.status,s.haltPending(),s.port[0],s.port[1],(s.port[0]>>5)&15,(s.port[0]>>10)&15);
    IOLog("NABU USB: rings CRCR=%08x:%08x DCBAA=%08x:%08x runtime_valid=%u IMAN=%08x ERSTSZ=%u ERST=%08x:%08x ERDP=%08x:%08x\n",
        s.crcrHi,s.crcrLo,s.dcbaaHi,s.dcbaaLo,s.runtimeValid,s.iman,s.erstSize,s.erstHi,s.erstLo,s.erdpHi,s.erdpLo);
    IOLog("NABU USB: irq arrivals=%u forwarded=%u inactive=%u mask=%u unmask=%u errors=%u last=%08x logical_enabled=%u in_flight=%u\n",
        count(&usbIrqs.arrivals),count(&usbIrqs.forwarded),count(&usbIrqs.inactive),
        count(&usbIrqs.masks),count(&usbIrqs.unmasks),count(&usbIrqs.errors),count(&usbIrqs.lastStatus),nub?nub->interruptEnabled():false,count(&usbIrqs.inFlight));
    IOLog("NABU USB: glue GCTL=%08x USB2=%08x GUCTL=%08x GUCTL1=%08x BUS0=%08x BUS1=%08x CGCTL=%08x MASTER=%08x CFG=%08x UTMI=%08x SLEEP=%08x\n",
        s.gctl,s.usb2,s.guctl,s.guctl1,s.bus0,s.bus1,s.cgctl,hardware.gcc.read(0xf010),hardware.gcc.read(0xf078),hardware.gcc.read(0xf018),hardware.gcc.read(0xf014));
    const uint64_t dcbaa=uint64_t(s.dcbaaHi)<<32|s.dcbaaLo,erst=uint64_t(s.erstHi)<<32|s.erstLo;
    IOLog("NABU USB: actual-ring-cache DCBAA=%x ERST=%x CPU_expected=6(Normal-NC) SMMU_MAIR_expected=44\n",
        dcbaa?pmap_cache_attributes(unsigned(dcbaa>>PAGE_SHIFT)):~0u,
        erst?pmap_cache_attributes(unsigned(erst>>PAGE_SHIFT)):~0u);
    if(hardware.dmaReady) {
        namespace sm=nabu_usb::smmu;const uint32_t cb=sm::context_offset(hardware.dmaGlobalPages,hardware.dmaBank);
        IOLog("NABU USB: dma SID=%x slot=%u CB=%u GFSR=%08x FSR=%08x FAR=%08x:%08x FSYNR0=%08x SCTLR=%08x TCR=%08x MAIR0=%08x route=%08x coherence=unverified\n",
            sid,hardware.dmaSlot,hardware.dmaBank,hardware.smmu.read(sm::kGfsr),hardware.smmu.read(cb+0x58),
            hardware.smmu.read(cb+0x64),hardware.smmu.read(cb+0x60),hardware.smmu.read(cb+0x68),hardware.smmu.read(cb),
            hardware.smmu.read(cb+0x30),hardware.smmu.read(cb+0x38),hardware.smmu.read(sm::kS2crBase+4*hardware.dmaSlot));
    }
}
bool publishHost(){
    nabu::FamilyRegistration registration;if(!registration.valid())return false;
    auto load=OSKextLoadKextWithIdentifier("com.apple.driver.usb.AppleUSBXHCI");
    if(load!=KERN_SUCCESS){IOLog("NABU USB: XHCI family load=%x\n",load);return false;}
    void *handle=OSMetaClass::preModLoad(g_kernel_kmod_info.name);if(!handle)return false;
    nabu_usb_static_init();if(OSMetaClass::postModLoad(handle)!=kOSReturnSuccess)return false;
    nub=OSTypeAlloc(NabuXhciNub);
    auto *range=IODeviceMemory::withRange(0xa600000,0x8000);
    const OSObject *objects[]={range};
    auto *ranges=range?OSArray::withObjects(objects,1):nullptr;
    auto *zero=OSData::withBytes("\0\0\0\0",4);
    bool ok=nub&&ranges&&zero&&nub->init();
    if(ok){nub->setDeviceMemory(ranges);nub->setName("usb-host0");nub->setProperty("bus-number",zero);
        nub->setProperty("AAPL,clock-id",0ull,32);ok=nub->attach(IOService::getPlatform());}
    if(ok){nub->registerService();IOLog("NABU USB: NabuXhciNub published regs=0a600000 IRQ=165\n");host=new NabuUsbXhci;ok=host&&host->init()&&host->configureDmaCache()&&host->attach(nub);}
    if(ok){ok=host->start(nub);if(!ok)host->detach(nub);}
    OSSafeReleaseNULL(zero);OSSafeReleaseNULL(ranges);OSSafeReleaseNULL(range);
    if(!ok){OSSafeReleaseNULL(host);if(nub){nub->terminate(kIOServiceSynchronous);nub->release();nub=nullptr;}}
    IOLog("NABU USB: native AppleUSBXHCI start=%u IRQ=%u SID=%x\n",ok,irq,sid);return ok;
}
template<class Core> void reportFailure(const char *stage,const Core &core){
    for(unsigned attempt=0;attempt<2;++attempt){
        if(attempt)IOSleep(1000);
        IOLog("NABU USB: %s failed phase=%s block=%u off=%x value=%08x mask=%08x want=%08x\n",
            stage,core.lastPhase(),unsigned(core.failedBlock()),core.failedOffset(),core.failedValue(),core.failedMask(),core.failedExpected());
    }
}
void worker(void *,wait_result_t){
    if(!nabu::pmic().initialize()||!hardware.map()){IOLog("NABU USB: PMIC/map failed; worker stopped\n");return;}
    bool ok=hardware.rails();IOLog("NABU USB: stage=RPMh result=%u\n",ok);if(!ok)return;
    nabu::Usb2Core<Hardware> core(hardware);
    ok=core.clocksAndReset();
    IOLog("NABU USB: stage=clocks result=%u phase=%s\n",ok,core.lastPhase());
    // Delay one repeat on failure so early console contention cannot hide it.
    if(!ok){reportFailure("clocks",core);return;}
    IOLog("NABU USB: GDSC=%08x CFG_GDSC=%08x PLL=%08x vote=%08x\n",hardware.gcc.read(0xf004),hardware.gcc.read(0xf008),hardware.gcc.read(0),hardware.gcc.read(0x52000));
    IOLog("NABU USB: clocks MASTER=%08x CFG=%08x AGGR=%08x UTMI=%08x SLEEP=%08x\n",hardware.gcc.read(0xf010),hardware.gcc.read(0xf078),hardware.gcc.read(0xf07c),hardware.gcc.read(0xf018),hardware.gcc.read(0xf014));
    ok=core.femtoPhy(!(hardware.rcal.read(0)&0x1e00000));IOLog("NABU USB: stage=FEMTO result=%u CTRL=%08x POR=%08x\n",ok,hardware.phy.read(0x94),hardware.phy.read(0x50));if(!ok){reportFailure("FEMTO",core);return;}
    ok=core.selectUtmiHost();
    IOLog("NABU USB: IP GSNPSID=%08x revision=%08x phase=%s\n",core.coreId(),core.coreRevision(),core.lastPhase());
    IOLog("NABU USB: stage=UTMI-host result=%u SCRATCH=%08x GCTL=%08x USB2=%08x\n",ok,hardware.scratch.read(8),hardware.core.read(0xc110),hardware.core.read(0xc200));
    if(!ok){reportFailure("UTMI-host",core);return;}
    ok=core.resetXhci();IOLog("NABU USB: stage=HCRST result=%u\n",ok);if(!ok){reportFailure("HCRST",core);return;}
    ok=hardware.dma();IOLog("NABU USB: stage=SMMU result=%u\n",ok);if(!ok)return;
    nabu::UsbVbus<nabu::PmicBus> vbus(nabu::pmic());
    auto role=vbus.initializeRole();
    if(role!=nabu::InputError::Ok)IOLog("NABU USB: DRP setup error=%u; self-powered hub can supply VBUS\n",unsigned(role));
    if(!publishHost())return;
    nabu::UsbHostReportPolicy reports;uint64_t diagnosticMs=0;
    uint32_t previous=~0u;bool source=false;unsigned errorRounds=0;
    for(;;){
        drainTransfers();
        const auto hostState=core.snapshot();
        uint32_t usbFault=0;
        if(hardware.dmaReady)usbFault=nabu::usbContextFaults(hardware.smmu.read(nabu_usb::smmu::context_offset(hardware.dmaGlobalPages,hardware.dmaBank)+0x58));
        if(reports.sample(diagnosticMs,hostState,usbFault || (hostState.status&0x1004) ||
            (hostState.runtimeValid && (hostState.iman&3)==1)))reportRuntime(hostState);
        uint8_t cc=0,plugin=0,cmd=0,fault=0;auto e=vbus.status(cc,plugin,cmd,fault);
        bool external=!source&&vbus.externallyPowered(plugin);
        bool wanted=e==nabu::InputError::Ok&&nabu::UsbVbus<nabu::PmicBus>::wantSource(cc,plugin,source);
        if(wanted!=source){
            e=wanted?vbus.enable():vbus.disable();
            if(e==nabu::InputError::Ok){source=wanted;e=vbus.status(cc,plugin,cmd,fault);}
        }
        uint32_t snapshot=uint32_t(cc)|uint32_t(plugin)<<8|uint32_t(cmd)<<16|uint32_t(fault)<<24;
        if(snapshot!=previous || (e!=nabu::InputError::Ok && errorRounds++%60==0)){
            IOLog("NABU USB: CC=%02x USBIN=%02x OTG_CMD=%02x OTG_RT=%02x source=%u external5V=%u error=%u\n",cc,plugin,cmd,fault,source,external,unsigned(e));previous=snapshot;
        }
        const unsigned sleepMs=e==nabu::InputError::Ok?250:1000;
        IOSleep(sleepMs);diagnosticMs+=sleepMs;
    }
}
}
extern "C" bool nabu_usb_publish(IOService *platform){
    if(!platform)return false;thread_t t;
    if(kernel_thread_start(worker,nullptr,&t)!=KERN_SUCCESS)return false;thread_deallocate(t);return true;
}
