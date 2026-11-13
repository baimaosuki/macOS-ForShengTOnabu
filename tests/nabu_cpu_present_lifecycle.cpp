#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <atomic>
#include <mutex>
#include <condition_variable>
#include <thread>
#include <chrono>
#include <cstdarg>
#include <cstdio>
#include <string>
#include "../platform/nabu/macos26/display/nabu_cpu_present.h"
#include "../platform/sheng/macos26/log/framebuffer_owner.h"
sheng_framebuffer_owner sheng_framebuffer{};
extern "C" { int disableConsoleOutput=0; }
static std::mutex traceMutex;
static std::vector<std::string> traceLines;
static uint64_t mach_absolute_time() { return 1000000; }
static void absolutetime_to_nanoseconds(uint64_t ticks,uint64_t *ns) { *ns=ticks; }
extern "C" void nabu_capture_append(const char *data,unsigned bytes) {
    std::lock_guard<std::mutex> guard(traceMutex);traceLines.emplace_back(data,bytes);
}
#define NABU_DISPLAY_TRACE_HOST_TEST
#include "../platform/nabu/macos26/display/nabu_display_trace.h"
volatile uint64_t sheng_panel_ready = 1, sheng_status_pixels = 1;
static constexpr int kIOReturnSuccess=0,kIOMapAnywhere=0,kIOMapReadOnly=0,kPEDisableScreen=0,kPEEnableScreen=1;
static constexpr uint32_t kFormatBGRA=0x42475241,kFormatRGhA=0x52476841;
static constexpr size_t kCurrentSwap=6032,kSwapQueueFirst=6040,kSwapQueueLast=6048,kSwapLayers=824,kSwapNext=3192,kSwapPrev=3200;
static int kernel_task=0;
static unsigned screenDisabled=0;
static std::atomic<bool> blockRelease{false},releaseEntered{false};
static std::atomic<unsigned> activeMaps{0},mapCalls{0},mapReleases{0},prepareCalls{0},completeCalls{0},workers{0};
static void *IOMalloc(uint64_t n) {return malloc(n);}
static void IOFree(void *p,uint64_t) {free(p);}
static int handoffFailure=0;static unsigned screenEnabled=0;
static uint32_t *firstScanout=nullptr;
static void PE_initialize_console(void *,int op) {
    if (op==kPEDisableScreen) {
        if(firstScanout)assert(firstScanout[0]==0xdeadbeef);
        screenDisabled++;
        disableConsoleOutput=1;
        if(handoffFailure==1)sheng_framebuffer.writer=1;
        if(handoffFailure==2)sheng_framebuffer.owner=SHENG_FB_PANIC;
    } else {screenEnabled++;sheng_framebuffer.writer=0;disableConsoleOutput=0;}
}
static void sheng_boot_checkpoint(int) {}
static uint8_t halfToByte(uint16_t half) {return half==0x3c00?255:0;}
template<typename T> static T &at(void *p,size_t n) {return *reinterpret_cast<T *>(static_cast<uint8_t *>(p)+n);}
struct IOMemoryMap {
    std::vector<uint8_t> mappedBytes;
    IOMemoryMap(const uint8_t *data,uint64_t n):mappedBytes(data,data+n) {activeMaps++;mapCalls++;}
    uint64_t getLength() {return mappedBytes.size();}
    uintptr_t getVirtualAddress() {return reinterpret_cast<uintptr_t>(mappedBytes.data());}
    void release() {
        if(blockRelease) {releaseEntered=true;while(blockRelease)std::this_thread::sleep_for(std::chrono::milliseconds(1));}
        assert(activeMaps);activeMaps--;mapReleases++;delete this;
    }
};
struct IOMemoryDescriptor {
    std::vector<uint8_t> bytes;
    bool fail=false, mapFail=false; unsigned prepared=0;
    uint64_t getLength() {return bytes.size();}
    int prepare() {prepareCalls++;if (fail) return -1;prepared++;return 0;}
    void complete() {completeCalls++;assert(prepared);prepared--;}
    IOMemoryMap *createMappingInTask(int,int,int,uint64_t off,uint64_t n) {
        assert(!prepared);return (mapFail || fail)?nullptr:new IOMemoryMap(bytes.data()+off,n);
    }
    uint64_t readBytes(uint64_t off,void *out,uint64_t n) {assert(prepared);memcpy(out,bytes.data()+off,n);return n;}
};
// Execute the real independent reaper with host mutex/condition/thread adapters.
#define NABU_SOURCE_MAPS_HOST_TEST
struct IOLock { std::mutex mutex; std::condition_variable condition; };
static IOLock *IOLockAlloc() {return new IOLock;}
static void IOLockFree(IOLock *p) {delete p;}
static void IOLockLock(IOLock *p) {p->mutex.lock();}
static void IOLockUnlock(IOLock *p) {p->mutex.unlock();}
static void IOLockSleep(IOLock *p,void *,unsigned) {
    std::unique_lock<std::mutex> lock(p->mutex,std::adopt_lock);
    p->condition.wait(lock);lock.release();
}
static void IOLockWakeup(IOLock *p,void *,bool) {p->condition.notify_one();}
static bool failWorkerStart=false;
using wait_result_t=int;using thread_t=std::thread *;
#define THREAD_NULL nullptr
static constexpr int THREAD_UNINT=0,KERN_SUCCESS=0;
static int kernel_thread_start(void (*function)(void *,int),void *arg,thread_t *out) {
    if(failWorkerStart)return -1;
    workers++;*out=new std::thread([=]{function(arg,0);workers--;});return 0;
}
static void thread_deallocate(thread_t t) {t->detach();delete t;}
static void *current_thread() {return nullptr;}
static void thread_terminate(void *) {}
#include "../platform/nabu/macos26/display/nabu_source_maps.h"
static void waitForReapers() {
    for (unsigned i=0;i<1000&&workers;i++)std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(!workers);
}
struct IOSurface {
    uint64_t width,height,rowBytes,base=0; uint32_t format=kFormatBGRA;
    IOMemoryDescriptor memory;
};
static uint64_t ioSurfaceWidth(IOSurface *s) {return s->width;}
static uint64_t ioSurfaceHeight(IOSurface *s) {return s->height;}
static uint64_t ioSurfaceBytesPerRow(IOSurface *s) {return s->rowBytes;}
static uint64_t ioSurfaceBaseOffset(IOSurface *s) {return s->base;}
static uint32_t ioSurfacePixelFormat(IOSurface *s) {return s->format;}
static IOMemoryDescriptor *ioSurfaceMemory(IOSurface *s) {return &s->memory;}
struct IOMFBSwapIORequest {alignas(8) uint8_t bytes[3300]{};};
struct NativeGOPDisplay {
    alignas(8) uint8_t family[7000]{};
    struct {uint64_t v_width=4,v_height=2,v_rowBytes=24;} fVideo;
    uint32_t *fComposition=nullptr;
    uint8_t *fScanout=nullptr,*fHalfToByte=nullptr;
    uint16_t *fRow=nullptr;uint64_t fRowBytes=0;
    NabuGopSourceMap fSourceMaps[4]{};
    NabuGopSourceReaper *fSourceReaper=NabuGopSourceReaper::create();
    uint64_t fFrames=0,fLastLit=0,fLastCenter=0,fCopyFailures=0,fDropped=0,fCompleted=0;
    unsigned nativeComplete=0,nativeDrop=0;
    void stopSourceMaps() {
        if (!fSourceReaper)return;
        IOMemoryMap *maps[4]{};
        for(unsigned i=0;i<4;i++){maps[i]=fSourceMaps[i].mapping;fSourceMaps[i]={};}
        fSourceReaper->finish(maps);fSourceReaper=nullptr;
    }
    ~NativeGOPDisplay() {stopSourceMaps();free(fComposition);free(fHalfToByte);free(fRow);}
    void program_hardware_gated();
    void failCurrentSwap(IOMFBSwapIORequest *);
    bool copyLayer(IOSurface *,const NabuGopRect &,const NabuGopRect &,bool,unsigned);
    void swap_complete_gated() {
        assert(prepareCalls==0 && completeCalls==0);
        auto *swap=at<IOMFBSwapIORequest *>(this,kCurrentSwap);assert(swap);
        for(unsigned i=0;i<4;i++) if(at<uint32_t>(swap,kSwapLayers)&(1u<<i)) {
            auto *surface=at<IOSurface *>(swap,0xac0+i*8);
            if(surface)assert(!surface->memory.prepared);
            at<IOSurface *>(this,0x1510+i*8)=surface;
            memcpy(family+0x11e9+i*16,swap->bytes+0x11+i*16,16);
            memcpy(family+0x1229+i*16,swap->bytes+0x51+i*16,16);
        }
        at<IOMFBSwapIORequest *>(this,kCurrentSwap)=nullptr;nativeComplete++;
    }
    void dropSwap_gated(IOMFBSwapIORequest *swap,bool) {
        auto *next=at<IOMFBSwapIORequest *>(swap,kSwapNext);
        auto **prev=at<IOMFBSwapIORequest **>(swap,kSwapPrev);
        assert(*prev==swap);
        if(next)at<IOMFBSwapIORequest **>(next,kSwapPrev)=prev;
        else at<IOMFBSwapIORequest **>(this,kSwapQueueLast)=prev;
        *prev=next;
        at<IOMFBSwapIORequest *>(this,kCurrentSwap)=nullptr;nativeDrop++;
    }
};
#include "../platform/nabu/macos26/display/nabu_cpu_present.inc"
static void layer(IOMFBSwapIORequest &r,unsigned i,IOSurface *s,NabuGopRect src,NabuGopRect dst) {
    at<uint32_t>(&r,kSwapLayers)|=1u<<i;at<IOSurface *>(&r,0xac0+i*8)=s;
    memcpy(r.bytes+0x11+i*16,&src,16);memcpy(r.bytes+0x51+i*16,&dst,16);
}
static void present(NativeGOPDisplay &d,IOMFBSwapIORequest &r) {
    at<IOMFBSwapIORequest *>(&d,kCurrentSwap)=&r;d.program_hardware_gated();
    assert(!at<IOMFBSwapIORequest *>(&d,kCurrentSwap));
}
int main() {
    failWorkerStart=true;assert(!NabuGopSourceReaper::create());assert(!workers);failWorkerStart=false;
    NativeGOPDisplay d;uint32_t scanout[12];for(auto &p:scanout)p=0xdeadbeef;
    d.fScanout=reinterpret_cast<uint8_t *>(scanout);
    at<IOMFBSwapIORequest **>(&d,kSwapQueueLast)=&at<IOMFBSwapIORequest *>(&d,kSwapQueueFirst);
    IOSurface root{4,2,20};root.memory.bytes.resize(40);
    for(unsigned y=0;y<2;y++) for(unsigned x=0;x<4;x++)at<uint32_t>(root.memory.bytes.data(),y*20+x*4)=0x00ff0000;
    IOSurface cursor{2,1,8};cursor.memory.bytes.resize(8);
    at<uint32_t>(cursor.memory.bytes.data(),0)=0;at<uint32_t>(cursor.memory.bytes.data(),4)=0xff00ff00;
    IOMFBSwapIORequest first;layer(first,0,&root,{0,0,4,2},{0,0,4,2});layer(first,1,&cursor,{0,0,2,1},{0,0,2,1});
    firstScanout=scanout;present(d,first);firstScanout=nullptr;assert(d.fFrames==1&&d.nativeComplete==1&&screenDisabled==1);
    // Execute the real trace helper with console disabled: native copy and
    // completion diagnostics must remain in RAM after first-frame takeover.
    for(const char *marker:{"console-disable-done", "scanout-copy-done", "complete=1"}) {
        bool found=false;
        for(const auto &line:traceLines) if(line.find(marker)!=std::string::npos &&
            line.find("console_disabled=1")!=std::string::npos &&
            line.find("t_ns=1000000")!=std::string::npos)found=true;
        assert(found);
    }
    nabuDisplayLog("NABU-DISPLAY: long=%s\n",std::string(2048,'x').c_str());
    assert(traceLines.back().size()<640 && traceLines.back().back()=='\n');
    assert(scanout[0]==0xffff0000&&scanout[1]==0xff00ff00&&scanout[4]==0xdeadbeef&&scanout[5]==0xdeadbeef);
    assert(d.fLastLit==8&&!root.memory.prepared&&!cursor.memory.prepared);
    // Cursor-only swap inherits root, restores old cursor pixels, and maps the retained native backing directly.
    IOMFBSwapIORequest moved;layer(moved,1,&cursor,{0,0,2,1},{2,1,2,1});present(d,moved);
    assert(d.fFrames==2&&scanout[1]==0xffff0000&&scanout[9]==0xff00ff00);
    assert(mapCalls==2 && mapReleases==0 && activeMaps==2); // root+cursor reuse, no in-gate unmap
    // Native null update clears cursor; it cannot erase the retained root plane.
    IOMFBSwapIORequest clear;layer(clear,1,nullptr,{},{ });present(d,clear);
    assert(d.fFrames==3&&scanout[9]==0xffff0000);
    // Mapping failure must preserve scanout, retire the unlinked request, and preserve the next queue entry.
    IOSurface unavailable{4,2,20};unavailable.memory.bytes.resize(40);unavailable.memory.fail=true;
    IOMFBSwapIORequest failed,next;
    at<IOMFBSwapIORequest *>(&d,kSwapQueueFirst)=&next;
    at<IOMFBSwapIORequest **>(&next,kSwapPrev)=&at<IOMFBSwapIORequest *>(&d,kSwapQueueFirst);
    at<IOMFBSwapIORequest **>(&d,kSwapQueueLast)=&at<IOMFBSwapIORequest *>(&next,kSwapNext);
    layer(failed,0,&unavailable,{0,0,4,2},{0,0,4,2});present(d,failed);
    assert(d.fFrames==3&&d.nativeComplete==3&&d.nativeDrop==1&&d.fCopyFailures==1);
    assert(at<IOMFBSwapIORequest *>(&d,kSwapQueueFirst)==&next);
    assert(at<IOMFBSwapIORequest **>(&next,kSwapPrev)==&at<IOMFBSwapIORequest *>(&d,kSwapQueueFirst));
    assert(scanout[0]==0xffff0000&&!root.memory.prepared);
    // An explicit later removal of all planes is a genuine black scene.
    at<IOMFBSwapIORequest *>(&d,kSwapQueueFirst)=nullptr;
    at<IOMFBSwapIORequest **>(&d,kSwapQueueLast)=&at<IOMFBSwapIORequest *>(&d,kSwapQueueFirst);
    IOMFBSwapIORequest black;layer(black,0,nullptr,{},{});present(d,black);
    assert(d.fFrames==4&&d.fLastLit==0&&scanout[0]==0&&scanout[9]==0);
    // Failed first frame never claims the console or reports a displayed swap.
    sheng_framebuffer.owner=SHENG_FB_BOOT;
    NativeGOPDisplay fresh;fresh.fScanout=reinterpret_cast<uint8_t *>(scanout);
    at<IOMFBSwapIORequest **>(&fresh,kSwapQueueLast)=&at<IOMFBSwapIORequest *>(&fresh,kSwapQueueFirst);
    IOMFBSwapIORequest badFirst;layer(badFirst,0,&unavailable,{0,0,4,2},{0,0,4,2});present(fresh,badFirst);
    assert(fresh.fFrames==0&&fresh.nativeComplete==0&&fresh.nativeDrop==1&&screenDisabled==1);
    assert(sheng_framebuffer.owner==SHENG_FB_BOOT&&!root.memory.prepared);
    // A failure after console handoff restores only a GUI-owned console;
    // a concurrent panic keeps ownership and is never overwritten/restored.
    for(int scenario:{1,2}) {
        sheng_framebuffer.owner=SHENG_FB_BOOT;sheng_framebuffer.writer=0;
        handoffFailure=scenario;
        NativeGOPDisplay rollback;rollback.fScanout=reinterpret_cast<uint8_t *>(scanout);
        at<IOMFBSwapIORequest **>(&rollback,kSwapQueueLast)=&at<IOMFBSwapIORequest *>(&rollback,kSwapQueueFirst);
        IOMFBSwapIORequest r;layer(r,0,&root,{0,0,4,2},{0,0,4,2});present(rollback,r);
        assert(!rollback.fFrames&&rollback.nativeDrop==1);
        assert(sheng_framebuffer.owner==(scenario==1?SHENG_FB_BOOT:SHENG_FB_PANIC));
        assert(screenEnabled==1);
    }
    handoffFailure=0;
    fresh.stopSourceMaps();d.stopSourceMaps();waitForReapers();
    assert(mapCalls>0 && activeMaps==0 && mapCalls==mapReleases && prepareCalls==0 && completeCalls==0);
    // A blocked VM unmap cannot grow references indefinitely or force stop
    // to wait for the reaper. Four shutdown slots remain reserved.
    blockRelease=true;releaseEntered=false;
    auto *reaper=NabuGopSourceReaper::create();uint8_t byte=1;
    assert(reaper->retire(new IOMemoryMap(&byte,1)));
    for(unsigned i=0;i<1000&&!releaseEntered;i++)std::this_thread::sleep_for(std::chrono::milliseconds(1));
    assert(releaseEntered);
    for(unsigned i=0;i<4;i++)assert(reaper->retire(new IOMemoryMap(&byte,1)));
    auto *refused=new IOMemoryMap(&byte,1);assert(!reaper->retire(refused));
    IOMemoryMap *held[4];for(auto &m:held)m=new IOMemoryMap(&byte,1);
    reaper->finish(held); // must return although worker's release is blocked
    for(auto *m:held)assert(!m);
    blockRelease=false;refused->release();waitForReapers();
    assert(!activeMaps&&mapCalls==mapReleases);
}
