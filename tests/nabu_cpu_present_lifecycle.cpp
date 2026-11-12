#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <vector>
#include "../platform/nabu/macos26/display/nabu_cpu_present.h"
#include "../platform/sheng/macos26/log/framebuffer_owner.h"
sheng_framebuffer_owner sheng_framebuffer{};
volatile uint64_t sheng_panel_ready = 1, sheng_status_pixels = 1;
static constexpr int kIOReturnSuccess=0,kIOMapAnywhere=0,kIOMapReadOnly=0,kPEDisableScreen=0;
static constexpr uint32_t kFormatBGRA=0x42475241,kFormatRGhA=0x52476841;
static constexpr size_t kCurrentSwap=6032,kSwapQueueFirst=6040,kSwapQueueLast=6048,kSwapLayers=824,kSwapNext=3192,kSwapPrev=3200;
static int kernel_task=0;
static unsigned screenDisabled=0;
static void *IOMalloc(uint64_t n) {return malloc(n);}
static void IOFree(void *p,uint64_t) {free(p);}
static void IOLog(const char *,...) {}
static void PE_initialize_console(void *,int) {screenDisabled++;}
static void sheng_boot_checkpoint(int) {}
static uint8_t halfToByte(uint16_t half) {return half==0x3c00?255:0;}
template<typename T> static T &at(void *p,size_t n) {return *reinterpret_cast<T *>(static_cast<uint8_t *>(p)+n);}
struct IOMemoryMap {
    uintptr_t data; uint64_t size;
    uint64_t getLength() {return size;}
    uintptr_t getVirtualAddress() {return data;}
    void release() {delete this;}
};
struct IOMemoryDescriptor {
    std::vector<uint8_t> bytes;
    bool fail=false, mapFail=false; unsigned prepared=0;
    uint64_t getLength() {return bytes.size();}
    int prepare() {if (fail) return -1;prepared++;return 0;}
    void complete() {assert(prepared);prepared--;}
    IOMemoryMap *createMappingInTask(int,int,int,uint64_t off,uint64_t n) {
        assert(prepared);return mapFail?nullptr:new IOMemoryMap{reinterpret_cast<uintptr_t>(bytes.data()+off),n};
    }
    uint64_t readBytes(uint64_t off,void *out,uint64_t n) {assert(prepared);memcpy(out,bytes.data()+off,n);return n;}
};
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
    uint64_t fFrames=0,fLastLit=0,fLastCenter=0,fCopyFailures=0,fDropped=0,fCompleted=0;
    unsigned nativeComplete=0,nativeDrop=0;
    ~NativeGOPDisplay() {free(fComposition);free(fHalfToByte);free(fRow);}
    void program_hardware_gated();
    void failCurrentSwap(IOMFBSwapIORequest *);
    bool copyLayer(IOSurface *,const NabuGopRect &,const NabuGopRect &,bool);
    void swap_complete_gated() {
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
    NativeGOPDisplay d;uint32_t scanout[12];for(auto &p:scanout)p=0xdeadbeef;
    d.fScanout=reinterpret_cast<uint8_t *>(scanout);
    at<IOMFBSwapIORequest **>(&d,kSwapQueueLast)=&at<IOMFBSwapIORequest *>(&d,kSwapQueueFirst);
    IOSurface root{4,2,20};root.memory.bytes.resize(40);
    for(unsigned y=0;y<2;y++) for(unsigned x=0;x<4;x++)at<uint32_t>(root.memory.bytes.data(),y*20+x*4)=0x00ff0000;
    IOSurface cursor{2,1,8};cursor.memory.bytes.resize(8);
    at<uint32_t>(cursor.memory.bytes.data(),0)=0;at<uint32_t>(cursor.memory.bytes.data(),4)=0xff00ff00;
    IOMFBSwapIORequest first;layer(first,0,&root,{0,0,4,2},{0,0,4,2});layer(first,1,&cursor,{0,0,2,1},{0,0,2,1});
    present(d,first);assert(d.fFrames==1&&d.nativeComplete==1&&screenDisabled==1);
    assert(scanout[0]==0xffff0000&&scanout[1]==0xff00ff00&&scanout[4]==0xdeadbeef&&scanout[5]==0xdeadbeef);
    assert(d.fLastLit==8&&!root.memory.prepared&&!cursor.memory.prepared);
    // Cursor-only swap inherits root, restores old cursor pixels, and can fall back to readBytes.
    root.memory.mapFail=true;IOMFBSwapIORequest moved;layer(moved,1,&cursor,{0,0,2,1},{2,1,2,1});present(d,moved);
    assert(d.fFrames==2&&scanout[1]==0xffff0000&&scanout[9]==0xff00ff00);
    // Native null update clears cursor; it cannot erase the retained root plane.
    IOMFBSwapIORequest clear;layer(clear,1,nullptr,{},{ });present(d,clear);
    assert(d.fFrames==3&&scanout[9]==0xffff0000);
    // Source failure must preserve scanout, retire the unlinked request, and preserve the next queue entry.
    root.memory.fail=true;IOMFBSwapIORequest failed,next;
    at<IOMFBSwapIORequest *>(&d,kSwapQueueFirst)=&next;
    at<IOMFBSwapIORequest **>(&next,kSwapPrev)=&at<IOMFBSwapIORequest *>(&d,kSwapQueueFirst);
    at<IOMFBSwapIORequest **>(&d,kSwapQueueLast)=&at<IOMFBSwapIORequest *>(&next,kSwapNext);
    layer(failed,0,&root,{0,0,4,2},{0,0,4,2});present(d,failed);
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
    IOMFBSwapIORequest badFirst;layer(badFirst,0,&root,{0,0,4,2},{0,0,4,2});present(fresh,badFirst);
    assert(fresh.fFrames==0&&fresh.nativeComplete==0&&fresh.nativeDrop==1&&screenDisabled==1);
    assert(sheng_framebuffer.owner==SHENG_FB_BOOT&&!root.memory.prepared);
}
