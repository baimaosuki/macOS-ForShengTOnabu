// SPDX-License-Identifier: BSD-2-Clause-Patent
// SM8150 PMIC arbiter v5: vendor DTS and Linux spmi-pmic-arb.c.
#pragma once
#include <stdint.h>
namespace nabu {
enum class InputError { Ok, Geometry, NoMapping, Permission, Timeout, Transaction, Readback, Busy, Hardware };
struct SpmiMapping {unsigned apid=0;unsigned owner=0;bool valid=false;};
template<class Bus> class SpmiV5 {
    Bus &bus;
public:
    explicit SpmiV5(Bus &b):bus(b){}
    bool geometry() {
        const uint32_t version=bus.coreRead(0);
        return version>=0x50000000 && version<0x70000000 &&
            (bus.coreRead(4)&0x7ff)>0 && (bus.coreRead(4)&0x7ff)<=512;
    }
    SpmiMapping find(uint16_t ppid) {
        SpmiMapping found;
        if(!geometry())return found;
        const unsigned count=bus.coreRead(4)&0x7ff;
        for(unsigned i=0;i<count;++i) {
            uint32_t v=bus.coreRead(0x900+4*i);
            if(v && ((v>>8)&0xfff)==ppid) {
                const unsigned owner=bus.ownerRead(0x700+4*i)&7;
                if(!found.valid || owner==0)found={i,owner,true};
            }
        }
        return found;
    }
    InputError transaction(bool write,uint16_t address,uint8_t sid,uint8_t &value) {
        if(!geometry())return InputError::Geometry;
        SpmiMapping m=find(uint16_t(unsigned(sid)<<8 | address>>8));
        if(!m.valid)return InputError::NoMapping;
        if(write && m.owner!=0)return InputError::Permission;
        const uint32_t channel=write?0x10000*m.apid:0x80*m.apid;
        if(write)bus.writeChannel(true,channel+0x10,value);
        bus.writeChannel(write,channel,(write?0u:1u<<27)|((address&255u)<<4));
        for(unsigned i=0;i<1000;++i) {
            uint32_t status=bus.readChannel(write,channel+8);
            if(status&1) {
                if(status&0xe)return InputError::Transaction;
                if(!write)value=uint8_t(bus.readChannel(false,channel+0x18));
                return InputError::Ok;
            }
            bus.delayUs(1);
        }
        return InputError::Timeout;
    }
    InputError read(uint8_t sid,uint16_t address,uint8_t &value) {return transaction(false,address,sid,value);}
    InputError write(uint8_t sid,uint16_t address,uint8_t value) {return transaction(true,address,sid,value);}
    InputError update(uint8_t sid,uint16_t address,uint8_t mask,uint8_t value) {
        uint8_t old=0;auto e=read(sid,address,old);if(e!=InputError::Ok)return e;
        uint8_t next=uint8_t((old&~mask)|(value&mask));
        e=write(sid,address,next);if(e!=InputError::Ok)return e;
        uint8_t check=0;e=read(sid,address,check);if(e!=InputError::Ok)return e;
        return (check&mask)==(next&mask)?InputError::Ok:InputError::Readback;
    }
};
// PM8150 PON status bits differ from PMK8550/Gen3. No reset timer writes.
struct PowerKeyState {
    bool stable=false,candidate=false,longSent=false,initialized=false;
    uint64_t candidateAt=0,pressedAt=0;
    // Event bits: 1 press, 2 release, 4 short, 8 long. All times are monotonic ms.
    unsigned sample(bool pressed,uint64_t ms) {
        if(!initialized){initialized=true;candidate=pressed;candidateAt=ms;return 0;}
        if(pressed!=candidate){candidate=pressed;candidateAt=ms;}
        unsigned event=0;
        if(candidate!=stable && ms-candidateAt>=30) {
            stable=candidate;
            if(stable){pressedAt=ms;longSent=false;event|=1;}
            else {event|=2;if(!longSent)event|=4;}
        }
        if(stable && !longSent && ms-pressedAt>=2000){longSent=true;event|=8;}
        return event;
    }
};
}
