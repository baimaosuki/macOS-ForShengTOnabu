"""Exercise the actual opt-in exec/exit diagnostic helpers, including late failures."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BootProcessTrace(unittest.TestCase):
    def test_actual_helpers_cover_proxy_exec_ancestry_and_recurring_failure(self):
        cc = next((shutil.which(name) for name in ('clang', 'clang-19', 'clang-18', 'clang-17')
                   if shutil.which(name)), None)
        if not cc:
            self.skipTest('requires Clang')
        patch = (ROOT / 'platform/nabu/macos26/desktop/patches/18-macos-boot-process-trace.patch').read_text()
        source = '\n'.join(line[1:] for line in patch.splitlines()
                           if line.startswith('+') and not line.startswith('+++'))
        helper = source[source.index('#define NABU_LIFE_SLOTS'):source.index('\n#endif')]
        common = r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <sys/wait.h>
#define MAXPATHLEN 1024
#define PROC_NULL NULL
#define OS_REASON_NULL NULL
#define EXECUTABLE_KEY "executable_path="
#define Z_WAITOK 0
#define Z_ZERO 0
#define relaxed 0
#define LCK_MTX_DECLARE(n,g) int n
#define lck_mtx_lock(p) ((void)(p))
#define lck_mtx_unlock(p) ((void)(p))
#define os_atomic_inc(p,o) ((void)++*(p))
#define kalloc_type(t,f) ((t*)calloc(1,sizeof(t)))
#define kfree_type(t,p) free(p)
struct reason {unsigned osr_namespace; uint64_t osr_code, osr_flags;};
typedef struct reason *os_reason_t;
struct proc {int pid,ppid; uint64_t uid; const char *p_comm; int p_xstat; os_reason_t p_exit_reason;};
typedef struct proc *proc_t;
struct test_cred {unsigned real_uid,effective_uid;};
typedef struct test_cred *kauth_cred_t;
static struct test_cred credentials={0,0};
static int cred_refs;
static kauth_cred_t kauth_cred_proc_ref(proc_t p){assert(p);cred_refs++;return &credentials;}
static unsigned kauth_cred_getruid(kauth_cred_t c){return c->real_uid;}
static unsigned kauth_cred_getuid(kauth_cred_t c){return c->effective_uid;}
static void kauth_cred_unref(kauth_cred_t *c){assert(*c);*c=0;cred_refs--;}
static proc_t retired_shadow;
static bool proc_is_shadow(proc_t p){return p==retired_shadow;}
struct image_params {const char *ip_startargv,*ip_endargv,*ip_strings,*ip_vp; int ip_argc;};
static int present, enabled, path_error, refs;
static uint64_t now;
static struct proc parents[]={{1,0,1,"launchd",0,0},{40,1,40,"diskarbitrationd",0,0},{41,40,41,"mount",0,0}};
#define strlcpy trace_strlcpy
static size_t trace_strlcpy(char *d,const char *s,size_t n){size_t k=strlen(s);if(n){size_t c=k<n-1?k:n-1;memcpy(d,s,c);d[c]=0;}return k;}
static int PE_parse_boot_argn(const char*n,uint32_t*v,size_t z){assert(!strcmp(n,"nabu-boot-proc")&&z==4);*v=enabled;return present;}
static uint64_t mach_absolute_time(void){return now;}
static void absolutetime_to_nanoseconds(uint64_t t,uint64_t*n){*n=t;}
static uint64_t proc_uniqueid(proc_t p){return p->uid;}
static int proc_getpid(proc_t p){return p->pid;}
static int proc_ppid(proc_t p){return p->ppid;}
static proc_t proc_find(int pid){for(unsigned i=0;i<3;i++)if(parents[i].pid==pid){refs++;return &parents[i];}return NULL;}
static void proc_rele(proc_t p){assert(p);refs--;}
static void proc_name(int pid,char*b,int n){proc_t p=proc_find(pid);if(p){strlcpy(b,p->p_comm,n);proc_rele(p);}}
static int vn_getpath(const char*vp,char*b,int*n){if(path_error)return path_error;strlcpy(b,vp,*n);return 0;}
'''
        body = r'''
static void image(proc_t p,const char *name,const char*path,const char*args,size_t bytes,int argc){
 p->p_comm=name;struct image_params im={args,args+bytes,"executable_path=/fallback",path,argc};nabu_lifecycle_exec(p,&im);
}
int main(void){
 struct proc p={50,41,500,"mount_apfs",0,0};
 const char args[]="mount_apfs\0-o\0rw,nobrowse\0/dev/disk0s1\0/System/Volumes/Data\0";
 image(&p,"mount_apfs","/sbin/mount_apfs",args,sizeof(args)-1,5);assert(!nabu_lifecycle_trace_events);
 present=1;enabled=2;image(&p,"mount_apfs","/sbin/mount_apfs",args,sizeof(args)-1,5);assert(!nabu_lifecycle_trace_events);
 enabled=1;
 image(&p,"mount_apfs","/sbin/mount_apfs",args,sizeof(args)-1,5);assert(nabu_lifecycle_trace_events==1);
 p.p_xstat=1<<8;nabu_lifecycle_exit(&p);assert(nabu_lifecycle_trace_events==2&&!refs);
 assert(!nabu_life_kind("mount_apfs.extra")&&!nabu_life_proxy_kind("com.apple.logd"));
 assert(!nabu_life_proxy_kind("com.apple.filesystems.fskitd.extra"));
 const char proxy[]="xpcproxy\0com.apple.filesystems.fskitd\0";
 p.pid=51;p.uid=501;p.ppid=1;
 image(&p,"xpcproxy","/usr/libexec/xpcproxy",proxy,sizeof(proxy)-1,2);
 struct proc shadow=p;
 const char fsk[]="fskitd\0";
 image(&shadow,"fskitd","/usr/libexec/fskitd",fsk,sizeof(fsk)-1,1);
 /* Exec replacement preserves uid: retiring the old proc must neither emit
  * a false exit0 nor consume the final image's saved args/reason record. */
 unsigned before_shadow=nabu_lifecycle_trace_events;
 retired_shadow=&p;p.p_xstat=0;p.p_exit_reason=0;
 nabu_lifecycle_exit(&p);retired_shadow=0;
 assert(nabu_lifecycle_trace_events==before_shadow);
 struct reason reason={3,0xabc,4};shadow.p_exit_reason=&reason;shadow.p_xstat=SIGKILL;
 nabu_lifecycle_exit(&shadow);assert(!refs&&nabu_lifecycle_trace_events==before_shadow+1);
 /* Independent success/failure buckets: initial successful churn cannot hide
  * first failure; later failures recur after the interval with omitted count. */
 for(int i=0;i<100;i++){p.pid=100+i;p.uid=1000+i;p.ppid=40;
  image(&p,"mount","/sbin/mount",args,sizeof(args)-1,5);p.p_xstat=0;nabu_lifecycle_exit(&p);}
 unsigned count=nabu_lifecycle_trace_events;
 p.pid=300;p.uid=3000;image(&p,"mount","/sbin/mount",args,sizeof(args)-1,5);
 p.p_xstat=2<<8;nabu_lifecycle_exit(&p);assert(nabu_lifecycle_trace_events==count+1);
 for(int i=0;i<20;i++){p.uid=4000+i;image(&p,"mount","/sbin/mount",args,sizeof(args)-1,5);nabu_lifecycle_exit(&p);}
 count=nabu_lifecycle_trace_events;now=6000000000ULL;p.pid=999;p.uid=9999;
 image(&p,"mount","/sbin/mount",args,sizeof(args)-1,5);nabu_lifecycle_exit(&p);
 assert(nabu_lifecycle_trace_events==count+2);
 /* Bounded arguments, malformed copied argv, and vnode-path fallback. */
 char longarg[900];memset(longarg,'q',sizeof(longarg));longarg[899]=0;
 struct nabu_life_record r={0};struct image_params im={longarg,longarg+900,0,0,1};
 nabu_life_args(&im,&r);assert(r.argv_truncated&&strlen(r.args)<sizeof(r.args));
 im.ip_endargv=longarg+899;assert(!nabu_life_arg(&im,0));
 path_error=5;p.uid=10000;image(&p,"WindowServer","bad",fsk,sizeof(fsk)-1,1);nabu_lifecycle_exit(&p);
 const char unrelated[]="xpcproxy\0com.apple.logd\0";
 count=nabu_lifecycle_trace_events;p.uid=10001;
 image(&p,"xpcproxy","/usr/libexec/xpcproxy",unrelated,sizeof(unrelated)-1,2);nabu_lifecycle_exit(&p);
 assert(count==nabu_lifecycle_trace_events&&!refs);
 /* All selected services and original cached proxy labels have independent
  * normal/failure exits; credentials are refreshed for the actual exit. */
 const char *names[]={"cfprefsd","digest-service","eligibilityd","endpointsecurity","opendirectoryd","loginwindow"};
 const char *labels[]={"com.apple.cfprefsd.xpc.agent","com.apple.cfprefsd.xpc.daemon",
 "com.apple.Kerberos.digest-service","com.apple.eligibilityd",
 "com.apple.endpointsecurity.endpointsecurityd","com.apple.opendirectoryd","com.apple.loginwindow"};
 for(unsigned i=0;i<6;i++){
  assert(nabu_life_kind(names[i])==i+7);
  p.pid=1100+i;p.uid=11000+i;p.p_exit_reason=0;p.p_xstat=0;
  credentials.real_uid=501;credentials.effective_uid=501;
  count=nabu_lifecycle_trace_events;image(&p,names[i],names[i],fsk,sizeof(fsk)-1,1);
  credentials.effective_uid=0;nabu_lifecycle_exit(&p);
  assert(nabu_lifecycle_trace_events==count+2&&!cred_refs);
  p.uid=12000+i;p.p_xstat=SIGABRT;
  image(&p,names[i],names[i],fsk,sizeof(fsk)-1,1);nabu_lifecycle_exit(&p);
  assert(nabu_lifecycle_trace_events==count+4&&!cred_refs);
 }
 assert(nabu_life_kind("endpointsecurityd")==nabu_life_kind("endpointsecurity"));
 assert(!nabu_life_kind("endpointsecurity.extra"));
 for(unsigned i=0;i<7;i++){
  assert(nabu_life_proxy_kind(labels[i])==i+17);
  char proxyargs[160];size_t len=strlen(labels[i])+1;
  memcpy(proxyargs,"xpcproxy",9);memcpy(proxyargs+9,labels[i],len);
  p.uid=13000+i;p.p_xstat=0;count=nabu_lifecycle_trace_events;
  image(&p,"xpcproxy","/usr/libexec/xpcproxy",proxyargs,9+len,2);nabu_lifecycle_exit(&p);
  assert(nabu_lifecycle_trace_events==count+2&&!cred_refs);
 }

 return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            src, binary = Path(directory) / 'trace.c', Path(directory) / 'trace'
            src.write_text(common + helper + body)
            build = subprocess.run([cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-format',
                                    str(src), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([binary], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('ppid=41 parent=mount gpid=40 grandparent=diskarbitrationd', run.stdout)
        self.assertIn('argv=[mount_apfs][-o][rw,nobrowse][/dev/disk0s1][/System/Volumes/Data]', run.stdout)
        self.assertIn('wait=0x100 exit=1 signal=0', run.stdout)
        self.assertIn('reason_ns=3 reason_code=0xabc reason_flags=0x4', run.stdout)
        self.assertIn('pid=999', run.stdout)
        self.assertIn('suppressed=17', run.stdout)
        self.assertIn('path=/fallback path_error=5', run.stdout)
        self.assertNotIn('com.apple.logd', run.stdout)
        self.assertIn('event=exec pid=1100 uid=11000', run.stdout)
        self.assertIn('real_uid=501 effective_uid=501', run.stdout)
        self.assertIn('event=exit pid=1100 uid=11000', run.stdout)
        self.assertIn('real_uid=501 effective_uid=0', run.stdout)


if __name__ == '__main__':
    unittest.main()
