// SPDX-License-Identifier: GPL-2.0-only
#include "OriginalUsbXhciABI.h"
#include "NabuInputHardware.h"
#include "NabuUsb2Core.h"
#include "NabuUsbDma.h"
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
void nabu_usb_static_init(void) __asm__("__GLOBAL__sub_I_NabuUsbHost.cpp");
}
namespace {
constexpr uint32_t irq=165,sid=0x140;
struct Hardware {
    IOBufferMemoryDescriptor *dmaTable=nullptr;
    nabu::RegisterWindow gcc,phy,scratch,core,smmu,rsc,dbPointer,db,rcal;
    nabu::RegisterWindow &region(nabu::UsbBlock b){
        switch(b){case nabu::UsbBlock::Gcc:return gcc;case nabu::UsbBlock::Phy:return phy;
        case nabu::UsbBlock::Scratch:return scratch;case nabu::UsbBlock::Core:return core;}
        __builtin_unreachable();
    }
    uint32_t read(nabu::UsbBlock b,uint32_t off){return region(b).read(off);}
    void write(nabu::UsbBlock b,uint32_t off,uint32_t v){region(b).write(off,v);}
    void delayUs(unsigned us){IODelay(us);}
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
        uint32_t cb=sm::context_offset(info.global_pages,plan.bank);
        // Disable only USB's exact stream; UFS and all other firmware mappings survive.
        smmu.write(sm::kSmrBase+4*plan.index,0);
        smmu.write(sm::kCba2rBase+4*plan.bank,1);
        smmu.write(sm::kCbarBase+4*plan.bank,(1u<<16)|(15u<<12)|(3u<<8));
        smmu.write(cb+0x10,1u|(7u<<15)); // 36-bit output, matching SM8150 DRAM
        smmu.write(cb+0x30,28u|(1u<<8)|(1u<<10)|(3u<<12)|(1u<<23));
        smmu.write(cb+0x20,uint32_t(pa));smmu.write(cb+0x24,uint32_t(pa>>32));
        smmu.write(cb+0x28,0);smmu.write(cb+0x2c,0);
        smmu.write(cb+0x38,0xff);smmu.write(cb+0x3c,0);
        smmu.write(cb+0x610,0);smmu.write(cb+0x7f0,0);
        if(!smmu.wait(cb+0x7f4,1,0,10000))return false;
        smmu.write(cb+0x58,smmu.read(cb+0x58));
        smmu.write(cb,1u|2u|4u|(1u<<5)|(1u<<12));
        if(!(smmu.read(cb)&1) || smmu.read(cb+0x20)!=uint32_t(pa) || smmu.read(cb+0x38)!=0xff)return false;
        smmu.write(sm::kS2crBase+4*plan.index,plan.bank);
        smmu.write(sm::kSmrBase+4*plan.index,sm::kSmrValid|sid);
        bool ok=smmu.read(sm::kSmrBase+4*plan.index)==(sm::kSmrValid|sid) && smmu.read(sm::kS2crBase+4*plan.index)==plan.bank;
        bool ufsPreserved=smmu.read(sm::kSmrBase+8)==ufsSmr && smmu.read(sm::kS2crBase+8)==ufsRoute;
        for(unsigned i=0;i<9;++i)ufsPreserved=ufsPreserved && smmu.read(ufsCb+ufsRegisters[i])==ufsBefore[i];
        IOLog("NABU USB: UFS slot2=%08x route=%08x CB1 preserved=%u USB slot=%u CB=%u\n",ufsSmr,ufsRoute,ufsPreserved,plan.index,plan.bank);
        if(!ufsPreserved)return false;
        IOLog("NABU USB: SID=%x slot=%u context=%u coherent-identity=%u table=%llx GFSR=%x\n",sid,plan.index,plan.bank,ok,pa,smmu.read(sm::kGfsr));
        return ok;
    }
} hardware;
} // namespace
class NabuXhciNub;
NabuXhciNub *active=nullptr;bool lineAttached=false;
class NabuXhciNub : public IOService {
    OSDeclareDefaultStructors(NabuXhciNub)
    IOInterruptAction action=nullptr;OSObject *target=nullptr;void *refcon=nullptr;bool enabled=false;
    static void primary(void *){auto *self=active;if(self&&self->enabled&&self->action)self->action(self->target,self->refcon,self,0);}
public:
    IOReturn getInterruptType(int source,int *type) override {
        if(source || !type)return kIOReturnNoInterrupt;*type=kIOInterruptTypeLevel;return kIOReturnSuccess;
    }
    IOReturn registerInterrupt(int source,OSObject *owner,IOInterruptAction handler,void *context) override {
        if(source || !handler)return kIOReturnNoInterrupt;if(action)return kIOReturnNoResources;
        target=owner;refcon=context;action=handler;enabled=false;active=this;
        asm volatile("dmb ish":::"memory");
        if(!lineAttached){
            auto r=armcpu_spi_attach(irq,false,primary,nullptr,nullptr,nullptr);
            if(r){action=nullptr;active=nullptr;return r;}lineAttached=true;
        }
        return armcpu_spi_mask(irq,true);
    }
    IOReturn unregisterInterrupt(int source) override {
        if(source)return kIOReturnNoInterrupt;enabled=false;auto r=armcpu_spi_mask(irq,true);
        action=nullptr;target=nullptr;refcon=nullptr;return r;
    }
    IOReturn enableInterrupt(int source) override {
        if(source||!action)return kIOReturnNoInterrupt;enabled=true;return armcpu_spi_mask(irq,false);
    }
    IOReturn disableInterrupt(int source) override {
        if(source)return kIOReturnNoInterrupt;enabled=false;return armcpu_spi_mask(irq,true);
    }
};
OSDefineMetaClassAndStructors(NabuXhciNub,IOService)
class NabuUsbXhci : public AppleUSBXHCI {
    OSDeclareDefaultStructors(NabuUsbXhci)
public:
    uint32_t desiredPower() override {return 3;}
    uint64_t regRead64(void *base,uint32_t off) override {
        auto *r=reinterpret_cast<volatile uint32_t *>(static_cast<uint8_t *>(base)+off);
        uint32_t lo=r[0];asm volatile("dmb oshld":::"memory");return uint64_t(r[1])<<32|lo;
    }
    void regWrite64(void *base,uint32_t off,uint64_t value) override {
        auto *r=reinterpret_cast<volatile uint32_t *>(static_cast<uint8_t *>(base)+off);
        r[1]=uint32_t(value>>32);asm volatile("dmb oshst":::"memory");r[0]=uint32_t(value);asm volatile("dsb sy":::"memory");
    }
};
OSDefineMetaClassAndStructors(NabuUsbXhci,AppleUSBXHCI)
namespace {
NabuUsbXhci *host=nullptr;NabuXhciNub *nub=nullptr;
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
    if(ok){nub->registerService();IOLog("NABU USB: NabuXhciNub published regs=0a600000 IRQ=165\n");host=new NabuUsbXhci;ok=host&&host->init()&&host->attach(nub);}
    if(ok){ok=host->start(nub);if(!ok)host->detach(nub);}
    OSSafeReleaseNULL(zero);OSSafeReleaseNULL(ranges);OSSafeReleaseNULL(range);
    if(!ok){OSSafeReleaseNULL(host);if(nub){nub->terminate(kIOServiceSynchronous);nub->release();nub=nullptr;}}
    IOLog("NABU USB: native AppleUSBXHCI start=%u IRQ=%u SID=%x\n",ok,irq,sid);return ok;
}
void worker(void *,wait_result_t){
    if(!nabu::pmic().initialize()||!hardware.map()){IOLog("NABU USB: PMIC/map failed; worker stopped\n");return;}
    bool ok=hardware.rails();IOLog("NABU USB: stage=RPMh result=%u\n",ok);if(!ok)return;
    nabu::Usb2Core<Hardware> core(hardware);
    ok=core.clocksAndReset();IOLog("NABU USB: stage=clocks result=%u GDSC=%08x MASTER=%08x UTMI=%08x\n",ok,hardware.gcc.read(0xf004),hardware.gcc.read(0xf010),hardware.gcc.read(0xf018));if(!ok)return;
    ok=core.femtoPhy(!(hardware.rcal.read(0)&0x1e00000));IOLog("NABU USB: stage=FEMTO result=%u CTRL=%08x POR=%08x\n",ok,hardware.phy.read(0x94),hardware.phy.read(0x50));if(!ok)return;
    ok=core.selectUtmiHost();IOLog("NABU USB: stage=UTMI-host result=%u SCRATCH=%08x GCTL=%08x USB2=%08x\n",ok,hardware.scratch.read(8),hardware.core.read(0xc110),hardware.core.read(0xc200));if(!ok)return;
    ok=core.resetXhci();IOLog("NABU USB: stage=HCRST result=%u\n",ok);if(!ok)return;
    ok=hardware.dma();IOLog("NABU USB: stage=SMMU result=%u\n",ok);if(!ok)return;
    nabu::UsbVbus<nabu::PmicBus> vbus(nabu::pmic());
    auto role=vbus.initializeRole();
    if(role!=nabu::InputError::Ok)IOLog("NABU USB: DRP setup error=%u; self-powered hub can supply VBUS\n",unsigned(role));
    if(!publishHost())return;
    uint32_t previous=~0u;bool source=false;unsigned errorRounds=0;
    for(;;){
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
        IOSleep(e==nabu::InputError::Ok?250:1000);
    }
}
}
extern "C" bool nabu_usb_publish(IOService *platform){
    if(!platform)return false;thread_t t;
    if(kernel_thread_start(worker,nullptr,&t)!=KERN_SUCCESS)return false;thread_deallocate(t);return true;
}
