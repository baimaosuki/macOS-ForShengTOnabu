#!/usr/bin/env python3
"""Exercise the actual generated NABU start/timer functions without Apple IOKit.

The timer stub requires completed family initialization and a nonzero deadline.
It deliberately models enable() without arming a new timer. Full kernel/IOKit
compilation and device delivery remain separate checks.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile


def function(source, signature):
    begin = source.index(signature)
    brace = source.index('{', begin)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[begin:end]


HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <vector>
#include <string>
#include <cstdio>
#include "nabu_vsync25.h"
using IOReturn = int;
constexpr int kIOReturnSuccess=0, kIOReturnNotReady=-1;
constexpr int kIODirectionInOut=1, kIOMapAnywhere=2, kIOMapWriteCombineCache=4;
static bool baseSuccess=true, familyReady=false, timerMissing=false;
static int armResult=0, stopCalls=0, armCalls=0, timerEntries=0, swaps=0;
static bool armed=false;
static unsigned refreshCalls=0, sends=0;
template<class T> void store(void *object,unsigned offset,T value) {
    memcpy(static_cast<uint8_t *>(object)+offset,&value,sizeof(value));
}
extern "C" void nabuVsync25NativeNote(void *object,unsigned period) {
    assert(period==16000000 && nabu_vsync25::clockWantedGated(object));
    refreshCalls++;
    store<uint64_t>(object,nabu_vsync25::kCount,refreshCalls);
    store<uint64_t>(object,nabu_vsync25::kLastMach,refreshCalls*307200);
    store<uint64_t>(object,nabu_vsync25::kInterval,307200);
}
extern "C" void nabuVsync25NativeNotify(void *object,uint64_t count,uint64_t now,uint64_t interval) {
    assert(nabu_vsync25::wantedGated(object));
    assert(count==refreshCalls && now==refreshCalls*307200 && interval==307200);
    sends++;
}
static std::vector<std::string> logLines;
template<class... T> void nabuDisplayLog(const char *format,T...) {
    logLines.emplace_back(format);
    if (strstr(format,"first-timer-fired")) timerEntries++;
}
struct OSObject {};
struct IOService : OSObject {};
struct IOTimerEventSource {
    IOReturn setTimeoutMS(int ms) {
        assert(familyReady && ms == 16);
        armCalls++;
        armed=armResult == 0;
        return armResult;
    }
};
static IOTimerEventSource timer;
struct PE_Video {
    unsigned long v_baseAddr=0x9c000000, v_depth=32, v_width=1600,
                  v_height=2560, v_rowBytes=6400;
};
struct IOMemoryMap {
    uintptr_t getVirtualAddress() { return 0x1000; }
    void release() {}
};
static IOMemoryMap map;
struct IOMemoryDescriptor {
    static IOMemoryDescriptor *withPhysicalAddress(uint64_t,uint64_t,int) {
        static IOMemoryDescriptor descriptor; return &descriptor;
    }
    IOMemoryMap *createMappingInTask(void *,int,int) { return &map; }
    void release() {}
};
struct NabuGopSourceReaper {
    static NabuGopSourceReaper *create() { static NabuGopSourceReaper worker; return &worker; }
    void finish(IOMemoryMap **) {}
};
struct Platform { int getConsoleInfo(PE_Video *) { return 0; } };
static Platform platform;
static void *kernel_task=nullptr;
static void PE_parse_boot_argn(const char *key,unsigned *value,unsigned) {
    *value=!strcmp(key,"cpus") ? 8 : 1;
}
static bool nabuDisplayConsoleValid(uint64_t,uint64_t,uint64_t,uint64_t) { return true; }
#define OSSafeReleaseNULL(obj) do { if(obj) (obj)->release(); obj=nullptr; } while(0)
struct IOMobileFramebufferLegacy : IOService {
    alignas(uint64_t) uint8_t nativeState[0x1af8]{};
    bool start(IOService *) { familyReady=baseSuccess; return baseSuccess; }
};
struct NativeGOPDisplay : IOMobileFramebufferLegacy {
    PE_Video fVideo{};
    IOMemoryMap *fScanoutMap=nullptr;
    uint8_t *fScanout=nullptr;
    NabuGopSourceReaper *fSourceReaper=nullptr;
    IOTimerEventSource *fVBL=timerMissing ? nullptr : &timer;
    uint64_t fTicks=0;
    bool fDisplayOn=false;
    uint64_t fVsyncSent=0;
    Platform *getPlatform() { return &platform; }
    unsigned display_width() { return 1600; }
    unsigned display_height() { return 2560; }
    void stop(IOService *) { stopCalls++; armed=false; }
    void swap_begin_gated() {
        if (fDisplayOn && nabu_vsync25::clockWantedGated(this)) {
            // Any queued frame sees the native note from THIS callback,
            // including completion-only clients after native time was reset.
            assert(nabu_vsync25::load<uint64_t>(this,nabu_vsync25::kLastMach)==refreshCalls*307200);
            assert(refreshCalls>0);
        }
        swaps++; fTicks++;
    }
    bool start(IOService *);
    static void vblFired(OSObject *,IOTimerEventSource *);
};
'''

CHECKS = r'''
int main() {
    IOService provider;
    NativeGOPDisplay display;
    assert(display.start(&provider));
    assert(armed && armCalls==1 && swaps==0 && timerEntries==0);
    NativeGOPDisplay::vblFired(&display,&timer);
    NativeGOPDisplay::vblFired(&display,&timer);
    assert(swaps==2 && timerEntries==1);
    assert(refreshCalls==0 && display.fVsyncSent==0);
    display.fDisplayOn=true;
    store<uintptr_t>(&display,nabu_vsync25::kType5Head,0x1234);
    NativeGOPDisplay::vblFired(&display,&timer);
    assert(refreshCalls==1 && display.fVsyncSent==1 && sends==1 && swaps==3);
    store<uintptr_t>(&display,nabu_vsync25::kType5Head,0);
    store<uintptr_t>(&display,nabu_vsync25::kType3Head,0x5678);
    store<uint64_t>(&display,nabu_vsync25::kLastMach,0);
    NativeGOPDisplay::vblFired(&display,&timer);
    assert(refreshCalls==2 && display.fVsyncSent==1 && sends==1 && swaps==4);
    store<uintptr_t>(&display,nabu_vsync25::kType3Head,0);
    NativeGOPDisplay::vblFired(&display,&timer);
    assert(refreshCalls==2 && sends==1 && swaps==5);
    display.fDisplayOn=false;
    store<uintptr_t>(&display,nabu_vsync25::kType3Head,0x5678);
    NativeGOPDisplay::vblFired(&display,&timer);
    assert(refreshCalls==2 && sends==1 && swaps==6);
    familyReady=false; baseSuccess=false;
    NativeGOPDisplay failedBase;
    assert(!failedBase.start(&provider));
    assert(armCalls==1 && stopCalls==0);
    baseSuccess=true; familyReady=false; armResult=-5;
    NativeGOPDisplay failedArm;
    assert(!failedArm.start(&provider));
    assert(armCalls==2 && stopCalls==1 && !armed);
    familyReady=false; timerMissing=true; armResult=0;
    NativeGOPDisplay missingTimer;
    assert(!missingTimer.start(&provider));
    assert(armCalls==2 && stopCalls==2);
    assert(!display.start(nullptr));
    puts("PASS: arm after family start; first callback without submit; base/arm/missing-timer failure cleanup");
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    args = parser.parse_args()
    source = args.source.read_text()
    actual = '\n'.join(function(source, signature) for signature in (
        'bool NativeGOPDisplay::start(IOService *provider)',
        'void NativeGOPDisplay::vblFired(OSObject *owner, IOTimerEventSource *)'))
    with tempfile.TemporaryDirectory(prefix='nabu-refresh-test-') as temp:
        cpp, executable = Path(temp) / 'refresh.cpp', Path(temp) / 'refresh'
        cpp.write_text(HARNESS + actual + CHECKS)
        subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', '-I', str(Path(__file__).resolve().parents[1] / 'platform/nabu/macos26/display'),
                        str(cpp), '-o', str(executable)], check=True)
        subprocess.run([str(executable)], check=True)


if __name__ == '__main__':
    main()
