/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef NABU_GPU_PERFORMANCE_H
#define NABU_GPU_PERFORMANCE_H
#include "NativeAdrenoA6xxPower.h"

/* Stock nabu FDT /soc/gpu_opp_table_v2 and kgsl-3d0/gpu-pwrlevel-bins.
 * OPP "microvolt" cells are RPMh levels plus one, not literal microvolts.
 * Keep only frequencies supported by BOTH bins until a speed-bin fuse is read.
 * 675 MHz is bin1-only. At 585 MHz bin0 requires DDR usecase11; bin1 allows
 * usecases9..11, so11 is valid for both. At345 MHz usecase4 is in both ranges.
 * HFI orders GX levels upward, the reverse of KGSL's pwrlevel numbering. */
struct NabuGpuOpp { uint32_t khz, arcLevel, bandwidth; };
static constexpr NabuGpuOpp NabuGpuOpps[] = {
    {0,0,0}, {257000,64,3}, {345000,128,4}, {427000,192,6},
    {499200,224,9}, {585000,256,11}
};
static constexpr uint32_t NabuGpuDdrKbps[] = {
    0,795000,1195000,1799000,2183000,2719000,3067000,4063000,
    5407000,6215000,7211000,8363000
};
static constexpr unsigned NabuGpuOppCount = sizeof(NabuGpuOpps)/sizeof(NabuGpuOpps[0]);
static constexpr unsigned NabuGpuDdrCount = sizeof(NabuGpuDdrKbps)/sizeof(NabuGpuDdrKbps[0]);
static_assert(NabuGpuOppCount<=16 && NabuGpuDdrCount<=16,"HFI v2 table capacity");

inline unsigned nabuGpuOppIndex(uint32_t khz)
{
    for(unsigned i=1;i<NabuGpuOppCount;++i)if(NabuGpuOpps[i].khz==khz)return i;
    return 0;
}

/* Validate fixed-capacity wire arrays before any HFI transaction. ARC indices
 * are resolved against live command-db by Power, not hardcoded PMIC values. */
inline bool nabuGpuPerformanceTablesValid(const NativeAdrenoA6xxTables &t)
{
    if(t.performance[0]!=NabuGpuOppCount || t.performance[1]!=2 ||
        !t.gpuLevel || t.gpuLevel>=NabuGpuOppCount ||
        t.bandwidthLevel!=NabuGpuOpps[t.gpuLevel].bandwidth ||
        t.bandwidth[0]!=NabuGpuDdrCount || t.bandwidth[1]!=3 || t.bandwidth[2]!=3 ||
        t.performance[50] || t.performance[51] ||
        (t.performance[52]>>16)<48 || (t.performance[52]>>16)>64 ||
        (t.performance[52]&255)>=16 || ((t.performance[52]>>8)&255)>=16 ||
        t.performance[53]!=200000)return false;
    for(unsigned i=0;i<NabuGpuOppCount;++i) {
        const uint32_t vote=t.performance[2+3*i];
        if(t.performance[3+3*i]!=0xffffffff || t.performance[4+3*i]!=NabuGpuOpps[i].khz ||
            (!i && vote) || (i && ((vote>>16)<NabuGpuOpps[i].arcLevel ||
                (vote>>16)>256 || (vote&255)>=16 || ((vote>>8)&255)>=16)))return false;
    }
    for(unsigned i=2+3*NabuGpuOppCount;i<50;++i)if(t.performance[i])return false;
    for(unsigned i=54;i<58;++i)if(t.performance[i])return false;
    if(t.bandwidth[3]&~7u || t.bandwidth[4]&~7u)return false;
    for(unsigned i=0;i<3;++i) {
        if(!t.bandwidth[5+i] || !t.bandwidth[23+i] ||
            (t.bandwidth[5+i]&3) || (t.bandwidth[23+i]&3))return false;
        for(unsigned level=0;level<NabuGpuDdrCount;++level) {
            const uint32_t value=t.bandwidth[31+8*level+i];
            if((!level && (value&0x20003fffu)) || (level && !(value&(1u<<29))))return false;
        }
    }
    return true;
}

inline bool nabuGpuPerformanceVote(const NativeAdrenoA6xxTables &t,uint32_t (&payload)[3])
{
    if(!nabuGpuPerformanceTablesValid(t))return false;
    payload[0]=1; // DCVS_ACK_BLOCK
    payload[1]=t.gpuLevel;
    payload[2]=t.bandwidthLevel;
    return true;
}
#endif
