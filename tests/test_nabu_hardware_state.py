"""Exercise the Hardware view against verified Data and its public write probe."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HardwareState(unittest.TestCase):
    def test_actual_mapping_probe_and_failure_cleanup(self):
        cc = next((shutil.which(n) for n in ('clang', 'clang-19', 'clang-18', 'clang-17')
                   if shutil.which(n)), None)
        if not cc:
            self.skipTest('requires Clang')
        patch = (ROOT / 'platform/nabu/macos26/desktop/patches/22-macos-writable-hardware-state.patch').read_text()
        added = '\n'.join(line[1:] for line in patch.splitlines()
                          if line.startswith('+') and not line.startswith('+++'))
        helper = added[added.index('volatile int native_owner_hardware_volume_status'):added.index('\n/*', added.index('return error;'))]
        harness = r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <sys/types.h>
typedef int proc_t; typedef uintptr_t user_addr_t;
typedef int vnode_t; typedef intptr_t mount_t; typedef int vfs_context_t;
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
struct mkdir_args {user_addr_t path;int mode;};
static int native_owner_data_stage=5;
static dev_t native_owner_data_device=9;
static int scenario,refs,links,rollbacks,opens,closes,removes,writes,reads,mapped,makes;
static char copied_path[128],stored[17];
static vfs_context_t vfs_context_current(void){return 1;}
static void *vfs_context_ucred(vfs_context_t c){(void)c;return NULL;}
static int vnode_lookup(const char *s,int flags,vnode_t *v,vfs_context_t c){
 (void)c;
 if(!strcmp(s,NATIVE_OWNER_DATA_MOUNT_POINT)){assert(flags==1);*v=1;}
 else if(!strcmp(s,"/private/var/db")){assert(flags==8);*v=7;}
 else if(!strcmp(s,"/private/var/db/hardware-volume")){assert(flags==8);*v=2;}
 else{assert(!strcmp(s,"/System/Volumes/Hardware"));*v=flags?(mapped?2:3):2;}
 refs++;return 0;
}
static mount_t vnode_mount(vnode_t v){return v==3?2:(scenario==2&&v==7?3:1);}
static int vnode_vtype(vnode_t v){return v==4?VREG:VDIR;}
static bool vnode_isvroot(vnode_t v){return v==1;}
static vnode_t vfs_devvp(mount_t m){assert(m==1);refs++;return 6;}
static dev_t vnode_specrdev(vnode_t v){assert(v==6);return scenario==1?11:9;}
static void vnode_put(vnode_t v){assert(v);refs--;}
static uint64_t vfs_flags(mount_t m){return m==2?MNT_RDONLY:0;}
static struct {char *f_fstypename;} fs={"apfs"};
#define vfs_statfs(m) (&fs)
static int vnode_setasfirmlink(vnode_t a,vnode_t b){assert(a==3);if(!b){rollbacks++;mapped=0;return 0;}assert(b==2);links++;mapped=1;return 0;}
static uint64_t mach_absolute_time(void){return 0x1234;}
static int copyout(const void*a,user_addr_t b,size_t n){assert(b==0x1c00&&n<128);memcpy(copied_path,a,n);return 0;}
static int mkdir(proc_t p,struct mkdir_args*a,int32_t*r){(void)p;(void)r;assert(a->path==0x1c00&&a->mode==0755);assert(!strcmp(copied_path,"/private/var/db/hardware-volume"));makes++;return mapped?EEXIST:0;}
static int vnode_open(const char*p,int f,int mode,int u,vnode_t*v,vfs_context_t c){
 (void)c;assert(!u&&mode==0600&&f==(FREAD|FWRITE|O_CREAT|O_EXCL|O_NOFOLLOW));assert(!strcmp(p,copied_path));assert(strstr(p,"/System/Volumes/Hardware/.nabu-hardware-write-probe-"));opens++;if(scenario==3)return EEXIST;*v=4;return 0;
}
static int vn_rdwr(int rw,vnode_t v,caddr_t b,int n,off_t o,int seg,int flags,void*cred,int*resid,proc_t p){
 (void)cred;(void)p;assert(v==4&&n==17&&!o&&!seg);*resid=0;
 if(rw==UIO_WRITE){assert(flags==IO_UNIT);writes++;memcpy(stored,b,n);if(scenario==4)return EIO;}
 else{reads++;memcpy(b,stored,n);if(scenario==5)b[0]^=1;}
 return 0;
}
static int vnode_close(vnode_t v,int flags,vfs_context_t c){(void)c;assert(v==4&&flags==3);closes++;return 0;}
static int unlink(proc_t p,struct unlink_args*a,int32_t*r){(void)p;(void)r;assert(a->path==0x1c00);removes++;return scenario==6?EIO:0;}
static void reset(void){scenario=refs=links=rollbacks=opens=closes=removes=writes=reads=mapped=makes=0;native_owner_data_stage=5;}
'''
        body = r'''
int main(void){
 reset();assert(!native_owner_macos_hardware_volume(1,0x1000));
 assert(!native_owner_hardware_volume_status&&!refs&&makes==1&&links==1&&opens==1&&closes==1&&removes==1&&writes==1&&reads==1);
 reset();mapped=1;assert(!native_owner_macos_hardware_volume(1,0x1000));assert(!links&&!refs&&opens==1);
 for(int failure=1;failure<=6;failure++){
  reset();scenario=failure;int error=native_owner_macos_hardware_volume(1,0x1000);
  assert(error&&native_owner_hardware_volume_status==error&&!refs);
  if(failure<=2)assert(!makes&&!links&&!opens);
  if(failure==3)assert(!closes&&!removes);
  if(failure>=4)assert(closes==1&&removes==1&&rollbacks==1);
 }
 reset();native_owner_data_stage=4;assert(native_owner_macos_hardware_volume(1,0x1000)==ENXIO&&!refs&&!makes);
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            src, binary = Path(directory) / 'hardware.c', Path(directory) / 'hardware'
            src.write_text(harness + helper + body)
            build = subprocess.run([cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                                    '-Wno-format', str(src), '-o', str(binary)],
                                   capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([binary], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('if (!error) error = native_owner_macos_hardware_volume(p, scratch);', added)
        self.assertNotIn('MobileActivation', helper)
        self.assertNotIn('FactoryData', helper)


if __name__ == '__main__':
    unittest.main()
