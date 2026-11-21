#!/usr/bin/env python3
"""Execute Turnip's real entry-merge error paths and NABU fence watchdog."""
import argparse,json,subprocess,tempfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--mesa',type=Path,required=True);p.add_argument('--baseline-mesa',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
d=Path(__file__).resolve().parents[1]
def body(text,signature):
 at=text.index(signature);end=text.index('{',at)+1;depth=1
 while depth:depth+=(text[end]=='{')-(text[end]=='}');end+=1
 return text[at:end]
def methods(root):
 s=(root/'src/freedreno/vulkan/tu_cs.cc').read_text()
 return '\n'.join(body(s,sig) for sig in ['static struct tu_bo *\ntu_cs_current_bo(', 'static uint32_t\ntu_cs_get_offset(', 'static VkResult\ntu_cs_reserve_entry(', 'static void\ntu_cs_add_entry(', 'VkResult\ntu_cs_add_entries('])
peer=r'''
#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <cstdio>
#include "NabuGpuWatchdog.h"
#define unlikely(x) (x)
#define MAX2(a,b) ((a)>(b)?(a):(b))
#define disabled_assert(x) ((void)sizeof(x))
using VkResult=int;enum{VK_SUCCESS=0,VK_ERROR_DEVICE_LOST=-4,VK_ERROR_OUT_OF_HOST_MEMORY=-1,TU_CS_MODE_GROW=0};
struct tu_bo{void *map;};struct tu_bo_array{tu_bo **bos=nullptr;uint32_t bo_count=0,bo_capacity=0;};
struct tu_cs_entry{const tu_bo *bo;uint32_t size,offset;};
struct tu_cs{uint32_t *start=nullptr,*cur=nullptr;tu_bo_array read_write,read_only;tu_bo *refcount_bo=nullptr;bool writeable=false;
 uint32_t entry_count=0,entry_capacity=0;tu_cs_entry *entries=nullptr;VkResult status=0;int mode=TU_CS_MODE_GROW;};
static bool tu_cs_is_empty(const tu_cs *cs){return cs->cur==cs->start;}
static uint32_t tu_cs_get_size(const tu_cs *cs){return cs->cur-cs->start;}
'''
test=r'''
int main(){
 constexpr uint64_t limit=500000;NabuGpuWatchdog w;
 assert(!w.sample(100,1,1,limit));assert(!w.sample(5000000,2,1,limit));
 assert(!w.sample(5500000,2,1,limit));assert(w.sample(5500001,2,1,limit));
 assert(w.sample(5500002,3,1,limit));
 assert(!w.sample(5600000,3,2,limit));assert(!w.sample(6100000,3,2,limit));assert(w.sample(6100001,4,2,limit));
 assert(!w.sample(6200000,4,4,limit));assert(!w.sample(9000000,5,4,limit));
 assert(!w.sample(1,5,4,limit));assert(!w.sample(100,5,5,limit));
 // An old worker counted an idle scheduling gap against the first busy sample.
 uint64_t oldSince=100;assert(5000000-oldSince>limit);
 uint32_t sink[16]={},buffer[16]={};tu_cs failed,target;
 (void)buffer;
 failed.status=VK_ERROR_DEVICE_LOST;failed.cur=sink+1;target.status=0;
#ifdef OLD_MESA
 // The production old code follows its nonempty failure sink into a zero BO
 // array. ASan must reject this; the test runner checks the nonzero exit.
 (void)tu_cs_add_entries(&failed,&target);return 0;
#else
 assert(tu_cs_add_entries(&failed,&target)==VK_ERROR_DEVICE_LOST);
 assert(!failed.entries && !failed.entry_count);
 tu_cs dst;dst.start=buffer;dst.cur=buffer+4;target.status=VK_ERROR_OUT_OF_HOST_MEMORY;
 assert(tu_cs_add_entries(&dst,&target)==VK_ERROR_OUT_OF_HOST_MEMORY && !dst.entries);
 dst.status=VK_ERROR_DEVICE_LOST;assert(tu_cs_add_entries(&dst,&target)==VK_ERROR_DEVICE_LOST);
 dst.status=0;target.status=0;tu_bo bo{buffer};tu_bo *bos[]={&bo};dst.read_only={bos,1,1};
 dst.entries=(tu_cs_entry*)calloc(4,sizeof(tu_cs_entry));dst.entry_capacity=4;
 tu_cs_entry entry{&bo,8,32};target.entries=&entry;target.entry_count=target.entry_capacity=1;
 assert(!tu_cs_add_entries(&dst,&target) && dst.entry_count==2);
 assert(dst.entries[0].bo==&bo && dst.entries[0].size==16 && dst.entries[0].offset==0);
 assert(dst.entries[1].bo==&bo && dst.entries[1].size==8 && dst.entries[1].offset==32);
 assert(dst.start==dst.cur);free(dst.entries);
 puts("PASS actual Turnip failed destination/source propagation, valid merge, and watchdog idle-gap/progress/hang boundaries");
#endif
}
'''
a.output.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='nabu-render-failure-') as td:
 t=Path(td);src=t/'test.cpp';src.write_text(peer+methods(a.mesa)+test)
 command=['c++','-std=c++20','-O1','-g','-Wall','-Wextra','-Werror','-Wno-narrowing','-fsanitize=address,undefined','-I',str(d/'package/gpu/a640')]
 subprocess.run([*command,str(src),'-o',str(t/'test')],check=True)
 q=subprocess.run([str(t/'test')],capture_output=True,text=True);(a.output/'run.log').write_text(q.stdout+q.stderr);assert q.returncode==0,q.stderr;print(q.stdout,end='')
 if a.baseline_mesa:
  src.write_text(peer+methods(a.baseline_mesa).replace('assert(', 'disabled_assert(')+test)
  subprocess.run([*command,'-DOLD_MESA',str(src),'-o',str(t/'old')],check=True)
  q=subprocess.run([str(t/'old')],capture_output=True,text=True);(a.output/'old-run.log').write_text(q.stdout+q.stderr)
  assert q.returncode and any(x in q.stderr for x in ('AddressSanitizer','runtime error:')),q.stdout+q.stderr
  print('REPRODUCED old Turnip failed-sink empty-BO access under ASan/UBSan')
(a.output/'verification.json').write_text(json.dumps(dict(passed=True,production_turnip_methods=True,failed_cs_returned_before_bo_access=True,
 source_failure_no_destination_mutation=True,valid_entry_merge=True,watchdog_idle_gap_not_gpu_time=True,
 watchdog_progress_restarts=True,watchdog_new_submits_do_not_postpone_hang=True,watchdog_limit_us=500000,
 old_mesa_negative_control=bool(a.baseline_mesa),sanitizers=['ASan','UBSan'],hardware_tested=False),indent=2)+'\n')
