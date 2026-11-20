/* SPDX-License-Identifier: MIT */
#ifndef NABU_GPU_PAGE_POLICY_H
#define NABU_GPU_PAGE_POLICY_H
#include <stddef.h>
#include <stdint.h>

/* Private XNU 12377.121.6 call ABI (osfmk/vm/pmap.h unified_page_list_t,
 * mach/memory_object_types.h upl_page_info). The SDK hides these definitions.
 * The host check compares these layouts with the selected kernel headers.
 * flags are zero: these are real, owned, prepared RAM pages, not fictitious. */
struct NabuGpuPmapPageInfo {uint32_t physicalPage,flags;};
struct NabuGpuPmapPageList {
    union {
        struct {NabuGpuPmapPageInfo *pages;unsigned count;} upl;
        void *head;
    };
    uint8_t type; // UNIFIED_PAGE_LIST_TYPE_UPL_ARRAY = 0
};
static_assert(sizeof(NabuGpuPmapPageInfo)==8,"XNU UPL element ABI");
static_assert(offsetof(NabuGpuPmapPageList,upl.count)==8 &&
    offsetof(NabuGpuPmapPageList,type)==16 && sizeof(NabuGpuPmapPageList)==24,"XNU page-list ABI");

enum class NabuGpuPagePolicyResult {Ok,Argument,Readback};

/* Validate the whole request before mutating anything. The caller owns these
 * wired pages and excludes CPU/GPU access during a cache-mode transition.
 * Batch at most 128 pages per pmap call; no allocations or interrupt masking.
 * pmap's batch API updates PTEs/TLB but lacks single-page pmap_sync_wimg's
 * COPYBACK->WCOMB clean. Synchronize every changed page explicitly after it. */
template<class Span,class Ops>
NabuGpuPagePolicyResult nabuGpuApplyPagePolicy(const Span *spans,size_t count,
    uint64_t physicalBase,uint64_t physicalBytes,unsigned target,Ops &ops,uint32_t *changed,bool forceSync=false,uint32_t *synchronized=nullptr)
{
    if(changed)*changed=0;
    if(synchronized)*synchronized=0;
    if(!spans || !count || !changed || !physicalBytes || physicalBase>UINT64_MAX-physicalBytes)
        return NabuGpuPagePolicyResult::Argument;
    size_t total=0;
    for(size_t i=0;i<count;++i) {
        const auto &span=spans[i];
        if(!span.bytes || ((span.physical|span.bytes)&4095) || span.offset!=total ||
            span.physical<physicalBase || span.physical-physicalBase>=physicalBytes ||
            span.bytes>physicalBytes-(span.physical-physicalBase) || span.bytes>256u*1024u*1024u-total ||
            ((span.physical+span.bytes-1)>>12)>UINT32_MAX)
            return NabuGpuPagePolicyResult::Argument;
        total+=span.bytes;
    }
    NabuGpuPmapPageInfo pages[128]={};unsigned used=0;
    auto flush=[&]() {
        if(!used)return true;
        NabuGpuPmapPageList list{};list.upl.pages=pages;list.upl.count=used;
        ops.batch(&list,target);
        bool valid=true;
        for(unsigned i=0;i<used;++i) {
            ops.sync(pages[i].physicalPage);
            if(synchronized)++*synchronized;
            valid=valid && ops.attribute(pages[i].physicalPage)==target;
        }
        *changed+=used;used=0;return valid;
    };
    for(size_t i=0;i<count;++i)for(uint64_t offset=0;offset<spans[i].bytes;offset+=4096) {
        const uint32_t page=uint32_t((spans[i].physical+offset)>>12);
        if(ops.attribute(page)==target) {
            if(forceSync){ops.sync(page);if(synchronized)++*synchronized;}
            continue;
        }
        pages[used++]={page,0};
        if(used==128 && !flush())return NabuGpuPagePolicyResult::Readback;
    }
    return flush()?NabuGpuPagePolicyResult::Ok:NabuGpuPagePolicyResult::Readback;
}
#endif
