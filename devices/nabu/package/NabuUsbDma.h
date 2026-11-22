// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once
#include "NabuUsbSmmu.h"
namespace nabu {
// Keep the device-proven WBWA/shareable USB SMMU output attributes. Normal-NC
// output regressed controller startup (p044: TF at IOVA0 and USBSTS.HSE).
// CPU buffer cache policy and DMA ownership synchronization are independent.
constexpr uint32_t usbDmaMair=0xff;
// Owned rings and transfer buffers must use Normal-NC on this non-snooping
// USB master. Do not combine a requested cache mode with the caller's mode.
inline uint32_t usbBufferOptions(uint32_t options) {
    return (options&~0xf00u)|0x400u; // kIOMapCacheMask / kIOMapWriteCombineCache
}
// Qualified against the original 26C5035r controller initializer and real
// DMABuffer/ClientBuffer allocation call sites, not the unused convenience
// createIOBuffer/getDMACommand methods. Only init writes these fields; set
// them once, after native init and before start allocates any DMA buffers.
struct UsbControllerCachePolicy27 {
    static constexpr unsigned instanceBytes=0xf88;
    static constexpr unsigned dmaOptionsOffset=0x5ac,clientOptionsOffset=0x5b0;
    uint32_t oldDma=0,oldClient=0,dma=0,client=0;
    template<class Fields> bool install(Fields &fields) {
        oldDma=fields.read(dmaOptionsOffset);oldClient=fields.read(clientOptionsOffset);
        dma=usbBufferOptions(oldDma);client=usbBufferOptions(oldClient);
        fields.write(dmaOptionsOffset,dma);fields.write(clientOptionsOffset,client);
        return fields.read(dmaOptionsOffset)==dma && fields.read(clientOptionsOffset)==client;
    }
};
// Bound recovery log volume without hiding new CMD/STS error signatures.
class UsbPowerReportPolicy {
    uint32_t result_[4]{},command_[4]{},status_[4]{},repeats_[4]{};
public:
    bool sample(unsigned state,uint32_t result,uint32_t command,uint32_t status) {
        if(state>=4)return false;
        if(!repeats_[state] || result_[state]!=result || command_[state]!=command || status_[state]!=status) {
            result_[state]=result;command_[state]=command;status_[state]=status;
            repeats_[state]=1;return true;
        }
        const unsigned n=++repeats_[state];return n<=3 || !(n%64);
    }
};
// SMMUv2 FSR[10] is RES0, not an architectural fault indication. Retain the
// raw register in diagnostics, but only real faults should trigger reporting.
inline uint32_t usbContextFaults(uint32_t fsr) {return fsr&0xc00001feu;}
// The USB controller is halted before changing its exact SID mapping. Never
// reuse a firmware/shared bypass context. WB/shareable SMMU attributes alone
// do not prove the USB master can snoop CPU caches.
template<class Read> nabu_usb::smmu::Plan usbIdentityPlan(Read read,const nabu_usb::smmu::Info &info,uint32_t sid){
    namespace sm=nabu_usb::smmu;
    if(!info.supported || info.exids || (sid&~sm::stream_id_mask(info)))return sm::plan_error("unsupported-geometry");
    unsigned slot=info.num_smr,matches=0;
    for(unsigned i=0;i<info.num_smr;++i){
        uint32_t v=read(sm::kSmrBase+4*i),mask=(v>>16)&0x7fff;
        if(!(v&sm::kSmrValid)){if(i!=2 && slot==info.num_smr)slot=i;continue;}
        if(!((sid^v)&sm::stream_id_mask(info)&~mask)){
            if(mask || ++matches>1)return sm::plan_error("shared-or-ambiguous-stream");
            if(i==2)return sm::plan_error("ufs-reserved-slot");
            slot=i;
        }
    }
    if(slot==info.num_smr)return sm::plan_error("no-free-stream-group");
    for(unsigned b=info.num_cb-1;b>1;--b){
        bool used=false;
        for(unsigned i=0;i<info.num_smr;++i){
            uint32_t v=read(sm::kSmrBase+4*i),s=read(sm::kS2crBase+4*i);
            if((v&sm::kSmrValid) && !(s&sm::kS2crTypeMask) && (s&255)==b)used=true;
        }
        if(used || b<info.num_s2_cb)continue;
        const uint32_t control=read(sm::context_offset(info.global_pages,b));
        const uint32_t cbar=read(sm::kCbarBase+4*b),cba2r=read(sm::kCba2rBase+4*b);
        // NUMS2CB excludes stage-2-only banks. For the remaining S1 banks,
        // ownership is determined by live stream routes and SCTLR.M, not by
        // reset values of CBAR/CBA2R or unrelated SCTLR bits. Disabled banks
        // may retain firmware state (or architecturally UNKNOWN reset bits).
        // The halted controller programs the complete context and checks it
        // before enabling the exact USB SID. Never take an enabled/used bank.
        if(control&sm::kSctlrM)continue;
        sm::Plan plan={};plan.valid=true;plan.index=slot;plan.bank=b;plan.global_pages=info.global_pages;
        plan.sctlr=control;plan.cbar=cbar;plan.cba2r=cba2r;return plan;
    }
    return sm::plan_error("no-free-context-bank");
}
inline uint64_t usbIdentityBlock(unsigned i){return i>=2&&i<8 ? uint64_t(i)<<30|1ull<<10|3ull<<8|1ull<<6|1ull : 0;}

// nabu-r-oss drivers/usb/host/xhci.h: low DWORD first, high DWORD second.
// The barriers also order preceding CPU stores before publishing a pointer.
template<class Io> uint64_t usbReadRegister64(Io &io,uint32_t offset) {
    const uint32_t low=io.read32(offset);io.readBarrier();
    const uint32_t high=io.read32(offset+4);io.readBarrier();
    return uint64_t(high)<<32|low;
}
template<class Io> uint32_t usbReadRegister32(Io &io,uint32_t offset) {
    const uint32_t value=io.read32(offset);io.readBarrier();return value;
}
template<class Io> void usbWriteRegister32(Io &io,uint32_t offset,uint32_t value) {
    // Publish Normal-NC TRBs/data before the doorbell, and finish control
    // register writes before subsequent polling or power-state transitions.
    io.writeBarrier();io.write32(offset,value);io.complete();
}
template<class Io> void usbWriteRegister64(Io &io,uint32_t offset,uint64_t value) {
    io.writeBarrier();io.write32(offset,uint32_t(value));
    io.writeBarrier();io.write32(offset+4,uint32_t(value>>32));io.complete();
}
}
