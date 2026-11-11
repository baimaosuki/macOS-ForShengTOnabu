"""Execute the actual opt-in Data policy helpers against VFS failure cases."""
from pathlib import Path
import ast
import plistlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def policy_source():
    patch = (ROOT / 'platform/nabu/macos26/desktop/integrated.patch').read_text()
    return '\n'.join(line[1:] for line in patch.splitlines()
                     if line.startswith('+') and not line.startswith('+++'))


class WatchdogPolicy(unittest.TestCase):
    def test_boolean_policy_and_checked_before_launchd(self):
        source = policy_source()
        literal = source.split('static const uint8_t contents[] =', 1)[1].split(';', 1)[0]
        data = ''.join(ast.literal_eval(part) for part in re.findall(r'"(?:\\.|[^"\\])*"', literal)).encode()
        parsed = plistlib.loads(data)
        self.assertEqual(parsed, {'com.apple.watchdogd': True})
        self.assertIs(parsed['com.apple.watchdogd'], True)
        self.assertLessEqual(len(data), 256)
        data_path = source[source.index('native_owner_macos_data_prepare(proc_t p'):]
        self.assertLess(data_path.index('error = mount(p, &request, &result)'),
                        data_path.index('native_owner_macos_watchdog_policy(p, scratch)'))
        self.assertLess(data_path.index('native_owner_data_firmlinks(vfs_context_current())'),
                        data_path.index('native_owner_macos_watchdog_policy(p, scratch)'))
        self.assertLess(data_path.index('native_owner_macos_boot_shims(p, scratch)'),
                        data_path.index('native_owner_macos_watchdog_policy(p, scratch)'))
        startup = source[source.index('native_owner_macos_data_prepare(p, (user_addr_t)scratch_addr'):]
        self.assertLess(startup.index('native_owner_macos_data_prepare(p, (user_addr_t)scratch_addr'),
                        startup.index('load_init_program_at_path(p, (user_addr_t)scratch_addr'))
        self.assertIn('if (data_error) panic("offline scratch Data mount failed: %d", data_error)', startup)

    def test_real_helpers_enforce_metadata_exclusive_create_and_readback(self):
        compiler = next((shutil.which(n) for n in ('clang', 'clang-19', 'clang-18', 'clang-17')
                         if shutil.which(n)), None)
        if not compiler:
            self.skipTest('requires Clang')
        source = policy_source()
        source = source[source.index('volatile int native_owner_watchdog_policy_status'):source.index(
            'static int\nnative_owner_macos_data_prepare')]
        common = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <sys/types.h>
