/* SPDX-License-Identifier: MIT */
#ifndef NABU_GPU_ACTIVE_IB_H
#define NABU_GPU_ACTIVE_IB_H
#include "NabuGpuFault.h"
#include "NativeAdrenoVm.h"

struct NabuGpuIbWindow {
    uint32_t at=0,count=0,status=0,words[32]{};
};
struct NabuGpuActiveIb {
    uint64_t address=0;
    NativeAdrenoVmAddress mapping{};
    uint32_t initial=0,remaining=0,roq=0,boundedWords=0,cursorHint=0,status=0,count=0;
    bool hintValid=false;
    NabuGpuIbWindow windows[3]{};
};

/* Called with the publication lock held and admission closed, before any
 * failed job is released. Only an existing owned BO mapping is read. Never
 * map a physical address or treat a clipped/failed read as captured words. */
template<class Vm> NabuGpuActiveIb NabuGpuCaptureActiveIb(const Vm &vm,
    const NabuGpuExecutionState &state,unsigned index)
{
    NabuGpuActiveIb out;
    if(index>1) {out.status=1;return out;}
    const unsigned lo=index?NabuGpuExecutionState::Ib2Lo:NabuGpuExecutionState::Ib1Lo;
    const unsigned size=index?NabuGpuExecutionState::Ib2Size:NabuGpuExecutionState::Ib1Size;
    const unsigned roq=index?NabuGpuExecutionState::Roq2:NabuGpuExecutionState::Roq1;
    const uint32_t needed=(7u<<lo)|(1u<<size)|(1u<<roq);
    if((state.valid&needed)!=needed) {out.status=1;return out;}
    out.address=state.ib(index);out.initial=state.words[size]&0xfffff;
    out.remaining=state.words[lo+2];out.roq=state.words[roq]>>16;
    if(!out.address) {out.status=2;return out;}
    out.status=uint32_t(vm.describeAddress(out.address,&out.mapping));
    if(out.status)return out;
    if(!out.mapping.owned || out.mapping.offset>=out.mapping.bytes) {out.status=3;return out;}
    const size_t available=(out.mapping.bytes-out.mapping.offset)/4;
    out.boundedWords=uint32_t(available>0xfffff?0xfffff:available);
    if(out.initial && out.initial<out.boundedWords)out.boundedWords=out.initial;
    const uint64_t buffered=uint64_t(out.remaining)+out.roq;
    out.hintValid=out.initial && buffered<=out.initial;
    if(out.hintValid)out.cursorHint=out.initial-uint32_t(buffered);
    const uint32_t hint=out.cursorHint>16?out.cursorHint-16:0;
    const uint32_t tail=out.boundedWords>32?out.boundedWords-32:0;
    const uint32_t offsets[]={0,hint,tail};
    for(uint32_t at:offsets) {
        if(at>=out.boundedWords)continue;
        bool seen=false;for(unsigned i=0;i<out.count;++i)if(out.windows[i].at==at)seen=true;
        if(seen)continue;
        auto &window=out.windows[out.count++];window.at=at;
        const uint32_t words=out.boundedWords-at<32?out.boundedWords-at:32;
        window.status=uint32_t(vm.peekWords(out.address+uint64_t(at)*4,window.words,words));
        if(!window.status)window.count=words;
    }
    return out;
}
#endif
