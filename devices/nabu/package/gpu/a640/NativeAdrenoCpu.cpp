/* SPDX-License-Identifier: MIT */
#include "NativeAdrenoMemory.h"
#include "NabuGpuPagePolicy.h"
#include "NabuGpuPteSnapshot.h"
#include "NabuGpuPhysmap.h"
// XNU osfmk/kern/cpu_data.h exports these C entry points. Avoid pulling
// private cpu_data_t layouts into a driver that only needs preemption control.
extern "C" void _disable_preemption(void);
extern "C" void _enable_preemption(void);
#include <vm/vm_map.h>
#include <IOKit/IOLib.h>
extern "C" vm_map_t kernel_map;
// XNU osfmk/vm/vm_map.h; hidden by the SDK kernel-private include fence.
extern "C" int vm_map_page_size(vm_map_t map);
#include <mach/vm_param.h>
#include <vm/memory_types.h>
#include <pexpert/pexpert.h>
extern "C" uint64_t gPhysBase,gPhysSize;
extern "C" unsigned int pmap_cache_attributes(ppnum_t pn);
extern "C" void pmap_batch_set_cache_attributes(const NabuGpuPmapPageList *,unsigned);
extern "C" void pmap_sync_page_attributes_phys(ppnum_t);
/* The board diagnostic ABI retains these zero-valued counters. */
extern "C" {volatile uint64_t native_adreno_cache_stats[5];}
extern "C" {volatile uint64_t native_adreno_reassert_ticks;}

IOReturn NativeAdrenoQueryCpu4K(NativeAdrenoCpuState *out)
{
    if(!out)return kIOReturnBadArgument;
    *out={};
    _disable_preemption();
    __asm__ volatile("mrs %0, tcr_el1" : "=r"(out->tcr));
    __asm__ volatile("mrs %0, ttbr0_el1" : "=r"(out->ttbr0));
    __asm__ volatile("mrs %0, ttbr1_el1" : "=r"(out->ttbr1));
    __asm__ volatile("mrs %0, sctlr_el1" : "=r"(out->sctlr));
    out->kernel_page_bytes=page_size;
    out->kernel_map_page_bytes=vm_map_page_size(kernel_map);
    _enable_preemption();
    bool native=PAGE_SIZE==4096 && out->kernel_page_bytes==4096 && out->kernel_map_page_bytes==4096 &&
        ((out->tcr>>14)&3)==0 && ((out->tcr>>30)&3)==2 &&
        (out->sctlr&1) && (out->ttbr1&UINT64_C(0x0000fffffffff000));
    return native?kIOReturnSuccess:kIOReturnUnsupported;
}
NativeAdrenoCpuCacheMode NativeAdrenoQueryPageCacheMode(uint64_t physical)
{
    if((physical&4095) || physical<gPhysBase || physical-gPhysBase>=gPhysSize ||
        gPhysSize-(physical-gPhysBase)<4096) {
        return NativeAdrenoCpuCacheMode::Unknown;
    }

    const unsigned attribute=pmap_cache_attributes((ppnum_t)(physical>>12))&VM_WIMG_MASK;
    switch(attribute) {
        case VM_WIMG_COPYBACK:
        case VM_WIMG_INNERWBACK:
        case VM_WIMG_WTHRU:
            return NativeAdrenoCpuCacheMode::Cached;
        case VM_WIMG_WCOMB:
            return NativeAdrenoCpuCacheMode::NormalUncached;
        case VM_WIMG_RT: {
            int managed_rt_normal=0;
            PE_parse_boot_argn("t733-managed-rt-normal",&managed_rt_normal,sizeof(managed_rt_normal));
            if(managed_rt_normal==1) {
                return NativeAdrenoCpuCacheMode::NormalUncached;
            }
            return NativeAdrenoCpuCacheMode::Unknown;
        }
        default:
            return NativeAdrenoCpuCacheMode::Unknown;
    }
}

