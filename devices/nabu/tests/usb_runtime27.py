"""A640-era nabu USB register order, level IRQ handoff and read-only fault capture."""
from pathlib import Path
import json
import shutil
import subprocess

DEVICE=Path(__file__).resolve().parents[1]

def compile_run(tmp_path,name,text):
    cpp=tmp_path/(name+'.cpp');exe=tmp_path/name;cpp.write_text(text)
    subprocess.run([shutil.which('clang++') or 'c++','-std=c++17','-O1','-g','-pthread',
        '-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-Wno-unused-parameter',
        '-fsanitize=address,undefined','-fno-sanitize-recover=all',
        '-I',str(DEVICE/'package'),str(cpp),'-o',str(exe)],check=True)
    result=subprocess.run([str(exe)],check=True,capture_output=True,text=True,timeout=10)
    (tmp_path/(name+'.log')).write_text(result.stdout+result.stderr)
    return result.stdout

def check_mmio_snapshot_and_clocks(tmp_path):
    code=r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <map>
#include <vector>
#include "NabuUsbDma.h"
#include "NabuUsb2Core.h"
struct Pair {
 std::vector<unsigned> events;uint32_t low=0,high=0;bool committed=false;
 uint32_t read32(uint32_t o){events.push_back(o);return o==0x18?low:high;}
 void write32(uint32_t o,uint32_t v){events.push_back(o);if(o==0x18)low=v;else {assert(o==0x1c);high=v;committed=true;}}
 void readBarrier(){events.push_back(1);}void writeBarrier(){events.push_back(2);}void complete(){assert(committed);events.push_back(3);}
};
struct Bus {
 std::map<std::pair<nabu::UsbBlock,uint32_t>,uint32_t> registers;
 std::vector<uint32_t> reads;unsigned writes=0;bool clockStuck=false,utmiDisabled=false;
 uint32_t read(nabu::UsbBlock b,uint32_t o){assert(o<0x10000 && !(o&3));reads.push_back(o);return registers[{b,o}];}
 void write(nabu::UsbBlock b,uint32_t o,uint32_t v){++writes;if(b==nabu::UsbBlock::Scratch && o==8 && (v&0x100))utmiDisabled=true;if(!(clockStuck && b==nabu::UsbBlock::Scratch && o==0x28))registers[{b,o}]=v;}
 void delayUs(unsigned){}void checkpoint(const char*){}
 void core(uint32_t o,uint32_t v){registers[{nabu::UsbBlock::Core,o}]=v;}
};
int main(){
 Pair pair;nabu::usbWriteRegister64(pair,0x18,0x12345678abcdef01ull);
 assert(pair.events==std::vector<unsigned>({2,0x18,2,0x1c,3}));
 pair.events.clear();assert(nabu::usbReadRegister64(pair,0x18)==0x12345678abcdef01ull);
 assert(pair.events==std::vector<unsigned>({0x18,1,0x1c,1}));
 struct Register32 {
  std::vector<unsigned> events;uint32_t value=0;
  void writeBarrier(){events.push_back(1);}void write32(unsigned,uint32_t v){events.push_back(2);value=v;}
  void complete(){events.push_back(3);}uint32_t read32(unsigned){events.push_back(4);return value;}
  void readBarrier(){events.push_back(5);}
 } single;
 nabu::usbWriteRegister32(single,0,0x40d);assert(single.events==std::vector<unsigned>({1,2,3}));
 assert(nabu::usbReadRegister32(single,0)==0x40d && single.events==std::vector<unsigned>({1,2,3,4,5}));
 for(uint32_t mode=0;mode<16;++mode) {
  const uint32_t original=0x12345003|(mode<<8),nc=nabu::usbBufferOptions(original);
  assert((nc&0xf00)==0x400 && (nc&~0xf00u)==(original&~0xf00u));
 }
 assert(!nabu::usbContextFaults(0x400) && nabu::usbContextFaults(2)==2);
 assert(nabu::usbContextFaults(0x80000480)==0x80000080);
 struct Fields {
  uint32_t words[nabu::UsbControllerCachePolicy27::instanceBytes/4]{};bool reject=false;
  uint32_t read(unsigned off){return words[off/4];}
  void write(unsigned off,uint32_t value){if(!reject)words[off/4]=value;}
 } fields;
 for(auto &word:fields.words)word=0xa5a5a5a5;
 fields.words[0x5ac/4]=0x10003;fields.words[0x5b0/4]=0x300;
 nabu::UsbControllerCachePolicy27 native;assert(native.install(fields));
 assert(native.oldDma==0x10003 && native.dma==0x10403 && native.client==0x400);
 for(unsigned off=0;off<sizeof(fields.words);off+=4)
  if(off!=0x5ac && off!=0x5b0)assert(fields.words[off/4]==0xa5a5a5a5);
 fields.reject=true;fields.words[0x5ac/4]=0x10003;assert(!native.install(fields));
 nabu::UsbPowerReportPolicy power;
 for(unsigned n=1;n<=130;++n)assert(power.sample(1,0xe00002e9,0,0x100d)==(n<=3 || n%64==0));
 assert(power.sample(1,0xe00002e9,0,0x18));assert(!power.sample(9,0,0,0));
 // Keep proven USB SMMU WBWA; CPU buffer coherency is a separate policy.
 const uint64_t pte=nabu::usbIdentityBlock(3);
 assert(((pte>>2)&7)==0 && ((pte>>8)&3)==3 && (pte&(1u<<10)));
 assert(nabu::usbDmaMair==0xff);
 Bus bus;bus.core(0,0x40);bus.core(4,2u<<24);bus.core(0x18,0x1000);
 bus.core(0x40,0);bus.core(0x44,0);bus.core(0x458,0xdeadbeef);
 bus.core(0x440,0x0e000663);bus.core(0x450,0x200);bus.core(0x1020,3);
 bus.core(0x1028,1);bus.core(0x1038,0xabc00008);bus.core(0x103c,1);
 nabu::Usb2Core<Bus> core(bus);auto s=core.snapshot();
 assert(s.valid && s.runtimeValid && s.haltPending() && s.iman==3);
 assert(s.port[0]==0x0e000663 && ((s.port[0]>>5)&15)==3 && ((s.port[0]>>10)&15)==1);
 assert(s.erdpHi==1 && s.erdpLo==0xabc00008 && !bus.writes);
 bus.core(0x44,1);assert(!core.snapshot().haltPending());
 bus.core(0x18,0xffffffe0);s=core.snapshot();assert(s.valid && !s.runtimeValid && !bus.writes);
 bus.core(0,0xff);assert(!core.snapshot().valid);
 nabu::UsbHostReportPolicy reports;s.valid=true;s.command=1;s.status=0;
 assert(reports.sample(0,s,false));s.port[0]^=0x60;
 assert(!reports.sample(250,s,false) && reports.sample(1000,s,false));
 assert(!reports.sample(2000,s,false) && !reports.sample(9999,s,true));
 assert(reports.sample(11000,s,true));
 Bus clocks;clocks.core(0xc120,0x33310000);clocks.core(0xc1a0,0x3137302a);
 clocks.registers[{nabu::UsbBlock::Scratch,0x28}]=0xa5000001;
 nabu::Usb2Core<Bus> boot(clocks);assert(boot.selectUtmiHost());
 assert(clocks.read(nabu::UsbBlock::Scratch,0x28)==0xa5000019);
 assert(!clocks.utmiDisabled && (clocks.read(nabu::UsbBlock::Scratch,8)&0x109)==9);
 Bus stuck;stuck.clockStuck=true;nabu::Usb2Core<Bus> failed(stuck);
 assert(!failed.selectUtmiHost() && failed.failedOffset()==0x28 && failed.failedMask()==0x18);
 puts("PASS low/high MMIO ordering, read-only bounded snapshots, U3/halt decoding, rate limits and stock RAM clocks");
}
'''
    assert 'PASS' in compile_run(tmp_path,'usb-registers',code)

IRQ_PREFIX=r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <atomic>
#include <thread>
using IOReturn=int;enum{kIOReturnSuccess=0,kIOReturnNoInterrupt=1,kIOReturnNoResources=2};
enum{kIOInterruptTypeLevel=1};
struct OSObject{};class IOService;
static thread_local bool interruptContext=false;
struct Platform {bool atInterruptLevel(){return interruptContext;}};
[[maybe_unused]] static void IODelay(unsigned){std::this_thread::yield();}
using IOInterruptAction=void(*)(OSObject*,void*,IOService*,int);
class IOService:public OSObject {
public:
 static Platform *getPlatform(){static Platform platform;return &platform;}
 virtual IOReturn getInterruptType(int,int*){return 0;}
 virtual IOReturn registerInterrupt(int,OSObject*,IOInterruptAction,void*){return 0;}
 virtual IOReturn unregisterInterrupt(int){return 0;}
 virtual IOReturn enableInterrupt(int){return 0;}
 virtual IOReturn disableInterrupt(int){return 0;}
};
#define OSDeclareDefaultStructors(T)
[[maybe_unused]] static void IOLog(const char*,...){}
constexpr uint32_t irq=165;
static void(*callback)(void*)=nullptr;static bool masked=true,pending=true,storm=false,failUnmask=false;
static unsigned handlers=0,maskCalls=0,unmaskCalls=0;
static void deliver(){interruptContext=true;callback(nullptr);interruptContext=false;}
static int armcpu_spi_mask(uint32_t n,bool mask){assert(n==165);if(mask)++maskCalls;else ++unmaskCalls;
 if(!mask && failUnmask)return 7;masked=mask;return 0;}
static int armcpu_spi_attach(uint32_t n,bool edge,void(*fn)(void*),void*,uint32_t *config,uint64_t *route){
 assert(n==165 && !edge);callback=fn;masked=false;
 // Model an asserted GIC level line arriving BEFORE attach returns.
 for(unsigned i=0;i<8 && !masked && pending;++i)deliver();
 if(!masked && pending){storm=true;return 8;}
 if(config)*config=0xa001;if(route)*route=0;return 0;
}
'''
IRQ_TAIL=r'''
static bool filterAccept=true;
static std::atomic<bool> holdPrimary{false},entered{false},finish{false};
static void action(OSObject*,void*,IOService *source,int){++handlers;
 if(holdPrimary.load()){entered.store(true);while(!finish.load())std::this_thread::yield();}
 if(filterAccept)source->disableInterrupt(0); // exact XNU level/filter ownership.
 else pending=false;
}
int main(){
 NabuXhciNub nub;OSObject owner;const auto status=nub.registerInterrupt(0,&owner,action,nullptr);
#if !REPAIRED
 assert(storm && status==8 && !handlers);
 puts("PASS old registration's asserted level interrupt window reproduced");return 0;
#else
 assert(!status && !storm && masked && !handlers && usbIrqs.inactive==1);
 assert(!nub.enableInterrupt(0) && !masked);deliver();
 assert(handlers==1 && masked && usbIrqs.forwarded==1);
 unsigned unmaskBefore=unmaskCalls;deliver();
 assert(handlers==1 && masked && unmaskCalls==unmaskBefore);
 assert(!nub.enableInterrupt(0));filterAccept=false;deliver();
 assert(handlers==2 && !masked); // rejecting filter must not be force-masked.
 failUnmask=true;assert(nub.enableInterrupt(0)==7);deliver();
 assert(handlers==2 && masked && usbIrqs.errors==1);
 assert(!nub.unregisterInterrupt(0));deliver();assert(handlers==2 && masked);
 failUnmask=false;filterAccept=true;holdPrimary=true;
 assert(!nub.registerInterrupt(0,&owner,action,nullptr) && !nub.enableInterrupt(0));
 std::thread primaryThread([]{deliver();});while(!entered.load())std::this_thread::yield();
 std::atomic<bool> unregistered{false};
 std::thread teardown([&]{assert(!nub.unregisterInterrupt(0));unregistered=true;});
 while(__atomic_load_n(&active,__ATOMIC_SEQ_CST)!=nullptr)std::this_thread::yield();
 assert(!unregistered.load());finish=true;primaryThread.join();teardown.join();
 assert(unregistered && !usbIrqs.inFlight && handlers==3 && masked);
 puts("PASS actual nub: registration IRQ window, level filter ownership, no eager re-enable, mask errors and concurrent unregister quiescence");
#endif
}
'''

