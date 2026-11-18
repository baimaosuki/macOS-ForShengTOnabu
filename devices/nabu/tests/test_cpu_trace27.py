"""Run the production CPU source selector and bounded BGRA/FP16 diagnostics."""
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
DRIVER = ROOT / 'devices/nabu/drivers/GopFramebuffer'


def compile_run(tmp_path, name, source):
    path = tmp_path / (name + '.cpp')
    path.write_text(source)
    binary = tmp_path / name
    subprocess.run([shutil.which('clang++') or 'c++', '-std=c++17',
                    '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                    '-I', str(DRIVER), '-I', str(tmp_path), str(path),
                    '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)


def test_production_source_selection_and_throttle(tmp_path):
    production = (DRIVER / 'nabu_cpu_scene.inc').read_text()
    assert production.count('__asm__ volatile("dsb sy" ::: "memory");') == 2
    # Only the ARM device barriers are modeled on the host; source logic is intact.
    (tmp_path / 'cpu-scene-host.inc').write_text(production.replace(
        '__asm__ volatile("dsb sy" ::: "memory");', '__asm__ volatile("" ::: "memory");'))
    compile_run(tmp_path, 'selector', r'''
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stdio.h>
#include <string>
#include <vector>
#include "nabu_cpu_frame.h"
#include "nabu_cpu_trace.h"
#include "NativeHalfCodes.h"
static std::vector<std::string> messages;
static void IOLog(const char *fmt,...) { char b[1024];va_list a;va_start(a,fmt);vsnprintf(b,sizeof b,fmt,a);va_end(a);messages.emplace_back(b); }
static const int kIOReturnSuccess=0,kIOMapAnywhere=1,kIOMapReadOnly=2,kIOMapCopybackCache=4,kPEDisableScreen=1,kernel_task=0;
struct OSObject {int refs=1; virtual ~OSObject()=default;void retain(){++refs;}virtual void release(){assert(refs>0);if(!--refs)delete this;}virtual void *metaCast(const char*){return nullptr;}};
struct IOMemoryMap:OSObject {uint8_t *data;uint64_t bytes;IOMemoryMap(uint8_t*p,uint64_t n):data(p),bytes(n){} uint64_t getVirtualAddress(){return reinterpret_cast<uintptr_t>(data);}uint64_t getLength(){return bytes;}uint64_t getPhysicalAddress(){return 0x9c000000;} };
struct IOMemoryDescriptor:OSObject {std::vector<uint32_t> data;unsigned prepared=0,maps=0;IOMemoryDescriptor(uint32_t color):data(12,color){}uint64_t getLength(){return data.size()*4;}int prepare(){++prepared;return 0;}void complete(){assert(prepared);--prepared;}IOMemoryMap *createMappingInTask(int,int,int){++maps;return new IOMemoryMap(reinterpret_cast<uint8_t*>(data.data()),getLength());}};
struct Surface:OSObject {IOMemoryDescriptor memory;uint32_t id,format=0x42475241,planes=0;Surface(uint32_t i,uint32_t color):memory(color),id(i){}void *metaCast(const char*s)override{return !strcmp(s,"IOSurface")?this:nullptr;}};
#define OSSafeReleaseNULL(p) do{if(p){(p)->release();(p)=nullptr;}}while(0)
static void *IOMalloc(uint64_t n){return malloc(n);}static void IOFree(void*p,uint64_t){free(p);}
static IOMemoryDescriptor *native_legacy_surface_descriptor(OSObject*p){return &static_cast<Surface*>(p)->memory;}
static uint64_t native_legacy_surface_width(OSObject*){return 4;}static uint64_t native_legacy_surface_height(OSObject*){return 3;}
static uint64_t native_legacy_surface_stride(OSObject*p){return static_cast<Surface*>(p)->format==0x52476841 ? 32 : 16;}static uint32_t native_legacy_surface_format(OSObject*p){return static_cast<Surface*>(p)->format;}
static uint32_t native_legacy_surface_planes(OSObject*p){return static_cast<Surface*>(p)->planes;}static bool native_legacy_prepare_scanout(){return true;}
static void flush_dcache64(uint64_t,unsigned,bool){}static const uint8_t *halfTable(){static uint8_t table[32768];for(unsigned h=0;h<32768;++h)table[h]=native_half::code(h,false);return table;}
struct PE_Video {uint64_t v_depth=32,v_baseAddr=0x9c000000,v_rowBytes=16,v_width=4,v_height=3,v_rotate=0;};
static int PE_current_console(PE_Video*v){*v={};return 0;}
struct OriginalLegacySwapRequest {uint8_t bytes[4096]{};};
static constexpr unsigned kRequestMasks=1144,kRequestOptions=1156,kRequestDims=1168,kRequestSurfaces=3336,kRequestRect0=17,kRequestRect1=81;
struct Display {bool logicalPM=true,primary=true;IOMemoryMap *bootMapping;Surface *defaultSurface;
 Display(IOMemoryMap*m,Surface*d):bootMapping(m),defaultSurface(d){}void getDisplayDimensions(uint32_t*w,uint32_t*h){*w=4;*h=3;}
#include "cpu-scene-host.inc"
};
template<class T>void field(OriginalLegacySwapRequest&r,unsigned off,T v){memcpy(r.bytes+off,&v,sizeof v);}
static OriginalLegacySwapRequest request(uint32_t update,uint32_t disable,Surface*s=nullptr){
 OriginalLegacySwapRequest r;field(r,kRequestMasks,update);field(r,kRequestMasks+4,disable);field(r,kRequestDims,uint32_t(4));field(r,kRequestDims+4,uint32_t(3));
 field(r,kRequestSurfaces,static_cast<OSObject*>(s));field(r,kRequestRect0,NabuGopRect{0,0,4,3});field(r,kRequestRect1,NabuGopRect{0,0,4,3});return r;
}
int main(){
 uint32_t pixels[12]{};IOMemoryMap destination(reinterpret_cast<uint8_t*>(pixels),sizeof pixels);
 Surface defaultSurface(1,0xff0000ff),red(11,0xffff0000),green(22,0xff00ff00);
 Display d(&destination,&defaultSurface);
 auto blank=request(1,1,&defaultSurface);
 for(unsigned n=0;n<836;++n){assert(d.nabuPresentCpu(&blank));for(auto p:pixels)assert(p==0xff000000);}
 assert(d.nabuFrames==836 && !d.nabuSeenRgb && defaultSurface.memory.maps==0);
 unsigned summaries=0;for(const auto &log:messages)if(log.find("copied=")!=std::string::npos){++summaries;assert(log.find("active_mask=0 layer_count=0")!=std::string::npos);assert(log.find("slots_nonnull=1")!=std::string::npos);}assert(summaries==26);
 for(const auto &log:messages)assert(log.find("submitted_plane=")==std::string::npos);
 auto a=request(1,0,&red);assert(d.nabuPresentCpu(&a));assert(d.nabuPlanes[0].surface==&red);
 auto redMap=d.nabuPlanes[0].mapping->getVirtualAddress();for(auto p:pixels)assert(p==0xffff0000);
 auto b=request(1,0,&green);assert(d.nabuPresentCpu(&b));assert(d.nabuPlanes[0].surface==&green);
 auto greenMap=d.nabuPlanes[0].mapping->getVirtualAddress();assert(redMap!=greenMap);for(auto p:pixels)assert(p==0xff00ff00);
 // Retained plane uses the submitted backing on a later background-only update.
 auto retain=request(2,2);assert(d.nabuPresentCpu(&retain));
 assert(d.nabuPlanes[0].surface==&green);for(auto p:pixels)assert(p==0xff00ff00);
 assert(green.memory.maps==1);
 auto background=request(0x80000001,1); // explicitly disable plane 0 again
 assert(d.nabuPresentCpu(&background));for(auto p:pixels)assert(p==0xff000000);
 assert(defaultSurface.memory.maps==0 && red.memory.maps==1 && green.memory.maps==1);
 auto invalid=request(1,0,nullptr);assert(!d.nabuPresentCpu(&invalid));assert(d.nabuFrames==840);
 red.format=0x77343061;auto tenbit=request(1,0,&red);assert(!d.nabuPresentCpu(&tenbit));red.format=0x42475241;
 auto badMask=request(1,2,&red);assert(!d.nabuPresentCpu(&badMask));
 auto badOptions=request(1,0,&red);field(badOptions,kRequestOptions,uint32_t(1));assert(!d.nabuPresentCpu(&badOptions));
 auto badRect=request(1,0,&red);field(badRect,kRequestRect0,NabuGopRect{0,0,5,3});assert(!d.nabuPresentCpu(&badRect));
 red.planes=1;assert(!d.nabuPresentCpu(&tenbit));red.planes=0;
 assert(d.nabuFrames==840);
 Surface floating(33,0);floating.format=0x52476841;floating.memory.data.resize(24);
 uint16_t *rgba=reinterpret_cast<uint16_t*>(floating.memory.data.data());
 for(unsigned n=0;n<12;++n){rgba[n*4]=0x3c00;rgba[n*4+3]=0x3c00;}
 auto fp=request(1,0,&floating);assert(d.nabuPresentCpu(&fp));for(auto p:pixels)assert(p==0xffff0000);
 for(unsigned n=0;n<12;++n)rgba[n*4]=1;
 assert(d.nabuPresentCpu(&fp));for(auto p:pixels)assert(p==0xff000000);
 bool halfRaw=false;for(const auto &log:messages)if(log.find("fourcc=RGhA")!=std::string::npos && log.find("sampled_rgb_nonzero=0")!=std::string::npos && log.find("raw_rgb_nonzero=12")!=std::string::npos)halfRaw=true;
 assert(halfRaw);
 d.nabuFreeScene();assert(floating.refs==1 && !floating.memory.prepared);assert(red.refs==1 && green.refs==1);assert(!red.memory.prepared && !green.memory.prepared);
 printf("836 disabled-plane updates (nonnull default pointer ignored): default maps=0 rgb=0 alpha=12; dynamic id11/red -> id22/green mapped distinct backing; disable -> black; null enabled rejected\n");
}
''')


def test_bgra_half_samples_and_frame_policy(tmp_path):
    compile_run(tmp_path, 'samples', r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include "nabu_cpu_present.h"
#include "nabu_cpu_trace.h"
#include "NativeHalfCodes.h"
int main() {
    uint32_t bgra[]={0xff000000,0xffff0000,0x00000001,0xff00ff00};
    auto b=nabuCpuSampleSource(reinterpret_cast<uint8_t*>(bgra),sizeof(bgra),16,4,1,{0,0,4,1},nullptr);
    assert(b.count==4 && b.rgb==3 && b.alpha==3 && b.rawRgb==3);
    uint8_t table[65536]{};unsigned code=0;
    for(unsigned h=0;h<32768;++h){while(code<255 && h>=native_half::kLinear[code])++code;table[h]=code;}
    uint16_t half[]={0x3c00,0,0,0x3c00, 1,0,0,0x3c00};
    auto f=nabuCpuSampleSource(reinterpret_cast<uint8_t*>(half),sizeof(half),16,2,1,{0,0,2,1},table);
    assert(f.count==2 && f.rawRgb==2 && f.rgb==1 && f.alpha==2);
    half[0]=0;half[4]=0;
    f=nabuCpuSampleSource(reinterpret_cast<uint8_t*>(half),sizeof(half),16,2,1,{0,0,2,1},table);
    assert(f.count==2 && !f.rawRgb && !f.rgb && f.alpha==2);
    assert(!nabuCpuSampleSource(reinterpret_cast<uint8_t*>(half),4,16,2,1,{0,0,2,1},table).count);
    assert(!nabuCpuSampleSource(reinterpret_cast<uint8_t*>(half),sizeof(half),16,2,1,{-1,0,2,1},table).count);
    uint32_t grid[32*32]{};grid[0]=0xffff0000;grid[1023]=0xff00ff00;
    auto g=nabuCpuSampleSource(reinterpret_cast<uint8_t*>(grid),sizeof(grid),128,32,32,{0,0,32,32},nullptr);
    assert(g.count==256 && g.rgb==2);
    unsigned emitted=0;for(unsigned n=1;n<=836;++n)emitted+=nabuCpuTraceDue(n,false,false,false);
    assert(emitted==26);
    assert(nabuCpuTraceDue(11,true,false,false));
    assert(nabuCpuTraceDue(12,false,true,false));
    assert(nabuCpuTraceDue(13,false,false,true));
    assert(!nabuCpuTraceDue(14,false,false,false));
}
''')


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='nabu-cpu-trace-') as directory:
        work = Path(directory)
        test_production_source_selection_and_throttle(work)
        test_bgra_half_samples_and_frame_policy(work)
    print('production CPU trace/source-selection tests passed')
