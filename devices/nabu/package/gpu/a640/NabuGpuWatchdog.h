/* SPDX-License-Identifier: MIT */
#ifndef NABU_GPU_WATCHDOG_H
#define NABU_GPU_WATCHDOG_H
#include <stdint.h>

/* The worker may be delayed between samples. Idle time is never GPU execution
 * time; arm on the first observed outstanding fence and restart on progress.
 * More submissions alone do not postpone a hung oldest fence. */
class NabuGpuWatchdog {
    uint64_t since_=0;
    uint32_t completed_=0;
    bool busy_=false;
public:
    bool sample(uint64_t now,uint32_t submitted,uint32_t completed,uint64_t limit)
    {
        if(submitted==completed) {busy_=false;completed_=completed;since_=now;return false;}
        if(!busy_ || completed!=completed_ || now<since_) {
            busy_=true;completed_=completed;since_=now;return false;
        }
        return now-since_>limit;
    }
    uint64_t stalledUs(uint64_t now)const {return busy_ && now>=since_?now-since_:0;}
};
#endif
