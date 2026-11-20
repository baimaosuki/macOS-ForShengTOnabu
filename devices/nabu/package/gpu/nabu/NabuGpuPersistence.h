/* SPDX-License-Identifier: MIT */
#pragma once
#include <stdint.h>

enum class NabuGpuPersistenceResult {Ready,Stopped,Timeout};
struct NabuGpuPersistenceReport {
    unsigned waitedMs=0,state=0;
    uint64_t target=0,committed=0;
};

/* A fixed snapshot is essential: a continuously growing console must not move
 * the goalpost while the journal commits an earlier snapshot. Only this boot's
 * writer state 2 and completed commit position authorize the hardware step.
 * All waiting sleeps on the GPU worker; no UFS calls or main-thread blocking. */
template<class Peer>
NabuGpuPersistenceResult nabuGpuWaitPersistence(Peer &peer,unsigned timeoutMs,
                                               NabuGpuPersistenceReport &report)
{
    report={};
    for(;;) {
        report.state=peer.writerState();
        if(report.state==3)return NabuGpuPersistenceResult::Stopped;
        if(report.state==2) {
            if(!report.target) {
                uint64_t target=0;
                if(peer.snapshotEnd(target) && target)report.target=target;
            }
            report.committed=peer.committedEnd();
            if(report.target && report.committed>=report.target)
                return NabuGpuPersistenceResult::Ready;
        }
        if(report.waitedMs>=timeoutMs)return NabuGpuPersistenceResult::Timeout;
        const unsigned remaining=timeoutMs-report.waitedMs;
        const unsigned wait=remaining<20?remaining:20;
        peer.sleepMs(wait);
        report.waitedMs+=wait;
    }
}
