/* SPDX-License-Identifier: MIT */
#ifndef NABU_GPU_FAULT_H
#define NABU_GPU_FAULT_H
#include <stdint.h>

struct NabuGpuCpFault {
    uint32_t irq=0,cp=0,protect=0,valid=0;
    uint32_t opcode=0,rptr=0,wptr=0,hw_fault=0;
};

/* First-error ownership prevents a subsequent empty IRQ poll from replacing
 * the failing CP state. No allocation, log output or wait in the IRQ path.
 * A640's stock adreno_a6xx.c selects SQE_STAT_ADDR=1 before reading the
 * offending opcode; 0x908/0x909 and 0x821 are A6xx dword register indices. */
class NabuGpuFaultCapture final {
    uint32_t claimed_=0,ready_=0;
    NabuGpuCpFault first_{};
public:
    enum : uint32_t {OpcodeValid=1,RptrValid=2,WptrValid=4,HwFaultValid=8};

    template<class Io>
    bool capture(const Io &io,uint32_t irq,uint32_t cp,uint32_t protect)
    {
        uint32_t expected=0;
        if(!__atomic_compare_exchange_n(&claimed_,&expected,1,false,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE))
            return false;
        first_.irq=irq;first_.cp=cp;first_.protect=protect;
        if((cp&1) && io.write32 && io.read32 && io.barrier &&
            !io.write32(io.cookie,0x908,1) && !io.barrier(io.cookie) &&
            !io.read32(io.cookie,0x909,&first_.opcode))first_.valid|=OpcodeValid;
        if(io.read32) {
            if(!io.read32(io.cookie,0x806,&first_.rptr))first_.valid|=RptrValid;
            if(!io.read32(io.cookie,0x807,&first_.wptr))first_.valid|=WptrValid;
            if((cp&4) && !io.read32(io.cookie,0x821,&first_.hw_fault))first_.valid|=HwFaultValid;
        }
        __atomic_store_n(&ready_,1,__ATOMIC_RELEASE);
        return true;
    }

    bool claimed()const {return __atomic_load_n(&claimed_,__ATOMIC_ACQUIRE)!=0;}
    bool snapshot(NabuGpuCpFault *out)const
    {
        if(!out || !__atomic_load_n(&ready_,__ATOMIC_ACQUIRE))return false;
        *out=first_;return true;
    }
};
#endif
