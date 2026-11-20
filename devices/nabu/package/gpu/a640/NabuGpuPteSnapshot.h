/* SPDX-License-Identifier: MIT */
#ifndef NABU_GPU_PTE_SNAPSHOT_H
#define NABU_GPU_PTE_SNAPSHOT_H
#include <stdint.h>
struct NabuGpuPteSnapshot {uint64_t descriptor=0;unsigned level=0;bool valid=false;};
/* Native kernel translation uses TG1=4K and <=48-bit PA. Read only managed
 * table pages of retained kernel/physmap mappings. The caller never passes a
 * user VA whose page-table lifetime could race vm_deallocate. No PTE writes. */
template<class Read>
NabuGpuPteSnapshot nabuGpuKernelPte(uint64_t va,uint64_t ttbr,uint64_t tcr,Read &read)
{
    const unsigned size=unsigned((tcr>>16)&63);
    if(((tcr>>30)&3)!=2 || size<16 || size>39)return {};
    const unsigned bits=64-size;
    if((va>>bits)!=(UINT64_MAX>>bits))return {};
    const unsigned first=(bits>39)?0:(bits>30)?1:2;
    uint64_t table=ttbr&UINT64_C(0x0000fffffffff000);
    for(unsigned level=first;level<=3;++level) {
        const unsigned shift=39-9*level;
        const uint64_t address=table+((va>>shift)&511)*8;
        uint64_t entry=0;
        if(!read(address,&entry) || !(entry&1))return {};
        if(level==3)return {entry,level,(entry&3)==3};
        if(!(entry&2))return {entry,level,level!=0}; // L1/L2 block
        table=entry&UINT64_C(0x0000fffffffff000);
        if(!table)return {};
    }
    return {};
}
#endif
