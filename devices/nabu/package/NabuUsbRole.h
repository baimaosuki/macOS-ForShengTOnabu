// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once
#include "NabuSpmiV5.h"
namespace nabu {
// SMB5 (PM8150B SID2). Never source VBUS into an attached upstream source.
template<class Pmic> class UsbVbus {
    Pmic &bus;
    bool owned=false;
public:
    explicit UsbVbus(Pmic &p):bus(p){}
    InputError status(uint8_t &typec,uint8_t &plugin,uint8_t &otg,uint8_t &fault){
        auto e=bus.read(2,0x150b,typec);if(e!=InputError::Ok)return e;
        e=bus.read(2,0x1310,plugin);if(e!=InputError::Ok)return e;
        e=bus.read(2,0x1140,otg);if(e!=InputError::Ok)return e;
        return bus.read(2,0x1110,fault);
    }
    InputError initializeRole(){
        // SMB5 software VBUS control; DRP (disable/sink-only/source-only clear).
        auto e=bus.update(2,0x1140,1,0);if(e!=InputError::Ok)return e;
        owned=false;return bus.update(2,0x1544,7,0);
    }
    static bool wantSource(uint8_t cc,uint8_t plugin,bool sourcing){
        return (cc&1)&&(cc&0x40)&&(sourcing||!(plugin&0x10));
    }
    bool externallyPowered(uint8_t plugin) const{return (plugin&0x10)!=0;}
    InputError enable(){
        uint8_t c=0,p=0,o=0,f=0;auto e=status(c,p,o,f);if(e!=InputError::Ok)return e;
        if(!(c&1) || !(c&0x40))return InputError::Busy;
        if(p&0x10)return InputError::Busy; // self-powered hub: no second VBUS source
        // Source mode from hardware CC detection; leave DRP/CC controller policy intact.
        e=bus.update(2,0x1153,2,0);if(e!=InputError::Ok)return e;
        e=bus.update(2,0x1140,1,1);if(e==InputError::Ok)owned=true;
        return e;
    }
    InputError disable(){
        if(!owned)return InputError::Ok;
        auto e=bus.update(2,0x1140,1,0);if(e==InputError::Ok)owned=false;return e;
    }
};
}
