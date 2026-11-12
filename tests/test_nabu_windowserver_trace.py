"""Exercise the actual journal observer with missing state and unreadable frames."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WindowServerTrace(unittest.TestCase):
    def test_context_and_termination_observer(self):
        compiler = shutil.which('clang') or shutil.which('cc')
        if not compiler:
            self.skipTest('requires a C compiler')
        source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#define NABU_WS_TRACE_HOST_TEST 1
typedef uint64_t user_addr_t;
typedef struct saved {uint64_t x[29],pc,lr,sp,fp,esr,far;} arm_saved_state64_t;
typedef arm_saved_state64_t arm_saved_state_t;
struct task {int pid;};
typedef struct task *task_t;
struct region {uint64_t sr_slide;};
typedef struct region *vm_shared_region_t;
static struct task self={100},other={200};
static struct region region={0x6810000};
static arm_saved_state_t saved;
static bool enabled,have_state=true,have_region=true,readable=true,cyclic;
static unsigned copies,regions,emits;
static char journal[65536];
static size_t used;
static int PE_parse_boot_argn(const char *n,void *p,int size){
 assert(!strcmp(n,"nabu-ws-log") && size==sizeof(unsigned));
 *(unsigned *)p=enabled;return 1;
}
static int proc_selfpid(void){return self.pid;}
static void proc_name(int pid,char *p,int size){snprintf(p,size,"%s",pid==100?"WindowServer":"lsd");}
static int task_pid(task_t task){return task->pid;}
static task_t current_task(void){return &self;}
static int current_thread(void){return 1;}
static arm_saved_state_t *get_user_regs(int t){assert(t==1);return have_state?&saved:0;}
static int is_saved_state64(arm_saved_state_t *s){return s==&saved;}
#define get_saved_state_pc(s) ((s)->pc)
#define get_saved_state_lr(s) ((s)->lr)
#define get_saved_state_sp(s) ((s)->sp)
#define get_saved_state_fp(s) ((s)->fp)
#define get_saved_state_esr(s) ((s)->esr)
#define get_saved_state_far(s) ((s)->far)
static const arm_saved_state64_t *const_saved_state64(arm_saved_state_t *s){return s;}
static vm_shared_region_t vm_shared_region_get(task_t t){assert(t==&self);if(have_region)regions++;return have_region?&region:0;}
static void vm_shared_region_deallocate(vm_shared_region_t r){assert(r==&region && regions);regions--;}
static uint64_t mach_absolute_time(void){return 123456789;}
static void absolutetime_to_nanoseconds(uint64_t t,uint64_t *out){*out=t;}
static void nabu_capture_append(const char *s,unsigned n){assert(used+n<sizeof(journal));memcpy(journal+used,s,n);used+=n;journal[used]=0;emits++;}
static int copyin(user_addr_t addr,void *dest,size_t n){
 copies++;
 if(!readable)return 14;
 if(addr==0x180123000 && n==4){*(uint32_t *)dest=0xd4001001;return 0;}
 if(addr==0x160001000 && n==16){
  uint64_t pair[]={cyclic?addr:0x160001010,0xc93a000186f29900};
  memcpy(dest,pair,n);return 0;
 }
 return 14;
}
#include "windowserver_trace.h"
int main(void){
 saved=(arm_saved_state_t){.pc=0x180123000,.lr=0x180456000,.sp=0x160000000,.fp=0x160001000,.esr=0x56000080};
 nabu_ws_trace_context("exit-syscall",0,0,0);assert(!used && !copies);
 enabled=true;self.pid=200;
 nabu_ws_trace_context("exit-syscall",0,0,0);assert(!used && !copies);
 self.pid=100;nabu_ws_trace_context("exit-syscall",254,0,0);
 assert(strstr(journal,"event=exit-syscall pid=100") && strstr(journal,"code=fe"));
 assert(strstr(journal,"insn=d4001001 fetch=0 slide=6810000"));
 assert(strstr(journal,"caller=186f29900 copy=0") && strstr(journal,"copy=14"));
 assert(!regions);
 size_t before=used;unsigned prior=copies;
 nabu_ws_trace_terminate(&other,0x123);assert(used==before && copies==prior);
 nabu_ws_trace_terminate(&self,0xfffffe0001234567);
 assert(strstr(journal,"event=task-terminate target=100 caller_pid=100 self=1"));
 assert(copies==prior);
 have_region=false;readable=false;
 nabu_ws_trace_context("exception",2,1,99);
 assert(strstr(journal,"code=2 sub=1 detail=63") && strstr(journal,"fetch=14 slide=ffffffffffffffff"));
 assert(!regions);
 have_state=false;prior=copies;
 nabu_ws_trace_context("exit-with-reason",0,0,0);assert(copies==prior && strstr(journal,"state64=0"));
 have_state=true;readable=true;cyclic=true;prior=copies;
 nabu_ws_trace_context("exception",2,1,0);assert(copies==prior+2);
 saved.fp=0x160001003;prior=copies;
 nabu_ws_trace_context("exit-syscall",0,0,0);assert(copies==prior+1);
 /* Budget exhaustion stops user-memory reads as well as journal output. */
 have_state=false;
 while(nabu_ws_trace_count<32)nabu_ws_trace_context("exit-syscall",0,0,0);
 before=used;prior=copies;
 nabu_ws_trace_context("exception",2,1,0);nabu_ws_trace_terminate(&self,0);
 assert(used==before && copies==prior && !regions && emits);
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'observer.c').write_text(source)
            build = subprocess.run([compiler, '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                                    '-fsanitize=address,undefined',
                                    '-I', str(ROOT / 'platform/nabu/macos26/log'),
                                    str(path / 'observer.c'), '-o', str(path / 'observer')],
                                   capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(path / 'observer')], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == '__main__':
    unittest.main()