IOReturn NativeAdrenoSetOwnedPageCache(const NativeAdrenoPhysicalSpan *spans,size_t count,
    bool writeCombined,uint32_t *changed,bool forceSync)
{
    struct Ops {
        unsigned attribute(uint32_t page){return pmap_cache_attributes(page)&VM_WIMG_MASK;}
        void batch(const NabuGpuPmapPageList *list,unsigned target){pmap_batch_set_cache_attributes(list,target);}
        void sync(uint32_t page){pmap_sync_page_attributes_phys(page);}
    } ops;
    const auto result=nabuGpuApplyPagePolicy(spans,count,gPhysBase,gPhysSize,
        writeCombined?VM_WIMG_WCOMB:VM_WIMG_DEFAULT,ops,changed,forceSync);
    __asm__ volatile("dsb sy" ::: "memory");
    return result==NabuGpuPagePolicyResult::Ok?kIOReturnSuccess:
        result==NabuGpuPagePolicyResult::Argument?kIOReturnBadArgument:kIOReturnIOError;
}

// pmap_pte is a local inline symbol, not a driver link interface. Walk the
// live TTBR1 translation through bounded managed table pages instead.
void NativeAdrenoMemory::cacheSnapshot(const char *phase,size_t offset,uint64_t userAddress) const
{
    if(!storage_.prepared || offset>=storage_.bytes || !storage_.mapping)return;
    uint64_t physical=0;
    for(size_t i=0;i<storage_.span_count;++i) {
        const auto &span=storage_.spans[i];
        if(offset>=span.offset && offset-span.offset<span.bytes) {
            physical=span.physical+offset-span.offset;break;
        }
    }
    if(physical<gPhysBase || physical-gPhysBase>=gPhysSize)return;
    const uint64_t kva=reinterpret_cast<uintptr_t>(storage_.mapping)+offset;
    const uint64_t direct=phystokv(physical);
    uint64_t mair=0,mpidr=0,ttbr=0,tcr=0;
    _disable_preemption();
    __asm__ volatile("mrs %0, mair_el1" : "=r"(mair));
    __asm__ volatile("mrs %0, mpidr_el1" : "=r"(mpidr));
    __asm__ volatile("mrs %0, ttbr1_el1" : "=r"(ttbr));
    __asm__ volatile("mrs %0, tcr_el1" : "=r"(tcr));
    _enable_preemption();
    auto read=[](uint64_t address,uint64_t *entry) {
        if((address&7) || address<gPhysBase || address-gPhysBase>=gPhysSize ||
            gPhysSize-(address-gPhysBase)<8)return false;
        const uint64_t va=phystokv(address);
        *entry=__atomic_load_n(reinterpret_cast<const uint64_t *>(va),__ATOMIC_RELAXED);
        return true;
    };
    const auto kp=nabuGpuKernelPte(kva,ttbr,tcr,read);
    // A missing retained kernel mapping is diagnostic failure, never a reason
    // to read the direct mapping's bytes or continue a speculative walk.
    const auto dp=kp.valid?nabuGpuKernelPte(direct,ttbr,tcr,read):NabuGpuPteSnapshot{};
    // The caller may independently vm_deallocate a user mapping. Do not walk
    // its unprotected page-table pointer. Sample the retained kernel mapping
    // and stable physmap instead, and label the user address as unqueried.
    const uint64_t k=kp.descriptor;
    const uint64_t d=dp.descriptor;
    IOLog("[bo pte] phase=%s cpu=%llx mair=%016llx wc_byte=%02x ttbr1=%016llx tcr=%016llx pn=%llx wimg=%x kva=%llx pte=%016llx valid=%u level=%u attr=%u sh=%u direct=%llx direct_pte=%016llx direct_valid=%u direct_level=%u direct_attr=%u direct_sh=%u uva=%llx user_pte=unqueried physmap_sample=1\n",
        phase,mpidr,mair,unsigned((mair>>8)&255),ttbr,tcr,physical>>12,pmap_cache_attributes(ppnum_t(physical>>12)),
        kva,k,unsigned(kp.valid),kp.level,unsigned((k>>2)&7),unsigned((k>>8)&3),direct,d,unsigned(dp.valid),dp.level,unsigned((d>>2)&7),unsigned((d>>8)&3),
        userAddress);
}
