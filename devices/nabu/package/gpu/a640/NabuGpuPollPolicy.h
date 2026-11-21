/* SPDX-License-Identifier: MIT */
#ifndef NABU_GPU_POLL_POLICY_H
#define NABU_GPU_POLL_POLICY_H
#include <stdint.h>

/* The nabu adapter dispatches IRQ callbacks on the hardware worker, not GIC.
 * Keep one-ms service while fences are pending, and through a short idle gap
 * between compositor bursts. This changes observation latency, not timeouts. */
class NabuGpuPollPolicy {
    uint64_t activeAt_=0;
    bool seenActive_=false;
public:
    unsigned delayMs(uint64_t now,uint32_t submitted,uint32_t completed,bool ready=true) {
        if(!ready)return 10;
        if(submitted!=completed) {activeAt_=now;seenActive_=true;return 1;}
        return seenActive_ && now>=activeAt_ && now-activeAt_<20000?1:10;
    }
};
#endif
