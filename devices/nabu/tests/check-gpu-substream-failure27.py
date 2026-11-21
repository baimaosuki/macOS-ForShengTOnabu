#!/usr/bin/env python3
"""Run actual Turnip failed substream constructors and dynamic-state macros."""
import argparse,json,subprocess,tempfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--mesa',type=Path,required=True);p.add_argument('--baseline-mesa',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
def body(t,sig):
 start=t.index(sig);at=t.index('{',start)+1;depth=1
 while depth:depth+=(t[at]=='{')-(t[at]=='}');at+=1
 return t[start:at]
def methods(root):
 cc=(root/'src/freedreno/vulkan/tu_cs.cc').read_text();h=(root/'src/freedreno/vulkan/tu_cs.h').read_text()
 return '\n'.join([body(cc,'void\ntu_cs_init_external('),body(h,'static inline void\ntu_cs_fail('),
  body(cc,'static struct tu_bo *\ntu_cs_current_bo('),body(cc,'static uint32_t\ntu_cs_get_offset('),
  body(cc,'VkResult\ntu_cs_alloc('),body(cc,'VkResult\ntu_cs_begin_sub_stream_aligned('),
  body(h,'static inline struct tu_draw_state\ntu_cs_draw_state(')])
def macros(root):
 t=(root/'src/freedreno/vulkan/tu_pipeline.cc').read_text();at=t.index('#define DRAW_STATE_COND',t.index('tu_emit_draw_state(struct'));end=t.index('#define DRAW_STATE(name',at)
 return t[at:end]
peer=r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <algorithm>
#define unlikely(x) (x)
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
using VkResult=int;enum{VK_SUCCESS=0,VK_ERROR_DEVICE_LOST=-4,VK_ERROR_OUT_OF_HOST_MEMORY=-1,VK_ERROR_OUT_OF_DEVICE_MEMORY=-2};
enum{TU_CS_MODE_GROW,TU_CS_MODE_EXTERNAL,TU_CS_MODE_SUB_STREAM};
#define TU_CS_FAIL_SINK_SIZE 16384
uint32_t tu_cs_fail_sink[TU_CS_FAIL_SINK_SIZE];
struct Info{unsigned marker;};struct Physical{Info *info;};struct tu_device{Physical *physical_device;};
struct tu_bo{void *map;uint64_t iova;size_t size;};
struct tu_bo_array{tu_bo **bos=nullptr;uint32_t bo_count=0,bo_capacity=0;uint32_t *start=nullptr;};
struct tu_cs{uint32_t *start=nullptr,*cur=nullptr,*reserved_end=nullptr,*end=nullptr;const char *name=nullptr;tu_device *device=nullptr;
 int mode=0;bool writeable=false;uint32_t next_bo_size=0;VkResult status=0;tu_bo_array read_only,read_write;tu_bo *refcount_bo=nullptr;uint64_t external_iova=0;};