def nub_code(text):
    start=text.index('class NabuXhciNub;')
    end=text.index('OSDefineMetaClassAndStructors(NabuXhciNub',start)
    return text[start:end].replace('asm volatile("dmb ish":::"memory");',
                                  'std::atomic_thread_fence(std::memory_order_seq_cst);')

def check_actual_level_irq_nub_and_old_window(tmp_path):
    current=(DEVICE/'package/NabuUsbHost.cpp').read_text()
    compile_run(tmp_path,'usb-irq-new','#define REPAIRED 1\n'+IRQ_PREFIX+nub_code(current)+IRQ_TAIL)
    old=subprocess.check_output(['git','show','9488928:devices/nabu/package/NabuUsbHost.cpp'],cwd=DEVICE,text=True)
    compile_run(tmp_path,'usb-irq-old','#define REPAIRED 0\n'+IRQ_PREFIX+nub_code(old)+IRQ_TAIL)

def check_original_abi_and_diagnostic_claims():
    abi=json.loads((DEVICE/'scripts/usb-xhci-26C5035r.json').read_text())
    assert abi['slots'][292]['symbols']==['__ZNK22AppleUSBHostController17defaultPowerStateEv']
    assert 'regRead64' in abi['slots'][419]['symbols'][0] and 'regWrite64' in abi['slots'][421]['symbols'][0]
    source=(DEVICE/'package/NabuUsbHost.cpp').read_text()
    assert 'coherent-identity=' not in source and 'coherence=unverified' in source
    primary=source[source.index('static void primary('):source.index('public:',source.index('static void primary('))]
    assert 'IOLog(' not in primary and 'IOSleep(' not in primary

