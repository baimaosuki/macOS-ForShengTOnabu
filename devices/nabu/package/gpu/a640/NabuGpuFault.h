/* SPDX-License-Identifier: MIT */
#ifndef NABU_GPU_FAULT_H
#define NABU_GPU_FAULT_H
#include <stdint.h>

enum : unsigned {NabuGpuIbHeadWords=256};
struct NabuGpuExecutionState {
    enum Index : unsigned {Rptr,Wptr,Ib1Lo,Ib1Hi,Ib1Rem,Ib2Lo,Ib2Hi,Ib2Rem,
        Ib1Size,Ib2Size,Roq1,Roq2,Rbbm,Rbbm1,Rbbm2,Rbbm3,CpStatus,SqeControl,Count};
    uint32_t words[Count]{},valid=0;
    bool complete() const {return valid==((1u<<Count)-1);}
    uint64_t ib(unsigned index) const {
        const unsigned at=index?Ib2Lo:Ib1Lo;
        return uint64_t(words[at+1])<<32|words[at];
    }
    bool advancedFrom(const NabuGpuExecutionState &before) const {
        const unsigned fields[]={Rptr,Ib1Lo,Ib1Hi,Ib1Rem,Ib2Lo,Ib2Hi,Ib2Rem,Roq1,Roq2};
        for(unsigned at:fields)if(words[at]!=before.words[at])return true;
        return false;
    }
};

/* A640 register indices from a6xx.xml. CP_STATUS_1/SQE_CNTL replace the
 * legacy CP_STAT/ME_CNTL registers; reading this snapshot never selects a
 * debug bank, clears an IRQ, allocates, or waits. Busy bits are evidence,
 * not proof of forward execution. */
template<class Io> NabuGpuExecutionState NabuGpuReadExecution(const Io &io)
{
    NabuGpuExecutionState state;
    const uint32_t regs[]={0x806,0x807,0x928,0x929,0x92a,0x92b,0x92c,0x92d,
        0x943,0x944,0x949,0x94a,0x210,0x211,0x212,0x213,0x825,0x808};
    if(io.read32)for(unsigned i=0;i<NabuGpuExecutionState::Count;++i)
        if(!io.read32(io.cookie,regs[i],&state.words[i]))state.valid|=1u<<i;
    return state;
}

class NabuGpuStallConfirmation {
    NabuGpuExecutionState before_{};
    uint64_t at_=0;
    uint32_t completed_=0;
    bool armed_=false;
public:
    enum Result {Waiting,Moving,Stopped,Unreadable};
    void reset() {armed_=false;}
    const NabuGpuExecutionState &before() const {return before_;}
    uint64_t interval(uint64_t now)const {return armed_ && now>=at_?now-at_:0;}
    Result sample(uint64_t now,uint64_t elapsed,uint32_t completed,const NabuGpuExecutionState &state) {
        if(elapsed<450000) {reset();return Waiting;}
        if(!armed_ || completed!=completed_ || now<at_) {
            before_=state;at_=now;completed_=completed;armed_=true;return Waiting;
        }
        if(elapsed<=500000 || now-at_<30000)return Waiting;
        if(!before_.complete() || !state.complete())return Unreadable;
        return state.advancedFrom(before_)?Moving:Stopped;
    }
};

struct NabuGpuCpFault {
    uint32_t irq=0,cp=0,protect=0,valid=0;
    uint32_t opcode=0,rptr=0,wptr=0,hw_fault=0;
    uint32_t ib1_lo=0,ib1_hi=0,ib1_rem=0,ib2_lo=0,ib2_hi=0,ib2_rem=0;
    NabuGpuExecutionState execution{};
};

/* First-error ownership prevents a subsequent empty IRQ poll from replacing
 * the failing CP state. No allocation, log output or wait in the IRQ path.
 * A640's stock adreno_a6xx.c selects SQE_STAT_ADDR=1 before reading the
 * offending opcode; 0x908/0x909 and 0x821 are A6xx dword register indices. */
class NabuGpuFaultCapture final {
    uint32_t claimed_=0,ready_=0;
    NabuGpuCpFault first_{};
public:
    enum : uint32_t {OpcodeValid=1,RptrValid=2,WptrValid=4,HwFaultValid=8,
        Ib1LoValid=16,Ib1HiValid=32,Ib1RemValid=64,Ib2LoValid=128,Ib2HiValid=256,Ib2RemValid=512};

    template<class Io>
    bool capture(const Io &io,uint32_t irq,uint32_t cp,uint32_t protect)
    {
        uint32_t expected=0;
        if(!__atomic_compare_exchange_n(&claimed_,&expected,1,false,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE))
            return false;
        first_.irq=irq;first_.cp=cp;first_.protect=protect;
        first_.execution=NabuGpuReadExecution(io);
        if((cp&1) && io.write32 && io.read32 && io.barrier &&
            !io.write32(io.cookie,0x908,1) && !io.barrier(io.cookie) &&
            !io.read32(io.cookie,0x909,&first_.opcode))first_.valid|=OpcodeValid;
        const auto &e=first_.execution;
        uint32_t *fields[]={&first_.rptr,&first_.wptr,&first_.ib1_lo,&first_.ib1_hi,
            &first_.ib1_rem,&first_.ib2_lo,&first_.ib2_hi,&first_.ib2_rem};
        const uint32_t flags[]={RptrValid,WptrValid,Ib1LoValid,Ib1HiValid,Ib1RemValid,
            Ib2LoValid,Ib2HiValid,Ib2RemValid};
        for(unsigned i=0;i<8;++i)if(e.valid&(1u<<i)) {
            *fields[i]=e.words[i];first_.valid|=flags[i];
        }
        if((cp&4) && io.read32 && !io.read32(io.cookie,0x821,&first_.hw_fault))
            first_.valid|=HwFaultValid;
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