struct tu_cs_memory{uint32_t *map;uint64_t iova;bool writeable;};
struct tu_draw_state{uint64_t iova;uint16_t size;bool writeable;};
static unsigned align(unsigned x,unsigned a){return (x+a-1)/a*a;}
static uint32_t tu_cs_get_space(const tu_cs *cs){return cs->status?0:cs->end-cs->cur;}
static uint64_t tu_cs_get_cur_iova(const tu_cs *cs){return cs->refcount_bo->iova+4*(cs->cur-(uint32_t*)cs->refcount_bo->map);}
static void tu_cs_begin(tu_cs *cs){assert(cs->mode==TU_CS_MODE_EXTERNAL);}
static VkResult injected=0;
static void tu_cs_fail(tu_cs*,VkResult);
static VkResult tu_cs_reserve_space(tu_cs *cs,uint32_t words){if(cs->status)return cs->status;if(injected){tu_cs_fail(cs,injected);return injected;}cs->reserved_end=cs->cur+words;return 0;}
'''
wrapper=r'''
static VkResult tu_cs_begin_sub_stream(tu_cs *cs,uint32_t n,tu_cs *sub){return tu_cs_begin_sub_stream_aligned(cs,n,1,sub);}
enum chip{A6XX=6};struct vk{int error=0;struct{int unused;}dynamic_graphics_state;};
struct tu_cmd_buffer{struct vk vk;tu_device *device;tu_cs sub_cs;struct{unsigned pipeline_draw_states=0,dirty=0;bool has_fdm=false,per_layer_viewport=false;tu_draw_state dynamic_state[2]{};}state;};
static int emitCalls=0;
template<chip>static unsigned tu6_rast_size(tu_device*,int){return 16;}
template<chip>static void tu6_emit_rast(tu_cs *cs,int){++emitCalls;assert(cs->device->physical_device->info->marker==640);}
static void vk_command_buffer_set_error(struct vk *vk,VkResult status){vk->error=status;}
static tu_draw_state tu_cs_end_draw_state(tu_cs*,tu_cs *cs){return cs->status?tu_draw_state{}:tu_draw_state{0x1234,16,false};}
#define EMIT_STATE(name) true
'''
test=r'''
static uint32_t draw(tu_cmd_buffer *cmd){uint32_t dirty_draw_states=0;tu_cs cs;constexpr chip CHIP=A6XX;DRAW_STATE_COND(rast,0,false,0);return dirty_draw_states;}
int main(){
 Info info{640};Physical physical{&info};tu_device device{&physical};uint32_t buffer[256]{};tu_bo bo{buffer,0x20000000,sizeof(buffer)};
 for(int error:{-1,-2,-4})for(bool writable:{false,true}){
  tu_cs parent;parent.device=&device;parent.mode=TU_CS_MODE_SUB_STREAM;parent.start=parent.cur=buffer;parent.end=buffer+256;parent.refcount_bo=&bo;parent.writeable=writable;
  injected=error;tu_cs sub;assert(tu_cs_begin_sub_stream_aligned(&parent,16,1,&sub)==error);
#ifdef OLD_SUBSTREAM
  // Reproduce the device-dependent raster read after an unchecked failed allocation.
  return sub.device->physical_device->info->marker;
#else
  assert(sub.device==&device && sub.mode==TU_CS_MODE_EXTERNAL && sub.status==error && sub.writeable==writable);
  assert(sub.start==tu_cs_fail_sink && sub.cur==tu_cs_fail_sink && sub.end==tu_cs_fail_sink+TU_CS_FAIL_SINK_SIZE);
  assert(!sub.external_iova && parent.status==error);
  tu_cs child;auto ds=tu_cs_draw_state(&parent,&child,16);assert(!ds.iova && child.device==&device && child.status==error);
  tu_cmd_buffer cmd;cmd.device=&device;cmd.sub_cs=parent;emitCalls=0;assert(draw(&cmd)==0 && cmd.vk.error==error && !emitCalls);
#endif
 }
 injected=0;tu_cmd_buffer cmd;cmd.device=&device;cmd.sub_cs.device=&device;cmd.sub_cs.mode=TU_CS_MODE_SUB_STREAM;
 cmd.sub_cs.refcount_bo=&bo;cmd.sub_cs.start=cmd.sub_cs.cur=buffer;cmd.sub_cs.end=buffer+256;
 emitCalls=0;assert(draw(&cmd)==1 && !cmd.vk.error && emitCalls==1);
 puts("PASS production failed substream/draw-state constructors, real raster macro error propagation, successful state emission");
}
'''
a.output.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='nabu-substream-') as td:
 t=Path(td);src=t/'test.cpp';cmd=['c++','-std=c++20','-O1','-g','-Wall','-Wextra','-Werror','-Wno-narrowing','-Wno-unused-function','-Wno-class-memaccess','-Wno-unknown-warning-option','-fsanitize=address,undefined']
 src.write_text(peer+methods(a.mesa)+wrapper+macros(a.mesa)+test)
 subprocess.run([*cmd,str(src),'-o',str(t/'test')],check=True)
 q=subprocess.run([str(t/'test')],capture_output=True,text=True);(a.output/'run.log').write_text(q.stdout+q.stderr);assert not q.returncode,q.stderr;print(q.stdout,end='')
 if a.baseline_mesa:
  src.write_text(peer+methods(a.baseline_mesa)+wrapper+macros(a.baseline_mesa)+test)
  subprocess.run([*cmd,'-DOLD_SUBSTREAM',str(src),'-o',str(t/'old')],check=True)
  q=subprocess.run([str(t/'old')],capture_output=True,text=True);(a.output/'old-run.log').write_text(q.stdout+q.stderr)
  assert q.returncode and ('runtime error:' in q.stderr or 'AddressSanitizer' in q.stderr)
(a.output/'verification.json').write_text(json.dumps(dict(passed=True,actual_turnip_constructors=True,actual_dynamic_state_macro=True,
 allocation_errors=[-1,-2,-4],device_context_retained=True,failed_emit_not_called=True,command_buffer_error_propagated=True,
 successful_state_emission=True,old_failed_device_read_reproduced=bool(a.baseline_mesa),sanitizers=['ASan','UBSan'],hardware_tested=False),indent=2)+'\n')
