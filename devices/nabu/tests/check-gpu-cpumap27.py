#!/usr/bin/env python3
"""Execute the actual V2 map method with WC/WB descriptor peers."""
import argparse,json,subprocess,tempfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
g=Path(__file__).resolve().parents[1]/'package/gpu/a640';t=(g/'NativeAdrenoPublish.cpp').read_text();start=t.index('IOReturn AGXDeviceUserClient::mapCpu(');end=t.index('{',start)+1;depth=1
while depth:depth+=(t[end]=='{')-(t[end]=='}');end+=1
method=t[start:end]
peer=r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
using IOReturn=int;using IOOptionBits=unsigned;
enum{kIOReturnSuccess=0,kIOReturnBadArgument=1,kIOReturnNotReady=2,kIOReturnNoResources=3,kIOReturnNoMemory=4};
enum{kIOMapUnique=1,kIOMapAnywhere=2,kIOMapWriteCombineCache=0x400,kIOMapCopybackCache=0x500,NativeAdrenoCpuMapVersion=2};
struct NativeAdrenoAbiBoCpuMapV2{uint32_t version=2,flags=0;uint64_t handle=1,address=0;};
struct NativeAdrenoAbiBoCpuMapV2Reply{uint64_t map_id,address,bytes;};
struct NativeAdrenoAbiBoCpuMap{uint64_t handle;};struct NativeAdrenoAbiBoCpuMapReply{uint32_t token;uint64_t bytes;};
struct NativeAdrenoAbiBoCpuUnmap{uint64_t handle;uint32_t token,reserved;};
static void *current_task(){return (void*)1;}static void IOLockLock(void*){}static void IOLockUnlock(void*){}
static struct{void *lock=nullptr;bool failed=false;}g;
static bool failMap=false;static unsigned optionsSeen=0,unleases=0,liveMaps=0;
struct IOMemoryMap{uint64_t address;IOMemoryMap(uint64_t a):address(a){++liveMaps;}uint64_t getAddress(){return address;}uint64_t getLength(){return 8192;}
 int unmap(){return 0;}void release(){--liveMaps;delete this;}};
struct IOMemoryDescriptor{IOMemoryMap *createMappingInTask(void *task,uint64_t at,unsigned opts,uint64_t off,uint64_t bytes){
 assert(task==current_task() && !off && bytes==8192);optionsSeen=opts;return failMap?nullptr:new IOMemoryMap(at?at:0x12340000);}};
struct NativeAdrenoMemory{bool wc=true;IOMemoryDescriptor d;unsigned snaps=0;IOMemoryDescriptor *descriptor(){return &d;}bool writeCombined(){return wc;}
 void cacheSnapshot(const char*,size_t,uint64_t a){assert(a==0x12340000 || a==0x45670000);++snaps;}void quarantine(){}};
struct Core{NativeAdrenoMemory b;int cpuMap(NativeAdrenoAbiBoCpuMap *,NativeAdrenoAbiBoCpuMapReply *r,bool){*r={7,8192};return 0;}
 NativeAdrenoMemory *memoryOf(uint64_t h){return h==1?&b:nullptr;}int cpuUnmap(NativeAdrenoAbiBoCpuUnmap*,bool){++unleases;return 0;}};
#define IONewZero(T,n) new T[n]()
struct AGXDeviceUserClient{bool live_=true,closing_=false,kernel_=false;unsigned viewTeardownStatus_=0;uint64_t nextCpuMapId_=0;
 struct CpuMapping{uint64_t handle=0,map_id=0;uint32_t token=0;IOMemoryMap *map=nullptr;};
 static constexpr unsigned MaxViews=2;CpuMapping *cpuMappings_=nullptr;Core c;Core *core_=&c;
 IOReturn mapCpu(const NativeAdrenoAbiBoCpuMapV2 &,NativeAdrenoAbiBoCpuMapV2Reply &);
 ~AGXDeviceUserClient(){if(cpuMappings_){for(unsigned i=0;i<MaxViews;++i)if(cpuMappings_[i].map)cpuMappings_[i].map->release();delete[] cpuMappings_;}}
};
'''
test=r'''
int main(){
 for(bool wc:{false,true})for(uint64_t addr:{UINT64_C(0),UINT64_C(0x45670000)}){
  AGXDeviceUserClient c;c.c.b.wc=wc;NativeAdrenoAbiBoCpuMapV2 r;r.address=addr;NativeAdrenoAbiBoCpuMapV2Reply out{};
  assert(!c.mapCpu(r,out) && out.bytes==8192 && out.map_id==1);
  assert(optionsSeen==(kIOMapUnique|(addr?0:kIOMapAnywhere)|(wc?kIOMapWriteCombineCache:kIOMapCopybackCache)));
 }
 assert(!liveMaps);AGXDeviceUserClient c;NativeAdrenoAbiBoCpuMapV2 r;NativeAdrenoAbiBoCpuMapV2Reply out{};
 failMap=true;assert(c.mapCpu(r,out)==kIOReturnNoMemory && unleases==1 && !liveMaps);
 r.flags=1;assert(c.mapCpu(r,out)==kIOReturnBadArgument);
 puts("PASS production V2 WC/WB explicit mapping flags, fixed/anywhere addresses, lease rollback and retained-map diagnostics");
}
'''
a.output.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='nabu-cpumap-') as td:
 r=Path(td);cpp=r/'test.cpp';cpp.write_text('#include <initializer_list>\n'+peer+method+test)
 subprocess.run(['c++','-std=c++17','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(cpp),'-o',str(r/'test')],check=True)
 q=subprocess.run([str(r/'test')],capture_output=True,text=True);(a.output/'run.log').write_text(q.stdout+q.stderr);assert not q.returncode,q.stderr;print(q.stdout,end='')
(a.output/'verification.json').write_text(json.dumps(dict(passed=True,actual_v2_map_body=True,explicit_wc_wb=True,map_lifetime_and_rollback=True,sanitizers=['ASan','UBSan'],hardware_tested=False),indent=2)+'\n')
