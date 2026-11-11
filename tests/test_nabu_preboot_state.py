"""Execute checked Preboot mapping and write probe, retaining readonly children."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def function(text, name):
    start = text.index('static int\n' + name + '(')
    brace = text.index('{', start)
    at, depth = brace + 1, 1
    while depth:
        depth += (text[at] == '{') - (text[at] == '}')
        at += 1
    return text[start:at] + '\n'


class PrebootState(unittest.TestCase):
    def test_actual_checked_mapping_probe_cleanup_and_readonly_children(self):
        cc = next((shutil.which(n) for n in ('clang', 'clang-19', 'clang-18', 'clang-17') if shutil.which(n)), None)
        if not cc: self.skipTest('requires Clang')
        patch = (ROOT / 'platform/nabu/macos26/desktop/integrated.patch').read_text()
        source = '\n'.join(line[1:] for line in patch.splitlines() if line.startswith('+') and not line.startswith('+++'))
        helper = function(source, 'native_owner_macos_preboot_view')
        view = function(source, 'native_owner_macos_cryptex_view')
        harness = r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <sys/types.h>
typedef int proc_t;typedef uintptr_t user_addr_t;typedef int vnode_t;typedef intptr_t mount_t;typedef int vfs_context_t;
#undef NULL
#define NULL 0
#define NULLVP 0
#define NODEV ((dev_t)-1)
#define VDIR 1
#define VREG 2
#define MNT_RDONLY 1
#define VNODE_LOOKUP_NOFOLLOW 1
#define VNODE_LOOKUP_NOFOLLOW_ANY 8
#define FREAD 1
#define FWRITE 2
#define O_CREAT 4
#define O_EXCL 8
#define O_NOFOLLOW 16
#define UIO_WRITE 1
#define UIO_READ 0
#define UIO_SYSSPACE 0
#define IO_UNIT 1
#define NATIVE_OWNER_DATA_MOUNT_POINT "/System/Volumes/Data"
struct unlink_args {user_addr_t path;};
static int native_owner_data_stage=5;
static volatile int native_owner_preboot_status=-1;
static dev_t native_owner_data_device=9;
static int scenario,lookup_number,refs,links,rollbacks,opens,closes,removes,writes,reads,binds,parents,mapped;
static int native_cryptex_os_stage,native_cryptex_os_status;
static char copied_path[128],stored[16];
static vfs_context_t vfs_context_current(void){return 1;}
static void *vfs_context_ucred(vfs_context_t c){(void)c;return NULL;}
static int vnode_lookup(const char *s,int flags,vnode_t *v,vfs_context_t c){
 (void)c;lookup_number++;
 if(lookup_number==scenario)return EACCES;
 if(!strcmp(s,NATIVE_OWNER_DATA_MOUNT_POINT)){assert(flags==1);*v=1;}
 else if(!strcmp(s,"/private/var/ngpu/preboot")){assert(flags==8);*v=2;}
 else if(!strcmp(s,"/System/Volumes/Preboot")){*v=flags?(mapped?2:3):(scenario==10?3:2);}
 else{assert(strstr(s,"JavaScriptCore.framework"));*v=5;}
 refs++;return 0;
}
static mount_t vnode_mount(vnode_t v){return v==3?2:(scenario==6&&v==2?3:1);}
static int vnode_vtype(vnode_t v){return scenario==7&&v==2?VREG:(v==4?VREG:VDIR);}
static bool vnode_isvroot(vnode_t v){return v==1;}
static vnode_t vfs_devvp(mount_t m){assert(m==1);refs++;return 6;}
static dev_t vnode_specrdev(vnode_t v){assert(v==6);return scenario==8?11:9;}
static void vnode_put(vnode_t v){assert(v);refs--;}
static uint64_t vfs_flags(mount_t m){return m==2||scenario==9?MNT_RDONLY:0;}
static struct {char *f_fstypename;} fs={"apfs"};
#define vfs_statfs(m) (&fs)
static int vnode_setasfirmlink(vnode_t a,vnode_t b){assert(a==3);if(!b){rollbacks++;mapped=0;return 0;}assert(b==2);links++;if(scenario==11)return EIO;mapped=1;return 0;}
static uint64_t mach_absolute_time(void){return 0x1234;}
static int copyout(const void*a,user_addr_t b,size_t n){assert(b==0x1c00);assert(n<128);memcpy(copied_path,a,n);return scenario==12?EFAULT:0;}
static int vnode_open(const char*p,int f,int mode,int u,vnode_t*v,vfs_context_t c){
 (void)c;assert(!u&&mode==0600&&f==(FREAD|FWRITE|O_CREAT|O_EXCL|O_NOFOLLOW));assert(!strcmp(p,copied_path));assert(strstr(p,"/System/Volumes/Preboot/.nabu-preboot-write-probe-"));opens++;if(scenario==13)return EEXIST;*v=4;return 0;
}
static int vn_rdwr(int rw,vnode_t v,caddr_t b,int n,off_t o,int seg,int flags,void*cred,int*resid,proc_t p){
 (void)cred;(void)p;assert(v==4&&n==16&&!o&&!seg);*resid=0;
 if(rw==UIO_WRITE){assert(flags==IO_UNIT);writes++;memcpy(stored,b,n);if(scenario==14)return EIO;if(scenario==15)*resid=1;}
 else{reads++;memcpy(b,stored,n);if(scenario==16)b[0]^=1;if(scenario==17)return EIO;}
 return 0;
}
static int vnode_close(vnode_t v,int flags,vfs_context_t c){(void)c;assert(v==4&&flags==3);closes++;return scenario==18?EIO:0;}
static int unlink(proc_t p,struct unlink_args*a,int32_t*r){(void)p;(void)r;assert(a->path==0x1c00);removes++;return scenario==19?EIO:0;}
static int native_gpu_addon_parents(proc_t p,user_addr_t s,const char*path){(void)p;(void)s;assert(strstr(path,"/private/var/ngpu/preboot/Cryptexes/OS/"));parents++;return 0;}
static int native_cryptex_os_bind(proc_t p,user_addr_t s,const char*src,const char*dst,int flags){
 (void)p;(void)s;assert(!flags);assert(!strcmp(src,"/System")||!strcmp(src,"/usr"));assert(strstr(dst,"/System/Volumes/Preboot/Cryptexes/OS/"));binds++;return 0;
}
static void reset(void){scenario=lookup_number=refs=links=rollbacks=opens=closes=removes=writes=reads=binds=parents=mapped=0;native_owner_data_stage=5;}
'''
        body = r'''
int main(void){
 reset();assert(!native_owner_macos_cryptex_view(1,0x1000));
 assert(!refs&&links==1&&opens==1&&closes==1&&removes==1&&writes==1&&reads==1&&binds==2&&parents==2);
 assert(native_cryptex_os_stage==5&&!native_cryptex_os_status);
 reset();mapped=1;assert(!native_owner_macos_preboot_view(1,0x1000));assert(!links&&!refs&&opens==1);
 for(int failure=1;failure<=19;failure++){
  reset();scenario=failure;int error=native_owner_macos_cryptex_view(1,0x1000);
  assert(error&&native_cryptex_os_status==error&&!refs&&binds==(failure==5?2:0));
  if(opens&&failure!=13)assert(closes==1&&removes==1);
  if(links&&failure!=11&&failure!=5)assert(rollbacks==1);
 }
 reset();native_owner_data_stage=4;assert(native_owner_macos_preboot_view(1,0x1000)==ENXIO&&!lookup_number);
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            src,binary=Path(directory)/'preboot.c',Path(directory)/'preboot'
            src.write_text(harness+helper+view+body)
            build=subprocess.run([cc,'-std=gnu11','-Wall','-Wextra','-Werror','-Wno-format',str(src),'-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(build.returncode,0,build.stderr)
            run=subprocess.run([binary],capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
        self.assertIn('if (!error) error = native_owner_macos_cryptex_view(p, scratch);',source)
        self.assertNotIn('com.apple.security.cryptexd',helper)
        self.assertIn('uint64_t mount_flags = MNT_RDONLY;',source)


if __name__=='__main__':unittest.main()
