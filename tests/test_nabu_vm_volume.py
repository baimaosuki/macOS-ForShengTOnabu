"""Compile the integrated VM firmlink/APFS boot-shim helpers and check failures."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def helper(text, name):
    start = text.index('static int\n' + name + '(')
    brace = text.index('{', start)
    at, depth = brace + 1, 1
    while depth:
        depth += (text[at] == '{') - (text[at] == '}')
        at += 1
    return text[start:at] + '\n'


class VmVolume(unittest.TestCase):
    def test_real_helpers_preserve_original_tool_and_propagate_failures(self):
        compiler = next((shutil.which(name) for name in ('clang', 'clang-19', 'clang-18', 'clang-17')
                         if shutil.which(name)), None)
        if not compiler:
            self.skipTest('requires Clang')
        patch = (ROOT / 'platform/nabu/macos26/desktop/integrated.patch').read_text()
        source = '\n'.join(line[1:] for line in patch.splitlines()
                           if line.startswith('+') and not line.startswith('+++'))
        common = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef void *proc_t;
typedef uintptr_t user_addr_t;
typedef int vnode_t;
typedef void *vfs_context_t;
#define NULLVP 0
#define VNODE_LOOKUP_NOFOLLOW 1
struct mkdir_args { user_addr_t path; unsigned mode; };
static int scenario, released, links, installs, mounts, enabled;
static volatile int native_owner_vm_volume_status=-1;
static volatile int native_owner_boot_shim_stage;
static volatile int native_owner_boot_shim_status=-1;
static volatile int native_gpu_addon_files=-1, native_gpu_addon_failed, native_gpu_addon_error;
static vfs_context_t vfs_context_current(void) { return 0; }
static int copyout(const void *a,user_addr_t b,size_t c) {
 (void)a;(void)b;(void)c;return scenario==1?EFAULT:0;
}
static int mkdir(proc_t p,struct mkdir_args *a,int32_t *r) {
 (void)p;(void)r;assert(a->mode==0755);return scenario==2?EROFS:EEXIST;
}
static int vnode_lookup(const char *s,int flags,vnode_t *v,vfs_context_t c) {
 (void)c;assert(flags==VNODE_LOOKUP_NOFOLLOW);
 if(!strcmp(s,"/System/Volumes/VM")){if(scenario==3)return ENOENT;*v=1;}
 else if(!strcmp(s,"/private/var/ngpu/bootshim/apfs_boot_util")){if(scenario==10)return ENOENT;*v=2;}
 else{assert(!strcmp(s,"/private/var/vm"));if(scenario==4)return EACCES;*v=2;}
 return 0;
}
static int vnode_setasfirmlink(vnode_t a,vnode_t b) {
 assert(a==1&&b==2);links++;return scenario==5?EINVAL:0;
}
static void vnode_put(vnode_t v){assert(v==1||v==2);released++;}
static int PE_parse_boot_argn(const char *name,uint32_t *v,size_t n){
 assert(!strcmp(name,"apfs-boot-shim")&&n==4);*v=enabled;return enabled!=0;
}
static void native_gpu_addon_install(proc_t p,user_addr_t s){
 (void)p;(void)s;installs++;
 native_gpu_addon_files=scenario==6?-1:1;
 native_gpu_addon_failed=scenario==7;
 native_gpu_addon_error=scenario==8?EIO:0;
}
static void native_gpu_addon_mount(proc_t p,user_addr_t s,user_addr_t offset,
 const char *source,size_t sl,const char *point,size_t pl,const char *visible,
 const char *own,volatile int *stage,volatile int *status){
 (void)p;(void)s;mounts++;assert(offset==0xc00);
 assert(!strcmp(source,"/private/var/ngpu/bootshim")&&sl==strlen(source)+1);
 assert(!strcmp(point,"/System/Library/Filesystems/apfs.fs/Contents/Resources")&&pl==strlen(point)+1);
 assert(!strcmp(visible,"/System/Library/Filesystems/apfs.fs/Contents/Resources/apfs_checkseal"));
 assert(!strcmp(own,"/System/Library/Filesystems/apfs.fs/Contents/Resources/apfs_boot_util"));
 *stage=scenario==9?3:5;*status=scenario==9?ENOENT:0;
}
'''
        body = r'''
int main(void){
 int errors[]={0,EFAULT,EROFS,ENOENT,EACCES,EINVAL};
 for(scenario=0;scenario<=5;scenario++){
  released=links=0;int e=native_owner_macos_vm_volume(0,0x1000);
  assert(e==errors[scenario]&&native_owner_vm_volume_status==e);
  assert(released==(scenario==4?1:(scenario==0||scenario==5?2:0)));
 }
 enabled=0;assert(!native_owner_macos_boot_shims(0,0x1000)&&!installs&&!mounts);
 enabled=1;native_owner_vm_volume_status=EROFS;
 assert(native_owner_macos_boot_shims(0,0x1000)==EROFS&&!installs&&!mounts);
 native_owner_vm_volume_status=0;
 for(scenario=6;scenario<=8;scenario++){
  mounts=0;assert(native_owner_macos_boot_shims(0,0x1000)==(scenario==8?EIO:ENOENT));assert(!mounts);
 }
 scenario=9;assert(native_owner_macos_boot_shims(0,0x1000)==ENOENT);
 scenario=10;mounts=0;assert(native_owner_macos_boot_shims(0,0x1000)==ENOENT&&!mounts);
 scenario=0;assert(!native_owner_macos_boot_shims(0,0x1000));assert(native_owner_boot_shim_stage==5);
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as folder:
            src, binary = Path(folder) / 'check.c', Path(folder) / 'check'
            src.write_text(common + helper(source, 'native_owner_macos_vm_volume') +
                           helper(source, 'native_owner_macos_boot_shims') + body)
            result = subprocess.run([compiler, '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                                     str(src), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([binary], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = source[source.index('native_owner_macos_data_prepare(proc_t p'):]
        self.assertLess(data.index('native_owner_data_firmlinks(vfs_context_current())'),
                        data.index('error = native_owner_macos_vm_volume(p, scratch)'))
        self.assertLess(data.index('error = native_owner_macos_vm_volume(p, scratch)'),
                        data.index('error = native_owner_macos_boot_shims(p, scratch)'))
        self.assertIn('if (data_error) panic("offline scratch Data mount failed: %d", data_error)', source)


if __name__ == '__main__':
    unittest.main()
