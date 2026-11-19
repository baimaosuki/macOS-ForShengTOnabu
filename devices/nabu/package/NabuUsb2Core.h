// SPDX-License-Identifier: GPL-2.0-only
// SM8150 GCC + Synopsys FEMTO v2 + DWC3 USB2 glue.
// Sequences derived from Linux gcc-sm8150, phy-msm-snps-hs and dwc3-qcom.
#pragma once
#include <stdint.h>
#include "NabuClockBranch.h"
namespace nabu {
enum class UsbBlock { Gcc, Phy, Scratch, Core };
template<class Io> class Usb2Core {
    Io &io;
    bool update(UsbBlock b,uint32_t r,uint32_t mask,uint32_t value) {
        io.write(b,r,(io.read(b,r)&~mask)|(value&mask));
        return (io.read(b,r)&mask)==(value&mask);
    }
    bool wait(UsbBlock b,uint32_t r,uint32_t mask,uint32_t value,unsigned us) {
        for(unsigned t=0;t<us;t+=10){if((io.read(b,r)&mask)==value)return true;io.delayUs(10);}
        return (io.read(b,r)&mask)==value;
    }
    bool branch(uint32_t r) {
        if(!update(UsbBlock::Gcc,r,1,1))return false;
        for(unsigned us=0;us<100000;us+=10){
            if(clockBranchReady(io.read(UsbBlock::Gcc,r)))return true;
            io.delayUs(10);
        }
        return clockBranchReady(io.read(UsbBlock::Gcc,r));
    }
    bool rcg(uint32_t r,uint32_t cfg) {
        if(!update(UsbBlock::Gcc,r+4,0x371f,cfg))return false;
        io.write(UsbBlock::Gcc,r,io.read(UsbBlock::Gcc,r)|1);
        return wait(UsbBlock::Gcc,r,1,0,20000);
    }
public:
    explicit Usb2Core(Io &i):io(i){}
    bool clocksAndReset() {
        // GDSC USB30_PRIM, then core reset. Preserve unrelated clock fields.
        if(!update(UsbBlock::Gcc,0xf004,1,0) || !wait(UsbBlock::Gcc,0xf004,1u<<31,1u<<31,100000))return false;
        if(!update(UsbBlock::Gcc,0xf000,1,1))return false;
        io.delayUs(100);
        // DWC3-QCOM releases the controller BCR BEFORE enabling its clocks.
        // Waiting for the NoC branch while this reset is asserted never completes.
        if(!update(UsbBlock::Gcc,0xf000,1,0))return false;
        io.delayUs(100);
        // GPLL0 600MHz / 3 = 200MHz; CXO 19.2MHz / 1 for Mock UTMI.
        if(!rcg(0xf01c,0x105) || !rcg(0xf034,1))return false;
        const uint32_t branches[]={0xf078,0xf010,0xf07c,0xf014,0xf018};
        for(uint32_t off:branches)
            if(!branch(off))return false;
        if(!update(UsbBlock::Gcc,0x12000,1,1))return false;
        io.delayUs(150);
        if(!update(UsbBlock::Gcc,0x12000,1,0))return false;
        io.delayUs(150);
        return true;
    }
    bool femtoPhy(bool externalResistor=false) {
        using B=UsbBlock;
        if(!update(B::Phy,0x94,2,2) || !update(B::Phy,0x50,2,2) ||
           !update(B::Phy,0x54,0x70,0) || !update(B::Phy,0x58,0x20,0x20) ||
           !update(B::Phy,0xa0,3,2) || !update(B::Phy,0x58,0x10,0x10) ||
           !update(B::Phy,0x60,1,1))return false;
        // nabu's qcom,param-override-seq: value/offset pairs, CSOT board tuning.
        if(!update(B::Phy,0x70,0xff,4) || !update(B::Phy,0x74,0xff,0x2c) ||
           !update(B::Phy,0x5c,1,1) || !update(B::Phy,0x64,0xc,0xc) ||
           !update(B::Phy,0x3c,1,1) || !update(B::Phy,0x54,4,0))return false;
        if(externalResistor && !update(B::Phy,0xb4,1,1))return false;
        io.delayUs(100);
        if(!update(B::Phy,0x50,2,0) || !update(B::Phy,0x64,8,0) || !update(B::Phy,0x94,2,0))return false;
        io.delayUs(100);
        return true;
    }
    bool selectUtmiHost() {
        using B=UsbBlock;
        // Missing PIPE clock otherwise stalls xHCI HCRST. Disable -> select -> enable.
        if(!update(B::Scratch,8,0x100,0x100))return false;
        io.delayUs(100);
        if(!update(B::Scratch,8,9,9))return false;
        io.delayUs(100);
        if(!update(B::Scratch,8,0x100,0))return false;
        uint32_t id=io.read(B::Core,0xc120);
        if((id&0xffff0000)!=0x55330000)return false;
        // Core and PHY soft-reset while changing PRTCAPDIR; USB3 stays suspended.
        if(!update(B::Core,0xc110,1u<<11,1u<<11) ||
           !update(B::Core,0xc200,1u<<31,1u<<31) || !update(B::Core,0xc2c0,1u<<31,1u<<31))return false;
        io.delayUs(100000);
        // 8-bit UTMI (PHYIF=0), turnaround=9, no SUSPHY/ENBLSLPM on SM8150.
        const uint32_t mask=(1u<<31)|(15u<<10)|(1u<<8)|(1u<<6)|(1u<<3);
        if(!update(B::Core,0xc200,mask,9u<<10) ||
           !update(B::Core,0xc2c0,(1u<<31)|(1u<<17),1u<<17) ||
           !update(B::Core,0xc110,(3u<<12)|(1u<<11)|1u,(1u<<12)|1u))return false;
        io.delayUs(100000);
        // No synthetic VBUS-valid: the host provides power through SMB5.
        return update(B::Scratch,0x10,(1u<<20)|(1u<<28),0);
    }
    bool resetXhci() {
        using B=UsbBlock;
        const uint32_t cap=io.read(B::Core,0)&0xff;
        if(cap<0x20 || cap>0x80)return false;
        if(!update(B::Core,cap,1,0) || !wait(B::Core,cap+4,1,1,100000))return false;
        io.write(B::Core,cap,io.read(B::Core,cap)|2);
        return wait(B::Core,cap,2,0,1000000) && wait(B::Core,cap+4,1u<<11,0,1000000);
    }
};
}