typedef void *proc_t; typedef uintptr_t user_addr_t; typedef int vnode_t;
typedef void *vfs_context_t;
#define NULLVP 0
#define VNODE_LOOKUP_NOFOLLOW 1
#define FREAD 1
#define FWRITE 2
#define O_CREAT 0x200
#define O_EXCL 0x800
#define O_NOFOLLOW 0x100
#define UIO_WRITE 1
#define UIO_READ 2
#define UIO_SYSSPACE 0
#define IO_NOAUTH 1
#define IO_UNIT 2
#define MNT_WAIT 1
#define VREG 1
#define VDIR 2
struct mkdir_args { user_addr_t path; unsigned mode; };
struct vnode_attr { unsigned va_type, va_uid, va_gid, va_mode; size_t va_data_size; unsigned va_supported; };
#define VATTR_INIT(v) memset(v,0,sizeof(*(v)))
#define VATTR_SET(v,k,x) ((v)->k=(x))
#define VATTR_WANTED(v,k) ((void)(v))
#define VATTR_IS_SUPPORTED(v,k) ((v)->va_supported)
static const char *base="/private/var/db/com.apple.xpc.launchd";
static int enabled, present, scenario, lookups, opens, closes, put_count, syncs, writes, reads, copied;
static char stored[256]; static size_t stored_size;
static vfs_context_t vfs_context_current(void){return 0;}
static void *vfs_context_ucred(vfs_context_t c){return c;}
static proc_t vfs_context_proc(vfs_context_t c){return c;}
static int PE_parse_boot_argn(const char *n,uint32_t *v,size_t size){
 assert(!strcmp(n,"disable-watchdogd")&&size==4);*v=enabled;return present;
}
static int copyout(const void *src,user_addr_t dst,size_t len){
 assert(!strcmp(src,base)&&dst==0x1c00&&len==strlen(base)+1);copied++;
 return scenario==4?EFAULT:0;
}
static int mkdir(proc_t p,struct mkdir_args *a,int32_t *r){
 (void)p;(void)r;assert(a->mode==0755&&a->path==0x1c00);return scenario==5?EROFS:EEXIST;
}
static int vnode_lookup(const char *path,int flags,vnode_t *v,vfs_context_t c){
 (void)c;assert(flags==VNODE_LOOKUP_NOFOLLOW);lookups++;
 if(!strcmp(path,base)){*v=1;return scenario==6?ENOENT:0;}
 assert(!strncmp(path,base,strlen(base)));
 if((scenario==1&&strstr(path,"disabled.plist"))||
    (scenario==2&&strstr(path,"disabled.migrated"))){*v=4;return 0;}
 return scenario==3?EACCES:ENOENT;
}
static void vnode_put(vnode_t v){assert(v==1||v==4);put_count++;}
static int vnode_open(const char *path,int flags,int mode,int unused,vnode_t *v,vfs_context_t c){
 (void)c;assert(!unused&&flags==(FREAD|FWRITE|O_CREAT|O_EXCL|O_NOFOLLOW)&&mode==0644);
 opens++;assert(strstr(path,opens==1?"disabled.plist":"disabled.migrated"));
 if(scenario==12)return EEXIST;
 if(scenario==25&&opens==2)return EEXIST;
 *v=opens+1;return 0;
}
static int vnode_setattr(vnode_t v,struct vnode_attr *a,vfs_context_t c){
 (void)c;assert(v==2||v==3);assert(!a->va_uid&&!a->va_gid&&a->va_mode==0644);
 return scenario==13?EPERM:0;
}
static int vnode_getattr(vnode_t v,struct vnode_attr *a,vfs_context_t c){
 (void)c;assert(v>=1&&v<=3);a->va_type=v==1?VDIR:VREG;a->va_mode=v==1?0755:0644;
 a->va_uid=a->va_gid=0;a->va_data_size=v==2?stored_size:0;a->va_supported=1;
 if(scenario==7&&v==1)a->va_type=VREG;
 if(scenario==8&&v==1)a->va_uid=502;
 if(scenario==9&&v==1)a->va_gid=20;
 if(scenario==10&&v==1)a->va_mode=0777;
 if(scenario==11&&v==1)a->va_supported=0;
 if(v!=1){
  if(scenario==17)a->va_type=VDIR;
  if(scenario==18)a->va_uid=502;
  if(scenario==19)a->va_gid=20;
  if(scenario==20)a->va_mode=0666;
  if(scenario==21)a->va_data_size++;
  if(scenario==22)a->va_supported=0;
 }
 return scenario==26?EIO:0;
}
static int vn_rdwr(int direction,vnode_t v,caddr_t buf,int size,int offset,int space,
 int flags,void *cred,int *resid,proc_t p){
 (void)cred;(void)p;assert(v==2&&!offset&&space==UIO_SYSSPACE&&size>0&&size<=256);
 if(direction==UIO_WRITE){
  writes++;assert(flags==(IO_NOAUTH|IO_UNIT));memcpy(stored,buf,size);stored_size=size;
  if(scenario==14)return EIO;
  if(scenario==15)*resid=1;
 }else{
  reads++;assert(direction==UIO_READ&&flags==IO_NOAUTH);memcpy(buf,stored,size);
  if(scenario==23)buf[0]^=1;
  if(scenario==24)*resid=1;
 }
 return 0;
}
static int VNOP_FSYNC(vnode_t v,int wait,vfs_context_t c){
 (void)c;assert(v==2||v==3);assert(wait==MNT_WAIT);syncs++;return scenario==16?EIO:0;
}
static int vnode_close(vnode_t v,int flags,vfs_context_t c){
 (void)c;assert((v==2||v==3)&&flags==(FREAD|FWRITE));closes++;return scenario==27?EIO:0;
}
'''
        body = r'''
static void reset(void){lookups=opens=closes=put_count=syncs=writes=reads=copied=0;stored_size=0;}
int main(void){
 present=0;enabled=1;assert(!native_owner_macos_watchdog_policy(0,0x1000)&&!lookups&&!opens);
 present=1;for(enabled=0;enabled<4;enabled++)if(enabled!=1){
  assert(!native_owner_macos_watchdog_policy(0,0x1000)&&!lookups&&!opens);
 }
 enabled=1;scenario=0;reset();assert(!native_owner_macos_watchdog_policy(0,0x1000));
 assert(native_owner_watchdog_policy_status==0&&opens==2&&closes==2&&syncs==2&&writes==1&&reads==1);
 assert(lookups==3&&put_count==1&&copied==1);
 assert(strstr(stored,"<key>com.apple.watchdogd</key><true/>"));
 int errors[]={0,EEXIST,EEXIST,EACCES,EFAULT,EROFS,ENOENT,EINVAL,EINVAL,EINVAL,EINVAL,EINVAL,
 EEXIST,EPERM,EIO,EIO,EIO,EINVAL,EINVAL,EINVAL,EINVAL,EINVAL,EINVAL,EIO,EIO,EEXIST,EIO,EIO};
 for(scenario=1;scenario<=27;scenario++){
  reset();int err=native_owner_macos_watchdog_policy(0,0x1000);
  assert(err==errors[scenario]&&native_owner_watchdog_policy_status==err);
  assert(opens<=1||(scenario==25&&opens==2));
  if(scenario<=11)assert(!opens&&!writes&&!reads&&!syncs);
  if(scenario>=13&&scenario!=25)assert(closes==1||scenario==26);
 }
 reset();assert(native_owner_watchdog_file("bad",(const uint8_t*)"",257,0)==EINVAL&&!opens);
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            src, binary = Path(directory) / 'policy.c', Path(directory) / 'policy'
            src.write_text(common + source + body)
            build = subprocess.run([compiler, '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                                    str(src), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([binary], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == '__main__':
    unittest.main()
