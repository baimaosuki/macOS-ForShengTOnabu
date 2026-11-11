"""Run the actual bounded abort observer with saved-state and user-copy failures."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AbortTrace(unittest.TestCase):
    def test_actual_helper_is_opt_in_bounded_and_read_only(self):
        cc = next((shutil.which(name) for name in ('clang', 'clang-19', 'clang-18', 'clang-17')
                   if shutil.which(name)), None)
        if not cc:
            self.skipTest('requires Clang')
        patch = (ROOT / 'platform/nabu/macos26/desktop/patches/19-macos-abort-forensics.patch').read_text()
        source = '\n'.join(line[1:] for line in patch.splitlines()
                           if line.startswith('+') and not line.startswith('+++'))
        helper = source[source.index('static LCK_MTX_DECLARE'):source.index('\n#endif')]
        common = r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <stdlib.h>
#define LCK_MTX_DECLARE(n,g) int n
#define lck_mtx_lock(p) ((void)(p))
#define lck_mtx_unlock(p) ((void)(p))
#define os_atomic_inc(p,o) ((void)++*(p))
#define relaxed 0
typedef uint64_t user_addr_t;
struct proc {const char *p_comm;int pid;uint64_t uid;};
typedef struct proc *proc_t;
typedef struct {uint64_t pc,lr,fp,sp;bool is64;} arm_saved_state_t;
static arm_saved_state_t saved={0x180000100,0x180001234,0x1000,0x1000,true};
static int present,enabled,copy_calls,copy_error,no_state,reason_fault;
static uint64_t now;
static int PE_parse_boot_argn(const char*n,uint32_t*v,size_t z){assert(!strcmp(n,"nabu-boot-proc")&&z==4);*v=enabled;return present;}
static uint64_t mach_absolute_time(void){return now;}
static void absolutetime_to_nanoseconds(uint64_t t,uint64_t*n){*n=t;}
static uint64_t proc_uniqueid(proc_t p){return p->uid;}
static int proc_getpid(proc_t p){return p->pid;}
static void *current_thread(void){return 0;}
static arm_saved_state_t *get_user_regs(void*t){(void)t;return no_state?NULL:&saved;}
static bool is_saved_state64(arm_saved_state_t*s){return s->is64;}
static uint64_t get_saved_state_pc(arm_saved_state_t*s){return s->pc;}
static uint64_t get_saved_state_lr(arm_saved_state_t*s){return s->lr;}
static uint64_t get_saved_state_fp(arm_saved_state_t*s){return s->fp;}
static uint64_t get_saved_state_sp(arm_saved_state_t*s){return s->sp;}
static int copyinstr(user_addr_t a,char*b,size_t len,size_t*n){
 if(reason_fault)return EFAULT;size_t l=strlen((const char*)(uintptr_t)a)+1;
 if(l>len){memcpy(b,(const void*)(uintptr_t)a,len);return ENAMETOOLONG;}
 memcpy(b,(const void*)(uintptr_t)a,l);*n=l;return 0;
}
static int copyin(user_addr_t a,void*b,size_t n){
 copy_calls++;assert(n==16);if(copy_error)return copy_error;
 assert(a>=0x1000&&a<0x1060&&!(a&15));
 uint64_t pair[2]={a+16,0xa500000180002000ULL+(a-0x1000)};
 memcpy(b,pair,sizeof(pair));return 0;
}
'''
        body = r'''
int main(void){
 struct proc p={"cryptexd",69,100};arm_saved_state_t before=saved;
 nabu_abort_trace(&p,18,2,42,(uintptr_t)"fault",3,0);assert(!copy_calls&&!nabu_abort_trace_events);
 present=1;enabled=2;nabu_abort_trace(&p,18,2,42,0,3,0);assert(!nabu_abort_trace_events);
 enabled=1;p.p_comm="cryptexd.extra";nabu_abort_trace(&p,18,2,42,0,3,0);assert(!nabu_abort_trace_events);
 p.p_comm="cryptexd";nabu_abort_trace(&p,18,2,42,(uintptr_t)"reason\ntext",3,0);
 assert(copy_calls==6&&nabu_abort_trace_events==1&&!memcmp(&saved,&before,sizeof(saved)));
 /* Invalid/misaligned, below-SP, distant and non-user FP values never copy. */
 uint64_t bad[]={0x1001,0xff0,0x102000,0x00007ffffe000000ULL,0};
 p.p_comm="mount";
 for(unsigned i=0;i<sizeof(bad)/sizeof(bad[0]);i++){
  now+=6000000000ULL;saved.fp=bad[i];int calls=copy_calls;
  nabu_abort_trace(&p,18,2,0,0,0,0);assert(copy_calls==calls);
 }
 saved=before;p.p_comm="fskitd";copy_error=EFAULT;
 int calls=copy_calls;nabu_abort_trace(&p,18,2,0,0,0,0);assert(copy_calls==calls+1);copy_error=0;
 p.p_comm="WindowServer";reason_fault=1;nabu_abort_trace(&p,18,2,99,1,4,1);reason_fault=0;
 char longtext[300];memset(longtext,'q',299);longtext[299]=0;
 nabu_abort_trace(&p,18,2,0,(uintptr_t)longtext,0,0);
 no_state=1;p.p_comm="diskarbitrationd";nabu_abort_trace(&p,18,2,0,0,0,0);no_state=0;
 /* Per-target budgets recur after churn, and cannot starve another target. */
 p.p_comm="cryptexd";saved.fp=0;
 for(int i=0;i<8;i++)nabu_abort_trace(&p,18,2,0,0,0,0);
 unsigned seen=nabu_abort_trace_events;now+=6000000000ULL;
 nabu_abort_trace(&p,18,2,0,0,0,0);assert(nabu_abort_trace_events==seen+1);
 assert(saved.pc==before.pc&&saved.lr==before.lr&&saved.sp==before.sp);
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as folder:
            src, binary = Path(folder) / 'abort.c', Path(folder) / 'abort'
            src.write_text(common + helper + body)
            build = subprocess.run([cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-format',
                                    str(src), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([binary], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('ns=18 code=0x2', run.stdout)
        self.assertIn('payload_size=42', run.stdout)
        self.assertIn('text=reason?text', run.stdout)
        self.assertIn('copy_error=14', run.stdout)
        self.assertIn('truncated=1', run.stdout)
        self.assertIn('caller_user=0x180002000', run.stdout)
        self.assertIn('frame=5', run.stdout)
        self.assertNotIn('frame=6', run.stdout)
        self.assertIn('user_state64=0', run.stdout)
        self.assertIn('suppressed=5', run.stdout)
        # This patch adds only the observer call to the existing abort body.
        self.assertNotIn('return ', source[source.index('nabu_abort_trace(p, reason_namespace'):])
        self.assertNotIn('set_saved_state', helper)


if __name__ == '__main__': unittest.main()
