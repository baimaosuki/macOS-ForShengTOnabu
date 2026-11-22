"""Exercise real capture/decoding with simulated hardware, not USB success claims."""
from pathlib import Path
from usb_runtime27 import compile_run


def test_usb_transfer_event_and_abort_evidence(tmp_path):
    compile_run(tmp_path, 'usb-transfer', r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <map>
#include <vector>
#include "NabuUsbTransfer.h"
#include "NabuUsbDma.h"
using namespace nabu;
struct Io {
 std::map<unsigned,unsigned> registers{{0,0x40},{0x14,0x2000},{0x18,0x1000},{0x1028,2}};
 std::map<uint64_t,uint8_t> memory;
 unsigned reads=0,invalid=0;bool recursive=false;UsbTransferTrace *trace=nullptr;
 unsigned reg(unsigned o){return registers[o];}unsigned status(){return reg(0x44);}
 unsigned cache(uint64_t){return 6;}
 bool read(uint64_t a,void *p,unsigned n){
  ++reads;
  if(recursive){recursive=false;trace->status(*this,0);}
  for(unsigned i=0;i<n;++i)if(!memory.count(a+i)){++invalid;return false;}
  for(unsigned i=0;i<n;++i)static_cast<uint8_t*>(p)[i]=memory[a+i];return true;
 }
 template<class T>void store(uint64_t a,const T &v){
  for(unsigned i=0;i<sizeof(v);++i)memory[a+i]=reinterpret_cast<const uint8_t*>(&v)[i];
 }
 void zero(uint64_t a,unsigned n){for(unsigned i=0;i<n;++i)memory[a+i]=0;}
};
UsbTrb trb(uint64_t p,unsigned status,unsigned control){UsbTrb t{{unsigned(p),unsigned(p>>32),status,control}};return t;}
std::vector<UsbTraceRecord> drain(UsbTransferTrace &t){
 std::vector<UsbTraceRecord> r;UsbTraceRecord a;while(t.take(a))r.push_back(a);return r;
}
int main(){
 assert(usbDmaMair==0x44 && usbCpuDmaCache==6);
 UsbTransferTrace trace;Io io;io.trace=&trace;
 // Command cursor advances from the software CRCR, not read-only CRCR=8.
 io.zero(0x100000,64);
 io.store(0x100000,trb(0,0,(9u<<10)|1));io.store(0x100010,trb(0,0,(11u<<10)|1));
 trace.write64(io,0x58,0x100001);trace.write32(io,0x2000,0);
 auto records=drain(trace);assert(records.size()==2 && records[0].trb.type()==9 && records[1].trb.type()==11);
 assert(records[0].address==0x100000 && records[1].address==0x100010);
 // EP0: setup -> DATA IN -> status. Record actual bytes before ownership.
 const uint64_t context=0x300000,dequeue=0x400001;
 io.store(0x200008,context);io.store(context+32+8,dequeue);
 io.store(0x400000,trb(0x0012010006000680ull,8,(2u<<10)|(1u<<6)|1));
 io.store(0x400010,trb(0x500000,18,(3u<<10)|(1u<<16)|1));
 io.store(0x400020,trb(0,0,(4u<<10)|1));io.zero(0x500000,18);
 trace.write64(io,0x70,0x200000);trace.write32(io,0x2004,1);
 records=drain(trace);bool before=false;
 for(auto &r:records)if(r.kind==UsbTraceKind::DataBefore){before=true;assert(r.valid==1 && r.bytes[0]==0 && r.related==18 && r.cache==6);}
 assert(before);
 // Two event segments. A short-packet completion references the real DATA
 // TRB; data bytes are captured again after hardware writes the descriptor.
 struct Segment {uint64_t start;unsigned count,reserved;};
 io.store(0x600000,Segment{0x700000,4,0});io.store(0x600010,Segment{0x710000,4,0});
 // Match the original 26C5035r initRegisters order, not a mock-only order.
 trace.write64(io,0x1038,0x700008);trace.write64(io,0x1030,0x600000);
 io.store(0x700000,trb(0x400010,(13u<<24)|10,(32u<<10)|(1u<<16)|(1u<<24)|1));
 const uint32_t descriptor=0x02000112;io.store(0x500000,descriptor);
 trace.write64(io,0x1038,0x700018);
 records=drain(trace);assert(records.size()==2);
 assert(records[0].kind==UsbTraceKind::Event && records[0].trb.word[2]>>24==13);
 assert(records[0].related==0x400010 && records[0].valid==3 && records[0].bytes[3]==((3u<<10)|(1u<<16)|1));
 assert(records[1].kind==UsbTraceKind::DataAfter && records[1].bytes[0]==descriptor && records[1].valid);
 // ED cookies must NEVER be dereferenced as physical TRB addresses.
 io.store(0x700010,trb(0xdeadbeef00000000ull,1u<<24,(32u<<10)|4|1));
 unsigned invalid=io.invalid;trace.write64(io,0x1038,0x700028);
 records=drain(trace);assert(records.size()==1 && records[0].related==0 && records[0].valid==1 && io.invalid==invalid);
 // Segment transition and complete ring wrap preserve consumer cycle.
 io.store(0x700020,trb(0,1u<<24,(34u<<10)|1));io.store(0x700030,trb(0,1u<<24,(39u<<10)|1));
 trace.write64(io,0x1038,0x710008);assert(drain(trace).size()==1); // MFINDEX wrap doesn't spend capture budget.
 for(unsigned n=0;n<4;++n)io.store(0x710000+n*16,trb(0,1u<<24,(34u<<10)|1));
 trace.write64(io,0x1038,0x700008);assert(drain(trace).size()==4);
 io.store(0x700000,trb(0,1u<<24,34u<<10));
 trace.write64(io,0x1038,0x700018);records=drain(trace);assert(records.size()==1 && records[0].trb.cycle()==0);
 // Distinguish pre-abort STS from the first observed HCE. HCH is already
 // set in 0x1019; it is not evidence that hardware failed to halt.
 io.registers[0x44]=0x18;trace.write64(io,0x58,0x100004);
 io.registers[0x44]=0x1019;trace.status(io,0x1019);trace.status(io,0x1019);
 records=drain(trace);unsigned aborts=0,hces=0;
 for(auto &r:records){if(r.kind==UsbTraceKind::Abort){++aborts;assert(r.status==0x18);}if(r.kind==UsbTraceKind::Hce){++hces;assert(r.status==0x1019);}}
 assert(aborts==1 && hces==1);
 for(auto &r:records)assert(r.kind!=UsbTraceKind::DataAfter); // completed/freed PA was forgotten.
 // A recursion/IRQ while the diagnostic lock is held is dropped, never
 // spun on. Missing physical memory and cyclic Link TRBs remain bounded.
 trace.write64(io,0x58,0x800001);io.recursive=true;trace.write32(io,0x2000,0);
 assert(trace.dropped()==1);records=drain(trace);assert(records.back().kind==UsbTraceKind::Unreadable);
 io.store(0x800000,trb(0x800040,0,(6u<<10)|1));io.store(0x800040,trb(0x800000,0,(6u<<10)|1));
 unsigned reads=io.reads;trace.write32(io,0x2000,0);assert(io.reads-reads<=16);drain(trace);
 // Reset discards old pointers. Queue pressure never waits or overflows.
 trace.write32(io,0x40,2);drain(trace);reads=io.reads;trace.write32(io,0x2000,0);assert(io.reads==reads);
 for(unsigned n=0;n<1000;++n)trace.write32(io,0x40,0);
 records=drain(trace);assert(records.size()==64 && trace.dropped()>1);
 puts("PASS passive actual command/EP0/DATA-IN/event capture, ED safety, multi-segment wrap, abort/HCE order, bounded no-fault reads and nonblocking overflow");
}
''')


def test_usb_physmap_reader_rejects_faults_and_splits_apertures(tmp_path):
    source = (Path(__file__).resolve().parents[1] / 'package/NabuUsbHost.cpp').read_text()
    reader = source[source.index('struct UsbTraceIo {'):source.index('bool usbRegisterOffset(')]
    compile_run(tmp_path, 'usb-trace-physmap', r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <algorithm>
#include "NabuUsbDma.h"
using vm_offset_t=uintptr_t;using vm_map_address_t=uintptr_t;using vm_size_t=uintptr_t;
constexpr unsigned PAGE_SHIFT=12,PAGE_SIZE=4096,PAGE_MASK=4095;
uint64_t gPhysBase=0x80000000,gPhysSize=8192;
uint8_t first[4096],second[4096];
bool valid=true,shortCopy=false,zeroSpan=false;unsigned translations=0,copies=0,cache=6;
static int pmap_valid_page(unsigned pn){return valid && pn>=gPhysBase/4096 && pn<(gPhysBase+gPhysSize)/4096;}
static unsigned pmap_cache_attributes(unsigned){return cache;}
static vm_map_address_t phystokv_range(uint64_t pa,vm_size_t *span){
 assert(pa>=gPhysBase && pa-gPhysBase<gPhysSize);++translations;
 unsigned offset=unsigned(pa-gPhysBase);unsigned pageOffset=offset&4095;
 // Deliberately discontinuous virtual apertures, even within one page.
 *span=zeroSpan?0:std::min(*span,vm_size_t(2048-(offset&2047)));
 return reinterpret_cast<vm_map_address_t>((offset<4096?first:second)+pageOffset);
}
static vm_size_t ml_nofault_copy(vm_offset_t src,vm_offset_t dst,vm_size_t n){
 ++copies;if(shortCopy)return 0;
 std::memcpy(reinterpret_cast<void*>(dst),reinterpret_cast<void*>(src),n);return n;
}
struct Hardware{struct Core{uint32_t read(unsigned){return 0;}}core;}hardware;
''' + reader + r'''
int main(){
 for(unsigned i=0;i<4096;++i){first[i]=i%251;second[i]=(i+17)%251;}
 UsbTraceIo io;uint8_t sample[128]{};
 assert(io.read(gPhysBase+2040,sample,32) && translations==2 && copies==2);
 for(unsigned i=0;i<32;++i)assert(sample[i]==first[2040+i]);
 translations=copies=0;
 assert(io.read(gPhysBase+4090,sample,64) && translations==2 && copies==2);
 for(unsigned i=0;i<64;++i)assert(sample[i]==(i<6?first[4090+i]:second[i-6]));
 translations=0;
 assert(!io.read(0,sample,16) && !io.read(UINT64_MAX-7,sample,16));
 assert(!io.read(gPhysBase+8190,sample,16) && !io.read(gPhysBase,sample,129));
 assert(!translations);valid=false;assert(!io.read(gPhysBase,sample,16) && !translations);
 valid=true;zeroSpan=true;copies=0;assert(!io.read(gPhysBase,sample,16) && !copies);
 zeroSpan=false;shortCopy=true;assert(!io.read(gPhysBase,sample,16));
 shortCopy=false;assert(io.store32(gPhysBase+8,0x12345678));
 uint32_t word=0;assert(io.read(gPhysBase+8,&word,4) && word==0x12345678);
 assert(!io.store32(gPhysBase+9,1) && !io.store32(0,1));
 cache=2;assert(!io.store32(gPhysBase+8,0));cache=6;
 puts("PASS production no-fault reader: fragmented physmap/page splits, managed-page guard, overflow/size rejection and partial-copy failure");
}
''')
