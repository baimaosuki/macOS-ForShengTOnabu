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
    uint32_t coreId_=0,coreRevision_=0;
    const char *phase_="idle";
    uint32_t failedOffset_=0,failedValue_=0,failedMask_=0,failedExpected_=0;
    UsbBlock failedBlock_=UsbBlock::Gcc;
    void phase(const char *name) { phase_=name;io.checkpoint(name); }
    bool failure(UsbBlock b,uint32_t off,uint32_t mask,uint32_t expected) {
        failedBlock_=b;failedOffset_=off;failedValue_=io.read(b,off);
        failedMask_=mask;failedExpected_=expected;return false;
    }
    bool update(UsbBlock b,uint32_t r,uint32_t mask,uint32_t value) {
        io.write(b,r,(io.read(b,r)&~mask)|(value&mask));
        return (io.read(b,r)&mask)==(value&mask) || failure(b,r,mask,value&mask);
    }
    bool wait(UsbBlock b,uint32_t r,uint32_t mask,uint32_t value,unsigned us) {
        for(unsigned t=0;t<us;t+=10){if((io.read(b,r)&mask)==value)return true;io.delayUs(10);}
        return (io.read(b,r)&mask)==value || failure(b,r,mask,value);
    }
    bool branch(uint32_t r) {
        for(unsigned us=0;us<100000;us+=10){
            if(clockBranchReady(io.read(UsbBlock::Gcc,r)))return true;
            io.delayUs(10);
        }
        return clockBranchReady(io.read(UsbBlock::Gcc,r)) || failure(UsbBlock::Gcc,r,1u<<31,0);
    }
    bool rcg(uint32_t r,uint32_t cfg) {
        if(!update(UsbBlock::Gcc,r+4,0x371f,cfg))return false;
        io.write(UsbBlock::Gcc,r,io.read(UsbBlock::Gcc,r)|1);
        return wait(UsbBlock::Gcc,r,1,0,20000);
    }
