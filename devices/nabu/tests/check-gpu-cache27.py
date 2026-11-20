#!/usr/bin/env python3
"""Replay NC import cache ranges and the actual submit/completion maintenance helper."""
import argparse,json,subprocess,tempfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
d=Path(__file__).resolve().parents[1];g=d/'package/gpu/a640'
def extract(text,signature):
    start=text.index(signature);brace=text.index('{',start);end=brace+1;depth=1
    while depth:
        depth+=(text[end]=='{')-(text[end]=='}');end+=1
    return text[start:end]
cache=extract((g/'NativeAdrenoMemory.cpp').read_text(),'IOReturn NativeAdrenoMemory::maintainImported(')
cache=cache.replace('__asm__ volatile("dc ivac, %0" :: "r"(va):"memory");','hostCache(va,true);')
cache=cache.replace('__asm__ volatile("dc civac, %0" :: "r"(va):"memory");','hostCache(va,false);')
cache=cache.replace('__asm__ volatile("dsb sy" ::: "memory");','++barriers;')
submit=(g/'NativeAdrenoSubmit.cpp').read_text();helper=extract(submit,'static IOReturn nabuMaintainResource(')
assert 'nabuMaintainResource(vm_,job.resources[at].handle,true)' in submit
assert 'nabuMaintainResource(vm_,resource.handle,false)' in submit
prefix=r'''
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <vector>
using IOReturn=int;enum {kIOReturnSuccess=0,kIOReturnUnsupported=1,kIOReturnBadArgument=2};
namespace ranges {
static uint64_t gPhysBase=0x30000000,gPhysSize=0x100000,gVirtBase=0x100000000;
static std::vector<uint64_t> cleaned,invalidated;static unsigned barriers;
static void hostCache(uint64_t va,bool inv){(inv?invalidated:cleaned).push_back(va);}
struct Span{uint64_t offset,physical,bytes;};
struct NativeAdrenoMemory{struct {Span spans[2];size_t span_count;}storage_;
 IOReturn maintainImported(size_t,size_t,bool);
};
'''
tail=r'''
int test(){NativeAdrenoMemory m{{{{0,0x30000000,4096},{4096,0x30008000,4096}},2}};
 assert(!m.maintainImported(4000,200,false));assert(cleaned.size()==4 && cleaned[0]==0x100000f80 && cleaned.back()==0x100008040);
 assert(!m.maintainImported(4000,200,true));assert(invalidated==cleaned && barriers==2);
 m.storage_.spans[1].physical=0x40000000;assert(m.maintainImported(4096,4096,false)==kIOReturnUnsupported);return 0;}
}
struct NativeAdrenoMemory {bool maintained=true,wc=false;unsigned stores=0,acquires=0;int fail=0;
 bool maintainedImport()const{return maintained;}bool writeCombined()const{return wc;}size_t bytes()const{return 8192;}
 IOReturn publishForDevice(size_t o,size_t n){assert(!o && n==8192);++stores;return fail;}
 IOReturn acquireDeviceWrites(size_t o,size_t n){assert(!o && n==8192);++acquires;return fail;}
};
struct NativeAdrenoVm {NativeAdrenoMemory memory;const NativeAdrenoMemory *resourceBacking(uint64_t h){return h==1?&memory:nullptr;}};
'''
test=r'''
int main(){ranges::test();NativeAdrenoVm vm;
 assert(!nabuMaintainResource(&vm,1,false) && vm.memory.stores==1);
 assert(!nabuMaintainResource(&vm,1,true) && vm.memory.acquires==1);
 vm.memory.maintained=false;assert(!nabuMaintainResource(&vm,1,false) && vm.memory.stores==1);
 vm.memory.wc=true;assert(!nabuMaintainResource(&vm,1,false) && vm.memory.stores==2);
 assert(!nabuMaintainResource(&vm,1,true) && vm.memory.acquires==2);
 vm.memory.maintained=true;vm.memory.fail=7;assert(nabuMaintainResource(&vm,1,false)==7);
 assert(nabuMaintainResource(&vm,2,false)==kIOReturnBadArgument);
 puts("PASS production NC import cache range validation and submit/completion maintenance hooks; cache instructions modeled");}
'''
a.output.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='nabu-cache-range-') as temp:
    root=Path(temp);cpp=root/'test.cpp';cpp.write_text(prefix+cache+tail+helper+test)
    subprocess.run(['c++','-std=c++17','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(cpp),'-o',str(root/'test')],check=True)
    q=subprocess.run([str(root/'test')],capture_output=True,text=True,check=True);print(q.stdout,end='');(a.output/'run.log').write_text(q.stdout)
(a.output/'verification.json').write_text(json.dumps(dict(passed=True,production_cache_range_body=True,
    production_submission_hooks=True,cache_instructions_modeled=True,hardware_tested=False),indent=2)+'\n')
