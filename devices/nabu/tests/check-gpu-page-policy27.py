#!/usr/bin/env python3
"""Execute BO page-policy logic and compile its ABI against selected XNU types."""
import argparse,hashlib,json,re,subprocess,tempfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--xnu',type=Path,required=True);a=p.parse_args()
g=Path(__file__).resolve().parents[1]/'package/gpu/a640'
pmap=a.xnu/'osfmk/vm/pmap.h';mem=a.xnu/'osfmk/mach/memory_object_types.h'
pt=pmap.read_text();mt=mem.read_text()
enum=re.search(r'__enum_decl\(unified_page_list_type_t, uint8_t, \{.*?\}\);',pt,re.S).group()
layout=re.search(r'typedef struct \{\s*union \{\s*/\* Base address and size.*?\} unified_page_list_t;',pt,re.S).group()
upl=re.search(r'struct upl_page_info \{.*?^\};',mt,re.S|re.M).group()
abi='''#include <cstdint>
#include <cstddef>
using ppnum_t=uint32_t;using vm_page_t=void *;
#define XNU_KERNEL_PRIVATE 1
#ifndef __enum_decl
#define __enum_decl(N,T,...) enum N:T __VA_ARGS__
#endif
'''+upl+'\nusing upl_page_info_array_t=upl_page_info *;\n'+enum+'\n'+layout+'''
#include "NabuGpuPagePolicy.h"
static_assert(sizeof(upl_page_info)==sizeof(NabuGpuPmapPageInfo));
static_assert(offsetof(upl_page_info,phys_addr)==offsetof(NabuGpuPmapPageInfo,physicalPage));
static_assert(sizeof(unified_page_list_t)==sizeof(NabuGpuPmapPageList));
static_assert(offsetof(unified_page_list_t,upl.upl_info)==offsetof(NabuGpuPmapPageList,upl.pages));
static_assert(offsetof(unified_page_list_t,upl.upl_size)==offsetof(NabuGpuPmapPageList,upl.count));
static_assert(offsetof(unified_page_list_t,type)==offsetof(NabuGpuPmapPageList,type));
static_assert(UNIFIED_PAGE_LIST_TYPE_UPL_ARRAY==0);
'''
test=r'''
#include <cassert>
#include <map>
#include <vector>
#include <cstdio>
struct Span {uint64_t offset,physical;size_t bytes;};
struct Page {unsigned attr=3;uint32_t cpu=0,dram=0;bool dirty=false;};
struct Ops {
 std::map<uint32_t,Page> pages;std::vector<unsigned> batches;unsigned syncs=0;bool fail=false;
 unsigned attribute(uint32_t p){return pages[p].attr;}
 void batch(const NabuGpuPmapPageList *l,unsigned target){
  assert(l->type==0 && l->upl.count<=128);batches.push_back(l->upl.count);
  for(unsigned i=0;i<l->upl.count;++i){assert(!l->upl.pages[i].flags);if(!fail)pages[l->upl.pages[i].physicalPage].attr=target;}
 }
 void sync(uint32_t p){auto &x=pages[p];if(x.dirty){x.dram=x.cpu;x.dirty=false;}++syncs;}
};
int main(){
 uint32_t changed=99;Ops o;const uint64_t base=0x100000000,ram=0x40000000;Span s{0,base,130*4096};
 // Model the exact observed failure: new user writes in CPU dirty lines,
 // GPU/kernel NC reads old zero DRAM. A PTE change alone does not publish it.
 o.pages[base>>12]={3,0x70460001,0,true};
 assert(!o.pages[base>>12].dram);
 auto apply=[&](unsigned mode){return nabuGpuApplyPagePolicy(&s,1,base,ram,mode,o,&changed);};
 assert(apply(1)==NabuGpuPagePolicyResult::Ok && changed==130);
 assert((o.batches==std::vector<unsigned>{128,2}) && o.syncs==130);
 assert(o.pages[base>>12].dram==0x70460001 && !o.pages[base>>12].dirty);
 o.batches.clear();o.syncs=0;assert(apply(1)==NabuGpuPagePolicyResult::Ok && !changed && o.batches.empty() && !o.syncs);
 assert(apply(3)==NabuGpuPagePolicyResult::Ok && changed==130 && o.pages[base>>12].attr==3);
 // Whole request validation must precede any mutation, including a bad later span.
 const Span bad[][2]={{{0,base,4096},{8192,base+4096,4096}},{{0,base,4096},{4096,base+1,4096}},
  {{0,base,4096},{4096,base+ram,4096}},{{0,base,4096},{4096,base+4096,0}}};
 for(auto &spans:bad){Ops t;assert(nabuGpuApplyPagePolicy(spans,2,base,ram,1,t,&changed)==NabuGpuPagePolicyResult::Argument && t.batches.empty() && !changed);}
 Span trunc{0,UINT64_C(1)<<44,4096};Ops t;
 assert(nabuGpuApplyPagePolicy(&trunc,1,trunc.physical,4096,1,t,&changed)==NabuGpuPagePolicyResult::Argument && t.batches.empty());
 assert(nabuGpuApplyPagePolicy<Span>(nullptr,1,base,ram,1,t,&changed)==NabuGpuPagePolicyResult::Argument);
 t.fail=true;Span one{0,base,4096};
 assert(nabuGpuApplyPagePolicy(&one,1,base,ram,1,t,&changed)==NabuGpuPagePolicyResult::Readback && changed==1 && t.syncs==1);
 puts("PASS actual XNU private ABI, high PA, 128-page batches, WB dirty-line publication model, NC no-op, restore, preflight and failed readback");
}
'''
a.output.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='nabu-page-policy-') as td:
 r=Path(td);cpp=r/'test.cpp';cpp.write_text(abi+test)
 cmd=['c++','-std=c++17','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',str(g),str(cpp),'-o',str(r/'test')]
 subprocess.run(cmd,check=True);q=subprocess.run([str(r/'test')],capture_output=True,text=True,check=True)
 (a.output/'run.log').write_text(q.stdout);print(q.stdout,end='')
 (a.output/'selected-xnu-types.cpp').write_text(abi)
(a.output/'verification.json').write_text(json.dumps(dict(passed=True,selected_kernel_headers={str(f.relative_to(a.xnu)):hashlib.sha256(f.read_bytes()).hexdigest() for f in [pmap,mem]},private_abi_compiled=True,production_page_policy=True,cache_instructions_modeled=True,sanitizers=['ASan','UBSan'],hardware_tested=False),indent=2)+'\n')
