// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once
#include <stdint.h>

namespace nabu {
struct UsbTrb {
    uint32_t word[4]{};
    uint64_t pointer() const {return uint64_t(word[1])<<32|word[0];}
    unsigned type() const {return (word[3]>>10)&63;}
    unsigned cycle() const {return word[3]&1;}
};
static_assert(sizeof(UsbTrb)==16,"xHCI TRB");
enum class UsbTraceKind : uint32_t {Command,Transfer,Event,DataBefore,DataAfter,Abort,Power,Hce,Unreadable};
struct UsbTraceRecord {
    uint32_t sequence=0;UsbTraceKind kind{};
    uint64_t address=0,related=0;
    UsbTrb trb{};
    uint32_t bytes[4]{},status=0,cache=~0u,tag=0,valid=0;
};

// Passive, bounded capture at the real native MMIO boundary. No allocation,
// waiting, cache maintenance, register acknowledgement or logging in an IRQ.
// Producers and the worker use a try-lock: diagnostics must never block USB.
class UsbTransferTrace {
    static constexpr unsigned queueSize=64,maxSegments=8;
    UsbTraceRecord queue_[queueSize]{};
    unsigned lock_=0,head_=0,tail_=0,sequence_=0,ordinary_=0,critical_=0,dropped_=0;
    uint64_t command_=0,dcbaa_=0,erst_=0,event_=0,lastDequeue_=0;
    unsigned commandCycle_=1,eventCycle_=1;
    bool hce_=false;
    struct Pending {uint64_t address=0;uint32_t length=0,slot=0;} pending_[8]{};
    bool enter() {
        unsigned expected=0;
        if(__atomic_compare_exchange_n(&lock_,&expected,1,false,__ATOMIC_ACQUIRE,__ATOMIC_RELAXED))return true;
        __atomic_fetch_add(&dropped_,1,__ATOMIC_RELAXED);return false;
    }
    void leave(){__atomic_store_n(&lock_,0,__ATOMIC_RELEASE);}
    void append(UsbTraceRecord record,bool critical=false) {
        if(critical ? critical_>=32 : ordinary_>=512)return;
        if(head_-tail_==queueSize){__atomic_fetch_add(&dropped_,1,__ATOMIC_RELAXED);return;}
        if(critical)++critical_;else ++ordinary_;
        record.sequence=++sequence_;queue_[head_++%queueSize]=record;
    }
    template<class Io> void data(Io &io,const Pending &p,UsbTraceKind kind,uint32_t status,bool critical=false) {
        if(!p.address || !p.length)return;
        UsbTraceRecord record;record.kind=kind;record.address=p.address;record.tag=p.slot;
        record.related=p.length;record.status=status;record.cache=io.cache(p.address);
        const unsigned length=p.length<sizeof(record.bytes)?p.length:sizeof(record.bytes);
        record.valid=io.read(p.address,record.bytes,length);append(record,critical);
    }
    template<class Io> void unreadable(Io &io,uint64_t address,unsigned tag) {
        UsbTraceRecord record;record.kind=UsbTraceKind::Unreadable;record.address=address;
        record.tag=tag;record.status=io.status();append(record);
    }
    template<class Io> void commands(Io &io) {
        // The first CRCR write supplies the software cursor. CRCR reads do
        // not supply the current dequeue pointer while the ring is running.
        for(unsigned i=0;i<16 && command_;++i) {
            UsbTrb trb;
            if(!io.read(command_,&trb,sizeof(trb))){unreadable(io,command_,0);return;}
            if(trb.cycle()!=commandCycle_)return;
            UsbTraceRecord record;record.kind=UsbTraceKind::Command;record.address=command_;
            record.trb=trb;record.cache=io.cache(command_);record.status=io.status();record.valid=1;append(record);
            if(trb.type()==6) {
                if(trb.word[3]&2)commandCycle_^=1;
                const uint64_t next=trb.pointer()&~15ull;
                if(!next || next==command_)return;
                command_=next;
            } else command_+=16;
        }
    }
    template<class Io> void transfer(Io &io,unsigned slot,unsigned endpoint) {
        if(!dcbaa_ || !slot || slot>255 || endpoint!=1)return;
        uint64_t context=0,dequeue=0;
        const unsigned contextBytes=(io.reg(0x10)&4)?64:32;
        if(!io.read(dcbaa_+slot*8,&context,8) || !context || (context&63) ||
           !io.read(context+contextBytes+8,&dequeue,8)) {unreadable(io,dcbaa_+slot*8,slot);return;}
        uint64_t cursor=dequeue&~15ull;unsigned cycle=dequeue&1;
        Pending *pending=nullptr;
        for(auto &p:pending_)if(p.slot==slot){pending=&p;break;}
        if(!pending)for(auto &p:pending_)if(!p.slot){pending=&p;p.slot=slot;break;}
        if(pending){pending->address=0;pending->length=0;}
        for(unsigned i=0;i<12 && cursor;++i) {
            UsbTrb trb;
            if(!io.read(cursor,&trb,sizeof(trb))){unreadable(io,cursor,slot);return;}
            if(trb.cycle()!=cycle)return;
            UsbTraceRecord record;record.kind=UsbTraceKind::Transfer;record.address=cursor;
            record.tag=(slot<<8)|endpoint;record.trb=trb;record.status=io.status();
            record.cache=io.cache(cursor);record.valid=1;append(record);
            if(trb.type()==6) {
                if(trb.word[3]&2)cycle^=1;
                const uint64_t next=trb.pointer()&~15ull;
                if(!next || next==cursor)return;
                cursor=next;continue;
            }
            // Only a DATA IN stage refers to descriptor bytes. Setup/IDT
            // and Event Data parameters are not DMA buffer addresses.
            if(pending && trb.type()==3 && (trb.word[3]&(1u<<16)) && !(trb.word[3]&(1u<<6))) {
                pending->address=trb.pointer();pending->length=trb.word[2]&0x1ffff;
                data(io,*pending,UsbTraceKind::DataBefore,io.status());
            }
            cursor+=16;if(trb.type()==4 || trb.type()==7)return;
        }
    }
    struct Segment {uint64_t start;uint32_t count,reserved;};
    template<class Io> void events(Io &io,uint64_t next) {
        next&=~15ull;
        const unsigned runtime=io.reg(0x18)&~31u;
        if(runtime<0x20 || runtime>0xffc0)return;
        const unsigned count=io.reg(runtime+0x28)&0xffff;
        if(!erst_ || !next || !count || count>maxSegments)return;
        Segment segments[maxSegments]{};unsigned segment=maxSegments,target=maxSegments;
        for(unsigned i=0;i<count;++i) {
            if(!io.read(erst_+i*16,&segments[i],16))return;
            const auto &s=segments[i];
            if(!s.start || (s.start&63) || !s.count || s.count>4096 || s.start+uint64_t(s.count)*16<s.start)return;
            if(event_>=s.start && event_-s.start<uint64_t(s.count)*16)segment=i;
            if(next>=s.start && next-s.start<uint64_t(s.count)*16)target=i;
        }
        if(target==maxSegments)return;
        if(!event_){event_=next;eventCycle_=1;return;}
        if(segment==maxSegments){event_=next;return;}
        for(unsigned n=0;n<64 && event_!=next;++n) {
            UsbTrb trb;
            if(!io.read(event_,&trb,sizeof(trb))){unreadable(io,event_,0);return;}
            if(trb.cycle()!=eventCycle_)return;
            UsbTraceRecord record;record.kind=UsbTraceKind::Event;record.address=event_;
            record.trb=trb;record.status=io.status();record.cache=io.cache(event_);record.valid=1;
            // An ED=1 Transfer Event contains an opaque cookie, not a TRB PA.
            if((trb.type()==32 && !(trb.word[3]&4)) || trb.type()==33) {
                const uint64_t source=trb.pointer()&~15ull;record.related=source;
                if(source)record.valid|=unsigned(io.read(source,record.bytes,16))<<1;
            }
            append(record);
            if(trb.type()==32 && ((trb.word[3]>>16)&31)==1)
                for(auto &p:pending_)if(p.slot==trb.word[3]>>24) {
                    data(io,p,UsbTraceKind::DataAfter,record.status);
                    // This observer does not retain the descriptor. Never
                    // sample its old PA again after native completion frees it.
                    p.address=0;p.length=0;
                }
            event_+=16;
            if(event_==segments[segment].start+uint64_t(segments[segment].count)*16) {
                if(++segment==count){segment=0;eventCycle_^=1;}
                event_=segments[segment].start;
            }
        }
        // On overflow do not invent completion codes from stale ring cells.
        if(event_!=next){event_=next;__atomic_fetch_add(&dropped_,1,__ATOMIC_RELAXED);}
    }
public:
    template<class Io> void write32(Io &io,unsigned off,uint32_t value) {
        if(!enter())return;
        const unsigned cap=io.reg(0)&255,doorbells=io.reg(0x14)&~3u;
        if(cap>=0x20 && cap<=0x80 && off==cap) {
            UsbTraceRecord record;record.kind=UsbTraceKind::Power;record.tag=value;record.status=io.status();append(record);
            if(!(value&1))for(auto &p:pending_)p={};
            if(value&2) {command_=dcbaa_=erst_=event_=lastDequeue_=0;hce_=false;for(auto &p:pending_)p={};}
        }
        if(doorbells>=0x20 && doorbells<0xfc00 && off>=doorbells && off<doorbells+1024 && !(off&3) && ordinary_<512) {
            const unsigned slot=(off-doorbells)/4;
            if(!slot)commands(io);else transfer(io,slot,value&255);
        }
        leave();
    }
    template<class Io> void write64(Io &io,unsigned off,uint64_t value) {
        if(!enter())return;
        const unsigned cap=io.reg(0)&255,runtime=io.reg(0x18)&~31u;
        if(cap>=0x20 && cap<=0x80) {
            if(off==cap+0x18) {
                if(value&6) {
                    UsbTraceRecord record;record.kind=UsbTraceKind::Abort;record.address=value;
                    record.status=io.status();append(record,true);
                    for(const auto &p:pending_)data(io,p,UsbTraceKind::DataAfter,record.status,true);
                } else {command_=value&~63ull;commandCycle_=value&1;}
            } else if(off==cap+0x30)dcbaa_=value&~63ull;
        }
        if(runtime>=0x20 && runtime<=0xffc0) {
            // Original 26C5035r initRegisters writes ERDP BEFORE ERSTBA.
            // Retain that initial cursor, or the first completion is lost.
            if(off==runtime+0x30){erst_=value&~63ull;event_=lastDequeue_;eventCycle_=1;}
            if(off==runtime+0x38) {
                if(ordinary_<512)events(io,value);
                lastDequeue_=value&~15ull;
            }
        }
        leave();
    }
    template<class Io> void status(Io &io,uint32_t value) {
        if(!enter())return;
        if((value&0x1000) && !hce_) {
            UsbTraceRecord record;record.kind=UsbTraceKind::Hce;record.status=value;
            record.address=io.reg((io.reg(0)&255));append(record,true);
            for(const auto &p:pending_)data(io,p,UsbTraceKind::DataAfter,value,true);
        }
        hce_=(value&0x1000)!=0;leave();
    }
    bool take(UsbTraceRecord &record) {
        if(!enter())return false;
        const bool available=tail_!=head_;
        if(available)record=queue_[tail_++%queueSize];
        leave();return available;
    }
    unsigned dropped() const {return __atomic_load_n(&dropped_,__ATOMIC_RELAXED);}
};
}
