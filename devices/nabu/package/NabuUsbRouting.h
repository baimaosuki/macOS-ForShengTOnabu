// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once
#include "NabuUsbTransfer.h"

namespace nabu {
// One GIC SPI is exposed by this nub, so publish one usable interrupter to
// the native class even if a DWC3 revision implements additional banks.
inline uint32_t usbSingleInterrupterCapability(uint32_t hcsparams1) {
    return (hcsparams1&0x7ff00u)?(hcsparams1&~0x7ff00u)|0x100u:hcsparams1;
}
// 26C5035r emits IO target 1 (isochronous target 2), even when the
// non-MSI platform publishes one interrupter. nabu's single event ring is 0.
// Translate that native routing contract BEFORE publishing each doorbell.
// This changes only target bits, never invents a hardware completion.
class UsbSingleInterrupter {
public:
    struct Cursor {uint64_t context=0,address=0;uint32_t cycle=0,busy=0;};
    struct Result {bool ok=true;unsigned patched=0,visited=0,slot=0,endpoint=0;uint64_t failed=0;};
private:
    Cursor *cursors_=nullptr;unsigned slots_=0;
    uint64_t command_=0,dcbaa_=0;unsigned commandCycle_=1;
    static constexpr uint32_t targetMask=0xffc00000u;
    Cursor *cursor(unsigned slot,unsigned endpoint) {
        return cursors_ && slot && slot<=slots_ && endpoint && endpoint<32?&cursors_[slot*32+endpoint]:nullptr;
    }
    void clearSlot(unsigned slot) {
        for(unsigned endpoint=1;endpoint<32;++endpoint)
            if(auto *c=cursor(slot,endpoint)){c->context=0;c->address=0;c->cycle=0;}
    }
    template<class Io> bool context(Io &io,unsigned slot,uint64_t &output) {
        return dcbaa_ && io.read(dcbaa_+slot*8,&output,8) && output && !(output&63);
    }
    template<class Io> bool routeWord(Io &io,uint64_t address,uint32_t value,Result &result) {
        if(!(value&targetMask))return true;
        // The native gate owns these prepared ring/context buffers. Alter
        // exactly DWORD2, retaining transfer length, TD size and all flags.
        if(!io.store32(address,value&~targetMask)){result.ok=false;result.failed=address;return false;}
        ++result.patched;return true;
    }
    template<class Io> Result commands(Io &io) {
        Result result;
        for(unsigned n=0;n<4096 && command_;++n) {
            UsbTrb trb;
            if(!io.read(command_,&trb,16)){result.ok=false;result.failed=command_;return result;}
            if(trb.cycle()!=commandCycle_)return result;
            ++result.visited;
            const unsigned type=trb.type(),slot=trb.word[3]>>24,endpoint=(trb.word[3]>>16)&31;
            if(type==6) {
                const uint64_t next=trb.pointer()&~15ull;
                if(!next || next==command_){result.ok=false;result.failed=command_;return result;}
                if(trb.word[3]&2)commandCycle_^=1;
                command_=next;continue;
            }
            if(type==11 || type==12 || type==13) {
                const unsigned bytes=(io.reg(0x10)&4)?64:32;
                const uint64_t input=trb.pointer()&~15ull;
                uint32_t flags[2]{};
                if(!input || !io.read(input,flags,sizeof(flags))){result.ok=false;result.failed=input;return result;}
                // Only inspect a slot context actually included in this
                // command; Evaluate Context frequently updates EP0 alone.
                if(flags[1]&1) {
                    uint32_t word=0;
                    if(!io.read(input+bytes+8,&word,4)){result.ok=false;result.failed=input+bytes+8;return result;}
                    if(!routeWord(io,input+bytes+8,word,result))return result;
                }
                if(type==11 || type==12)
                    for(unsigned e=1;e<32;++e)if((flags[0]|flags[1])&(1u<<e))
                        if(auto *c=cursor(slot,e)){c->context=0;c->address=0;c->cycle=0;}
            } else if(type==10)clearSlot(slot);
            else if(type==16) {
                auto *c=cursor(slot,endpoint);uint64_t output=0;
                if(!c || !context(io,slot,output)){result.ok=false;result.failed=dcbaa_+slot*8;return result;}
                c->context=output;c->address=trb.pointer()&~15ull;c->cycle=trb.word[0]&1;
            }
            command_+=16;
        }
        // A malformed/cyclic chain must not spin indefinitely or publish a
        // partially translated request to the controller.
        if(command_){result.ok=false;result.failed=command_;}return result;
    }
    template<class Io> Result transfers(Io &io,unsigned slot,unsigned endpoint) {
        Result result;result.slot=slot;result.endpoint=endpoint;
        auto *c=cursor(slot,endpoint);uint64_t output=0;
        if(!c || !context(io,slot,output)){result.ok=false;result.failed=dcbaa_+slot*8;return result;}
        unsigned unlocked=0;
        if(!__atomic_compare_exchange_n(&c->busy,&unlocked,1,false,__ATOMIC_ACQUIRE,__ATOMIC_RELAXED)) {
            result.ok=false;return result;
        }
        if(c->context!=output || !c->address) {
            uint64_t dequeue=0;const unsigned bytes=(io.reg(0x10)&4)?64:32;
            uint32_t endpointFlags=0;
            if(!io.read(output+endpoint*bytes,&endpointFlags,4) || (endpointFlags&0x7c00) ||
               !io.read(output+endpoint*bytes+8,&dequeue,8) || !(dequeue&~15ull)) {
                result.ok=false;result.failed=output+endpoint*bytes;
                __atomic_store_n(&c->busy,0,__ATOMIC_RELEASE);return result;
            }
            c->context=output;c->address=dequeue&~15ull;c->cycle=dequeue&1;
        }
        for(unsigned n=0;n<4096;++n) {
            UsbTrb trb;
            if(!io.read(c->address,&trb,16)){result.ok=false;result.failed=c->address;break;}
            if(trb.cycle()!=c->cycle)break;
            ++result.visited;
            const unsigned type=trb.type();
            if(type<1 || type>8){result.ok=false;result.failed=c->address;break;}
            if(!routeWord(io,c->address+8,trb.word[2],result))break;
            if(type==6) {
                const uint64_t next=trb.pointer()&~15ull;
                if(!next || next==c->address){result.ok=false;result.failed=c->address;break;}
                if(trb.word[3]&2)c->cycle^=1;
                c->address=next;
            } else c->address+=16;
            if(n==4095){result.ok=false;result.failed=c->address;}
        }
        __atomic_store_n(&c->busy,0,__ATOMIC_RELEASE);return result;
    }
public:
    bool initialize(Cursor *storage,unsigned slots) {
        if(!storage || !slots || slots>255)return false;
        cursors_=storage;slots_=slots;reset();return true;
    }
    void reset() {
        command_=dcbaa_=0;commandCycle_=1;
        for(unsigned slot=1;slot<=slots_;++slot)clearSlot(slot);
    }
    template<class Io> void write64(Io &io,unsigned offset,uint64_t value) {
        const unsigned cap=io.reg(0)&255;
        if(cap<0x20 || cap>0x80)return;
        if(offset==cap+0x18 && !(value&6)){command_=value&~63ull;commandCycle_=value&1;}
        if(offset==cap+0x30)dcbaa_=value&~63ull;
    }
    template<class Io> Result beforeWrite32(Io &io,unsigned offset,uint32_t value) {
        const unsigned cap=io.reg(0)&255,doorbells=io.reg(0x14)&~3u;
        if(cap>=0x20 && cap<=0x80 && offset==cap && (value&2))reset();
        if(doorbells<0x20 || doorbells>=0xfc00 || offset<doorbells || offset>=doorbells+1024 || (offset&3))return {};
        const unsigned slot=(offset-doorbells)/4;
        if(slot>slots_ || (slot && (value>>16)))return {false,0,0,slot,value&255,0};
        return slot?transfers(io,slot,value&255):commands(io);
    }
};
}
