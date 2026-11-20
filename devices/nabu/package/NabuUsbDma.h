// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once
#include "NabuUsbSmmu.h"
namespace nabu {
// The USB controller is halted before changing its exact SID mapping. Never
// reuse a firmware/shared bypass context: Apple's XHCI needs coherent output.
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
        // Firmware initializes unused AArch64 S1 banks too: CBAR=1f000,
        // CBA2R=1 and disabled SCTLR=9f00e0 are not reset zeros. An unreferenced
        // bank with M=0 and that qualified S1/S2-bypass type can be programmed
        // while USB is halted. Never reuse an enabled or stream-owned bank.
        const bool reset=!control && !cbar && !cba2r;
        const bool disabled=!(control&sm::kSctlrM) &&
            cbar==0x0001f000 && cba2r==sm::kCba2rVa64;
        if(!reset && !disabled)continue;
        sm::Plan plan={};plan.valid=true;plan.index=slot;plan.bank=b;plan.global_pages=info.global_pages;
        plan.sctlr=control;plan.cbar=cbar;plan.cba2r=cba2r;return plan;
    }
    return sm::plan_error("no-free-context-bank");
}
inline uint64_t usbIdentityBlock(unsigned i){return i>=2&&i<8 ? uint64_t(i)<<30|1ull<<10|3ull<<8|1ull<<6|1ull : 0;}
}
