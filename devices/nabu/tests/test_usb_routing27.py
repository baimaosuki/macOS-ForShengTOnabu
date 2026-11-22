"""Replay p046's real target-1 EP0 TRBs through production routing logic."""
from usb_runtime27 import compile_run


def test_single_interrupter_routes_actual_control_and_hid_rings(tmp_path):
    compile_run(tmp_path, 'usb-routing', r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <map>
#include <vector>
#include "NabuUsbRouting.h"
using namespace nabu;
struct Io {
 std::map<unsigned,unsigned> regs{{0,0x40},{4,0x02000108},{0x10,0},{0x14,0x2000}};
 std::map<uint64_t,uint8_t> memory;
 std::vector<uint64_t> writes;unsigned reads=0;bool rejectWrite=false;
 unsigned reg(unsigned off){return regs[off];}
 bool read(uint64_t a,void *p,unsigned n){++reads;for(unsigned i=0;i<n;++i)if(!memory.count(a+i))return false;
  for(unsigned i=0;i<n;++i)static_cast<uint8_t*>(p)[i]=memory[a+i];return true;}
 template<class T>void store(uint64_t a,const T &v){for(unsigned i=0;i<sizeof(v);++i)memory[a+i]=reinterpret_cast<const uint8_t*>(&v)[i];}
 bool store32(uint64_t a,uint32_t v){if(rejectWrite)return false;writes.push_back(a);store(a,v);return true;}
 void zero(uint64_t a,unsigned n){for(unsigned i=0;i<n;++i)memory[a+i]=0;}
 UsbTrb get(uint64_t a){UsbTrb t;assert(read(a,&t,16));return t;}
};
UsbTrb trb(uint64_t p,unsigned length,unsigned flags){return UsbTrb{{unsigned(p),unsigned(p>>32),length,flags}};}
int main(){
 std::vector<UsbSingleInterrupter::Cursor> storage(9*32);UsbSingleInterrupter route;Io io;
 assert(!route.initialize(nullptr,8) && !route.initialize(storage.data(),0));
 assert(route.initialize(storage.data(),8));
 // P046 actual Enable/Address/Stop/Evaluate/SetDequeue sequence. Match
 // high physical context PAs; command type and cycle never change.
 const uint64_t cmd=0xd5366000,dcbaa=0xd3d19000,output=0x170000000,input=0x165412000;
 const uint64_t ring=0xd3d1ac00;
 io.zero(cmd,128);io.zero(input,96);io.zero(output,1024);io.zero(ring,128);
 io.store(dcbaa+8,output);
 io.store(input+4,uint32_t(3));io.store(input+32+8,uint32_t(0x00431234));
 io.store(output+32+8,ring|1);
 io.store(cmd,trb(0,0,0x2401));io.store(cmd+16,trb(input,0,0x01002c01));
 io.store(cmd+32,trb(ring|1,0,0x01014001));
 route.write64(io,0x58,cmd|1);route.write64(io,0x70,dcbaa);
 auto result=route.beforeWrite32(io,0x2000,0);
 assert(result.ok && result.patched==1 && result.visited==3);
 uint32_t slotWord=0;assert(io.read(input+32+8,&slotWord,4) && slotWord==0x31234);
 // Exactly the 3 real TRBs from seq88/89/91. Target1 used to select an
 // absent event ring; correction must preserve every other bit and buffer.
 UsbTrb setup{{0x01000680,0x00080000,0x00400008,0x00030841}};
 UsbTrb data{{0x6c209000,1,0x00400008,0x00010c05}};
 UsbTrb status{{0,0,0x00400000,0x00001021}};
 io.store(ring,setup);io.store(ring+16,data);io.store(ring+32,status);
 result=route.beforeWrite32(io,0x2004,1);
 assert(result.ok && result.slot==1 && result.endpoint==1 && result.patched==3 && result.visited==3);
 const UsbTrb originals[]={setup,data,status};
 for(unsigned i=0;i<3;++i){auto t=io.get(ring+i*16);assert(t.word[2]==(originals[i].word[2]&0x003fffff));
  assert(t.word[0]==originals[i].word[0] && t.word[1]==originals[i].word[1] && t.word[3]==originals[i].word[3]);}
 assert(io.get(ring+16).pointer()==0x16c209000ull);
 // Repeated doorbells must neither rewrite hardware-owned TRBs nor start
 // again at the stale output dequeue pointer. Only new producer TRBs change.
 io.writes.clear();result=route.beforeWrite32(io,0x2004,1);assert(result.ok && !result.visited && io.writes.empty());
 io.store(ring+48,trb(0x180000000,0x00400004,(1u<<10)|(1u<<5)|1));
 result=route.beforeWrite32(io,0x2004,1);assert(result.ok && result.patched==1 && io.writes[0]==ring+48+8);
 // HID interrupt IN endpoint3 on slot1; 64-byte contexts work independently.
 io.regs[0x10]=4;const uint64_t hid=0x180001000;
 io.store(output+3*64+8,hid|1);io.zero(hid,64);
 io.store(hid,trb(0x180002000,0x00800008,(1u<<10)|(1u<<5)|1));
 result=route.beforeWrite32(io,0x2004,3);assert(result.ok && result.endpoint==3 && result.patched==1);
 assert(io.get(hid).word[2]==8 && io.get(hid).word[3]==((1u<<10)|(1u<<5)|1));
 // Multiple segments and a toggle-cycle Link: preserve length/TD-size,
 // follow the link, route Event Data, and stop at the inverse cycle.
 const uint64_t a=0x190000000,b=0x190001000;
 io.store(output+5*64+8,a|1);io.zero(a,32);io.zero(b,32);
 io.store(a,trb(b,0x00400000,(6u<<10)|3));
 io.store(b,trb(0xdeadbeef00000000ull,0x00800321,(7u<<10)|(1u<<5)));
 io.store(b+16,trb(0,0,1));
 result=route.beforeWrite32(io,0x2004,5);assert(result.ok && result.patched==2 && io.get(b).word[2]==0x321);
 assert(io.get(b).pointer()==0xdeadbeef00000000ull); // opaque cookie never dereferenced.
 // Set Dequeue restarts the producer cursor after abort/recovery, even when
 // the slot's output context retains the same address.
 io.store(cmd+48,trb(hid|1,0,(1u<<24)|(3u<<16)|(16u<<10)|1));
 result=route.beforeWrite32(io,0x2000,0);assert(result.ok);
 io.store(hid,trb(0x180002000,0x00400008,(1u<<10)|(1u<<5)|1));
 result=route.beforeWrite32(io,0x2004,3);assert(result.ok && result.patched==1);
 // Malformed links, inaccessible memory, concurrent producer and write
 // failures are bounded errors, never fabricated success or DMA to ring1.
 io.store(output+7*64+8,uint64_t(0x1a0000001));io.zero(0x1a0000000,16);
 io.store(0x1a0000000,trb(0x1a0000000,0,(6u<<10)|1));
 assert(!route.beforeWrite32(io,0x2004,7).ok);
 io.store(output+9*64+8,uint64_t(0x1b0000001));assert(!route.beforeWrite32(io,0x2004,9).ok);
 io.store(output+11*64+8,uint64_t(0x1c0000001));io.zero(0x1c0000000,32);
 io.store(0x1c0000000,trb(0x1234,0x400004,(1u<<10)|1));io.rejectWrite=true;
 assert(!route.beforeWrite32(io,0x2004,11).ok);io.rejectWrite=false;
 assert(!route.beforeWrite32(io,0x2004,0x10001).ok); // no USB2 stream contexts.
 // HCRST forgets old context/command cursors; routing is re-established
 // from newly written CRCR/DCBAA, not old freed objects.
 route.beforeWrite32(io,0x40,2);assert(!route.beforeWrite32(io,0x2004,3).ok);
 puts("PASS p046 real target1 control TRBs -> ring0; slot-context routing, HID IN,64-byte contexts, high PA, links/cycles, producer cursor, SetDequeue/reset and failure bounds");
}
''')
