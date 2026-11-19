/* SPDX-License-Identifier: MIT */
// Execute the production recorder with host allocation/MMIO/lock peers.
#include <cassert>
#include <thread>
#include <vector>
#include "../package/gpu/a640/NativeAdrenoGpuProfile.cpp"

static uint64_t testTime;
int NativeAdrenoKernelTime::now_us(void *, uint64_t *out) { *out=testTime; return 0; }
int NativeAdrenoKernelTime::wait_us(void *, unsigned) { return 0; }
static int writeReg(void *, uint32_t, uint32_t) { return 0; }
static int readReg(void *, uint32_t, uint32_t *v) { *v=1; return 0; }
static int barrier(void *) { return 0; }
static int readAo(void *, uint64_t *v) { *v=testTime; return 0; }
static IOReturn acquire(void *) { return kIOReturnSuccess; }
static IOReturn release(void *) { return kIOReturnSuccess; }
static bool accessible(void *) { return true; }

int main()
{
    assert(native_adreno_gpu_profile_start(100)==uint32_t(kIOReturnNotReady));
    NativeAdrenoRingIo io{nullptr,writeReg,readReg,barrier,nullptr,nullptr,readAo};
    NativeAdrenoGpuProfileAccess access{acquire,release,accessible};
    assert(NativeAdrenoGpuProfile::initialize(&io,access)==0);
    assert(testLiveBytes==0 && !samples && !jobs);
    // Fail each allocation independently. No partial pair may become visible.
    for (int fail=1;fail<=2;++fail) {
        testAllocCalls=0; testFailAt=fail;
        assert(native_adreno_gpu_profile_start(100)==uint32_t(kIOReturnNoMemory));
        assert(testLiveBytes==0 && !samples && !jobs && !workerNeeded);
    }
    testAllocCalls=0; testFailAt=0;
    std::atomic<unsigned> accepted{0};
    std::vector<std::thread> callers;
    for (unsigned i=0;i<12;++i) callers.emplace_back([&] {
        auto rc=native_adreno_gpu_profile_start(100);
        if (rc==0) ++accepted; else assert(rc==uint32_t(kIOReturnBusy));
    });
    for (auto &t:callers) t.join();
    assert(accepted==1 && samples && jobs);
    assert(testLiveBytes==sizeof(CounterSample)*SampleCapacity+sizeof(JobSample)*JobCapacity);
    assert(NativeAdrenoGpuProfile::poll()==0 && NativeAdrenoGpuProfile::active());
    assert(sampleCount==1 && samples[0].validMask==((1u<<CounterCount)-1));
    auto token=NativeAdrenoGpuProfile::beginJob(); assert(token);
    NativeAdrenoGpuProfile::finishJob(token,1,1,10,20,30,true,true);
    assert(jobCount==1 && jobs[0].queueValid && jobs[0].rbValid);
    native_adreno_gpu_profile_stop(); assert(NativeAdrenoGpuProfile::poll()==0);
    native_adreno_gpu_profile_dump();
    auto allocations=testAllocCalls.load();
    assert(native_adreno_gpu_profile_start(100)==0 && testAllocCalls==allocations);
    NativeAdrenoGpuProfile::fault(); NativeAdrenoGpuProfile::hardwareStopped();
    assert(native_adreno_gpu_profile_start(100)==uint32_t(kIOReturnNotReady));
}