def check_actual_dma_allocation_and_failure_paths(tmp_path):
    source=(DEVICE/'package/NabuUsbHost.cpp').read_text()
    start=source.index('class NabuUsbXhci :')
    klass=source[start:source.index('OSDefineMetaClassAndStructors(NabuUsbXhci',start)]
    prefix=r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include "NabuUsbDma.h"
using IOReturn=int;using IOOptionBits=uint32_t;
constexpr int kIOReturnSuccess=0;constexpr unsigned PAGE_SHIFT=12;
#define OSDeclareDefaultStructors(T)
static void IOLog(const char*,...){}
static unsigned pmap_cache_attributes(unsigned){return 6;}
struct IOService{};struct OSObject{};
struct IOBufferMemoryDescriptor {
 uint64_t getPhysicalSegment(unsigned,uint64_t *span,unsigned){*span=4096;return 0xc0000000;}
} buffer;
struct IODMACommand {
 struct Segment64{uint64_t address,length;};
 using SegmentFunction=bool(*)(IODMACommand*,Segment64,void*,uint32_t);
 enum MappingOptions{kMapped=0,kNonCoherent=2};
 static bool OutputHost64(IODMACommand*,Segment64,void*,uint32_t){return true;}
} command;
#define kIODMACommandOutputHost64 IODMACommand::OutputHost64
static bool failBuffer=false,failCommand=false;static uint32_t allocationOptions=0;static uint64_t allocationSize=0;
struct AppleUSBXHCI:IOService {
 unsigned char nativePrivate[0xf88-sizeof(void*)]{};
 virtual uint32_t desiredPower(){return 0;}
 virtual uint32_t getBufferOptions(uint32_t o)const{return o|0x200;}
 virtual IOBufferMemoryDescriptor *createIOBuffer(uint32_t o,uint64_t n){allocationOptions=o;allocationSize=n;return failBuffer?nullptr:&buffer;}
 virtual IODMACommand *getDMACommand(uint32_t,uint32_t){assert(false);return nullptr;}
 virtual int lowerOnePowerStateTo(unsigned long){return 7;}
 virtual uint32_t regRead32(void*,uint32_t){return 0;}
 virtual uint64_t regRead64(void*,uint32_t){return 0;}
 virtual void regWrite32(void*,uint32_t,uint32_t){}
 virtual void regWrite64(void*,uint32_t,uint64_t){}
};
struct UsbRegisterIo {
 volatile uint32_t *words;uint32_t read32(unsigned o){return words[o/4];}
 void write32(unsigned o,uint32_t v){words[o/4]=v;}void readBarrier(){}void writeBarrier(){}void complete(){}
};
static bool nabu_usb_dma_aligned_mapper(OSObject*,IODMACommand::Segment64,uint32_t,bool){return true;}
static unsigned factoryCalls=0;
static IODMACommand *nabu_usb_dma_with_service(IODMACommand::SegmentFunction out,
 bool(*mapper)(OSObject*,IODMACommand::Segment64,uint32_t,bool),IOService *service,IODMACommand::MappingOptions options,void *ref) {
 assert(out==kIODMACommandOutputHost64 && mapper==nabu_usb_dma_aligned_mapper && service && !ref);
 assert(options==IODMACommand::kNonCoherent);++factoryCalls;return failCommand?nullptr:&command;
}
struct Hardware{} hardware;
namespace nabu {
struct UsbHostSnapshot{uint32_t command=0x40d,status=0;};
template<class H>struct Usb2Core{explicit Usb2Core(H&){}UsbHostSnapshot snapshot(){return {};}};
}
static unsigned faultReports=0;
static void reportRuntime(const nabu::UsbHostSnapshot&){++faultReports;}
'''
    tail=r'''