public:
    explicit Usb2Core(Io &i):io(i){}
    const char *lastPhase() const { return phase_; }
    uint32_t coreId() const { return coreId_; }
    uint32_t coreRevision() const { return coreRevision_; }
    UsbBlock failedBlock() const { return failedBlock_; }
    uint32_t failedOffset() const { return failedOffset_; }
    uint32_t failedValue() const { return failedValue_; }
    uint32_t failedMask() const { return failedMask_; }
    uint32_t failedExpected() const { return failedExpected_; }
    bool clocksAndReset() {
        using B=UsbBlock;
        phase("GDSC-config");
        // usb30_prim_gdsc uses POLL_CFG_GDSCR: +4 bit16, NOT GDSCR bit31.
        // Match gdsc_init: SW ownership, hardware sequencer, 2/8/2 XO waits.
        // Preserve every field outside this domain's sequencing controls.
        if(!update(B::Gcc,0xf004,0x00fff006,0x00282000))return false;
        phase("GDSC-power-up");
        if(!update(B::Gcc,0xf004,1,0) || !wait(B::Gcc,0xf008,1u<<16,1u<<16,100000))return false;
        phase("controller-reset");
        if(!update(B::Gcc,0xf000,1,1))return false;
        io.delayUs(100);
        if(!update(B::Gcc,0xf000,1,0))return false;
        io.delayUs(100);
        // Request the selected RCG parent before waiting on its child clocks.
        // OR only GPLL0's APPS vote; never reprogram/disable a PLL shared by UFS.
        phase("GPLL0-vote");
        if(!update(B::Gcc,0x52000,1,1))return false;
        const uint32_t mode=io.read(B::Gcc,0);
        if(mode&(1u<<20)) {
            if(!wait(B::Gcc,0,1u<<30,1u<<30,20000))return false;
        } else {
            // Firmware may leave GPLL0 in manual RUN mode. Accept only real
            // locked, enabled outputs; do not change shared PLL_MODE/OPMODE.
            if(!wait(B::Gcc,0,(1u<<31)|1,(1u<<31)|1,20000))return false;
        }
        phase("RCG-master");
        // GPLL0 600MHz / 3 = 200MHz; CXO 19.2MHz / 1 for Mock UTMI.
        if(!rcg(0xf01c,0x105))return false;
        phase("RCG-UTMI");
        if(!rcg(0xf034,1))return false;
        // Enable all prerequisites before polling NoC readiness. Previously the
        // CFG NoC halt loop ran while MASTER, UTMI and SLEEP were still gated.
        // Match the stock clock bulk order; writes preserve firmware fields.
        phase("branch-enable");
        const uint32_t branches[]={0xf010,0xf078,0xf07c,0xf018,0xf014};
        for(uint32_t off:branches)
            if(!update(B::Gcc,off,1,1))return false;
        phase("branch-ready");
        for(uint32_t off:branches)
            if(!branch(off))return false;
        phase("PHY-reset");
        if(!update(B::Gcc,0x12000,1,1))return false;
        io.delayUs(150);
        if(!update(B::Gcc,0x12000,1,0))return false;
        io.delayUs(150);
        return true;
    }
    bool femtoPhy(bool externalResistor=false) {
        using B=UsbBlock;
        phase("FEMTO-init");
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
        phase("UTMI-clock-select");
        // Missing PIPE clock otherwise stalls xHCI HCRST. Disable -> select -> enable.
        if(!update(B::Scratch,8,0x100,0x100))return false;
        io.delayUs(100);
        if(!update(B::Scratch,8,9,9))return false;
        io.delayUs(100);
        if(!update(B::Scratch,8,0x100,0))return false;
        phase("controller-id");
        coreId_=io.read(B::Core,0xc120);
        const uint32_t ip=coreId_>>16;
        // DWC_usb3 encodes its revision in GSNPSID; DWC_usb31 identifies as
        // 0x3331 and keeps the revision in VER_NUMBER (Linux dwc3_core_is_valid).
        // USB2-only describes link speed, not the DWC controller IP generation.
        if(ip==0x5533)coreRevision_=coreId_;
        else if(ip==0x3331){
            coreRevision_=io.read(B::Core,0xc1a0);
            // Published usb31 VER_NUMBER values encode decimal digits and '*'.
            if((coreRevision_&0xff)!=0x2a)
                return failure(B::Core,0xc1a0,0xff,0x2a);
        } else return failure(B::Core,0xc120,0xffff0000,0x33310000);
        // GCTL.CORESOFTRESET is a role-switch reset on DWC_usb3 and usb31
        // before 1.90a. Later usb31 cores synchronize through host HCRST.
        const bool roleReset=ip==0x5533 || coreRevision_<0x3139302a;
        phase("UTMI-PHY-reset");
        if(roleReset && !update(B::Core,0xc110,1u<<11,1u<<11))return false;
        if(!update(B::Core,0xc200,1u<<31,1u<<31) ||
           !update(B::Core,0xc2c0,1u<<31,1u<<31))return false;
        io.delayUs(100000);
        phase("UTMI-host-config");
        // 8-bit UTMI (PHYIF=0, ULPI_UTMI=0), turnaround=9, no SUSPHY/ENBLSLPM.
        const uint32_t mask=(1u<<31)|(15u<<10)|(1u<<8)|(1u<<6)|(1u<<4)|(1u<<3);
        if(!update(B::Core,0xc200,mask,9u<<10) ||
           !update(B::Core,0xc2c0,(1u<<31)|(1u<<17),1u<<17))return false;
        // Clear scaledown and select Host; retain all unrelated core settings.
        const uint32_t ctlMask=(3u<<12)|(3u<<4)|1u|(roleReset?1u<<11:0);
        if(!update(B::Core,0xc110,ctlMask,(1u<<12)|1u))return false;
        io.delayUs(100000);
        // No synthetic VBUS-valid: the host provides power through SMB5.
        return update(B::Scratch,0x10,(1u<<20)|(1u<<28),0);
    }
    bool resetXhci() {
        using B=UsbBlock;
        phase("XHCI-halt");
        const uint32_t cap=io.read(B::Core,0)&0xff;
        if(cap<0x20 || cap>0x80)return failure(B::Core,0,0xff,0x40);
        if(!update(B::Core,cap,1,0) || !wait(B::Core,cap+4,1,1,100000))return false;
        phase("XHCI-reset");
        io.write(B::Core,cap,io.read(B::Core,cap)|2);
        if(!wait(B::Core,cap,2,0,1000000))return false;
        phase("XHCI-ready");
        return wait(B::Core,cap+4,1u<<11,0,1000000);
    }
};
}
