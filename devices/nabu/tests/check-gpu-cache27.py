#!/usr/bin/env python3
"""Run actual publication/acquire and all-IB/all-resource submit helpers."""
import argparse,json,subprocess,tempfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
g=Path(__file__).resolve().parents[1]/'package/gpu/a640'
def body(t,s):
 start=t.index(s);end=t.index('{',start)+1;depth=1
 while depth:depth+=(t[end]=='{')-(t[end]=='}');end+=1
 return t[start:end]
m=(g/'NativeAdrenoMemory.cpp').read_text();sub=(g/'NativeAdrenoSubmit.cpp').read_text()
methods='\n'.join(body(m,'IOReturn NativeAdrenoMemory::'+x+'(') for x in ['maintainCpuRange','publishForDevice','acquireDeviceWrites'])
methods=methods.replace('__asm__ volatile("dc ivac, %0" :: "r"(va):"memory");','hostCache(va,true);').replace('__asm__ volatile("dc civac, %0" :: "r"(va):"memory");','hostCache(va,false);').replace('__asm__ volatile("dsb sy" ::: "memory");','++barriers;')
helper='\n'.join(body(sub,'static IOReturn '+n+'(') for n in ['nabuMaintainResource','nabuPublishIb','nabuPublishJobRanges'])
for signature in ['IOReturn NativeAdrenoSubmit::submit(', 'IOReturn NativeAdrenoSubmit::submitResources(']:
 code=body(sub,signature);assert code.index('nabuPublishJobRanges(')<code.index('ring_->submitFramed(')
assert 'nabuMaintainResource(vm_,r.handle,true,r.offset,r.bytes)' in sub
prefix=r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <vector>
using IOReturn=int;enum{kIOReturnSuccess=0,kIOReturnUnsupported=1,kIOReturnBadArgument=2,kIOReturnNotReady=3};
static uint64_t gPhysBase=0x30000000,gPhysSize=0x100000,gVirtBase=0x100000000;
static unsigned barriers=0;struct Op{uint64_t va;bool inv;};static std::vector<Op> operations;
static uint32_t cpu=0x70460001,dram=0;static bool dirty=true;
static void hostCache(uint64_t va,bool inv){operations.push_back({va,inv});if(!inv && dirty){dram=cpu;dirty=false;}}
struct Span{uint64_t offset,physical,bytes;};
struct NativeAdrenoMemory {
 struct{Span spans[2]={{0,0x30000000,4096},{4096,0x30008000,4096}};size_t span_count=2,bytes=8192;bool prepared=true,quarantined=false,write_combined=true;void *descriptor=(void*)1;}storage_;
 bool imported=false;int ensureFail=0;unsigned checks=0,snapshots=0;std::vector<Op> ranges;
 bool backed(){return true;}bool maintainedImport()const{return imported;}
 bool writeCombined()const{return storage_.write_combined;}size_t bytes()const{return storage_.bytes;}
 IOReturn ensureOwnedPageCache(){++checks;return ensureFail;}
 void cacheSnapshot(const char*,size_t=0,uint64_t=0){++snapshots;}
 IOReturn maintainCpuRange(size_t,size_t,bool);IOReturn publishForDevice(size_t,size_t);IOReturn acquireDeviceWrites(size_t,size_t);
};
'''
tail=r'''
struct NativeAdrenoSubmitBuffer{uint64_t handle,iova;size_t bytes;};
struct NativeAdrenoSubmitResource{uint64_t handle;size_t offset,bytes;unsigned access=1;bool owns_reference=false;};
struct NativeAdrenoVm{
 NativeAdrenoMemory memories[3];
 const NativeAdrenoMemory *resourceBacking(uint64_t h){return h>=1 && h<=3?&memories[h-1]:nullptr;}
 IOReturn describe(uint64_t h,uint64_t *base,size_t *bytes,unsigned *p){if(!resourceBacking(h))return 2;*base=0x20000000+h*0x10000;*bytes=8192;*p=3;return 0;}
 IOReturn peekWords(uint64_t,uint32_t *out,size_t n){for(size_t i=0;i<n;++i)out[i]=dram;return 0;}
};
static void IOLog(const char*,...){}
'''
test=r'''
int main(){
 NativeAdrenoMemory m;
 // Actual publish body must clean even when ensureOwnedPageCache changes zero
 // attributes. It also handles non-page-aligned ranges crossing physical spans.
 assert(!m.publishForDevice(4000,200) && m.checks==1 && dram==cpu && !dirty);
 assert(operations.size()==4 && operations[0].va==0x100000f80 && operations.back().va==0x100008040);
 assert(!m.acquireDeviceWrites(4000,200));for(size_t i=4;i<8;++i)assert(operations[i].inv);
 operations.clear();m.storage_.spans[1].physical=0x40000000;
 assert(m.publishForDevice(4000,200)==1 && operations.empty()); // validate all before mutation
 m.storage_.spans[1].physical=0x30008000;m.ensureFail=7;
 assert(m.publishForDevice(0,64)==7 && operations.empty());m.ensureFail=0;
 m.storage_.write_combined=false;assert(!m.publishForDevice(0,64) && operations.empty());
 m.imported=true;assert(!m.publishForDevice(0,64) && operations.size()==1);
 NativeAdrenoVm vm;NativeAdrenoSubmitBuffer ibs[]={{1,0x20010000+128,64},{2,0x20020000+256,128}};
 NativeAdrenoSubmitResource resources[]={{1,1024,64},{1,2048,64},{3,3072,128}};
 dirty=true;dram=0;operations.clear();
 assert(!nabuPublishJobRanges(&vm,ibs,2,resources,3));
 assert(operations.size()==1+2+1+1+2 && vm.memories[0].checks==3 && vm.memories[1].checks==1 && vm.memories[2].checks==1);
 assert(dram==cpu && !dirty); // first IB diagnostic observes actual pre/post publication
 // Direct/legacy path uses the identical complete-IB walker, even without resources.
 operations.clear();assert(!nabuPublishJobRanges(&vm,ibs,2,nullptr,0) && operations.size()==3);
 vm.memories[1].ensureFail=9;operations.clear();
 assert(nabuPublishJobRanges(&vm,ibs,2,resources,3)==9 && operations.size()==1);
 assert(nabuMaintainResource(&vm,9,false)==2);
 assert(nabuMaintainResource(&vm,1,false,8190,4)==2);
 operations.clear();assert(!nabuMaintainResource(&vm,1,true,64,64) && operations.size()==1 && operations[0].inv);
 puts("PASS actual owned-WC publish even unchanged attributes; exact CIVAC/IVAC ranges; all IBs and duplicate-handle resource intervals; legacy/V2 shared path; failure prevents ring");
}
'''
a.output.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='nabu-cache-host-') as td:
 r=Path(td);cpp=r/'test.cpp';cpp.write_text(prefix+methods+tail+helper+test)
 subprocess.run(['c++','-std=c++17','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(cpp),'-o',str(r/'test')],check=True)
 q=subprocess.run([str(r/'test')],capture_output=True,text=True);(a.output/'run.log').write_text(q.stdout+q.stderr)
 assert q.returncode==0,q.stdout+q.stderr
 print(q.stdout,end='')
(a.output/'verification.json').write_text(json.dumps(dict(passed=True,actual_publish_acquire_bodies=True,owned_wc_always_civac=True,all_ibs_and_resource_ranges=True,duplicate_handles_maintained=True,completion_invalidate=True,legacy_v2_share_publish=True,failed_publish_before_ring=True,cache_instructions_modeled=True,sanitizers=['ASan','UBSan'],hardware_tested=False),indent=2)+'\n')