int main(){
 NabuUsbXhci host;
 auto *raw=reinterpret_cast<uint8_t*>(&host);uint32_t value=0x10003;
 __builtin_memcpy(raw+0x5ac,&value,4);value=0x300;__builtin_memcpy(raw+0x5b0,&value,4);
 assert(host.configureDmaCache());__builtin_memcpy(&value,raw+0x5ac,4);assert(value==0x10403);
 __builtin_memcpy(&value,raw+0x5b0,4);assert(value==0x400);
 for(unsigned cache=0;cache<16;++cache){
  uint32_t options=0x80000003|(cache<<8);
  assert(host.getBufferOptions(options)==((options&~0xf00u)|0x400));
  assert(host.createIOBuffer(options,8192)==&buffer);
  assert(allocationSize==8192 && allocationOptions==((options&~0xf00u)|0x400));
 }
 for(unsigned i=0;i<8;++i)assert(host.getDMACommand(i,3)==&command);
 assert(factoryCalls==8);failCommand=true;assert(!host.getDMACommand(0,0));
 failBuffer=true;assert(!host.createIOBuffer(0,4096));
 assert(host.lowerOnePowerStateTo(1)==7 && faultReports==1);
 puts("PASS production allocation: replace cache modes, preserve flags/size, native noncoherent factory, failure propagation and halt diagnostics");
}
'''
    compile_run(tmp_path,'usb-dma-allocation',prefix+klass+tail)
