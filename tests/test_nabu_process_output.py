"""Run the actual stderr helper against filtered, vectored and late output."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ProcessOutput(unittest.TestCase):
    def test_real_header(self):
        cc = shutil.which('clang') or shutil.which('cc')
        if not cc:
            self.skipTest('requires C compiler')
        header = (ROOT / 'platform/nabu/macos26/log/process_output.h').read_text()
        header = '\n'.join(line for line in header.splitlines() if not line.startswith('#include'))
        harness = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
#define MAXCOMLEN 16
#define TUNABLE(t,n,k,v) t n = v
#define LCK_GRP_DECLARE(n,s) int n __attribute__((unused))
#define LCK_MTX_DECLARE(n,g) int n
#define lck_mtx_lock(p) ((void)(p))
#define lck_mtx_unlock(p) ((void)(p))
struct proc {char p_comm[17];};
typedef uintptr_t user_addr_t;
typedef size_t user_size_t;
struct vector {user_addr_t address;size_t bytes;};
struct uio {struct vector *vectors;int count;};
typedef struct uio *uio_t;
static uint64_t now;
static unsigned lines, reads;
static unsigned captured, capture_calls, root=1;
static char ram[1100000];
static int kauth_cred_get(void){return 1;}
static int kauth_cred_issuser(int c){(void)c;return root;}
static void nabu_capture_append(const char *s,unsigned n){memcpy(ram+captured,s,n);captured+=n;capture_calls++;}
static char output[8192];
static uint64_t mach_absolute_time(void){return now;}
static void absolutetime_to_nanoseconds(uint64_t t,uint64_t *n){*n=t;}
static int proc_getpid(struct proc *p){(void)p;return 5;}
static int copyin(user_addr_t a,void *p,size_t n){reads++;if(!a)return 1;memcpy(p,(void*)a,n);return 0;}
static int uio_iovcnt(uio_t u){return u->count;}
static int uio_getiov(uio_t u,int i,user_addr_t*a,user_size_t*n){*a=u->vectors[i].address;*n=u->vectors[i].bytes;return 0;}
static int trace_printf(const char *f,...){char b[512];va_list a;va_start(a,f);vsnprintf(b,sizeof(b),f,a);va_end(a);strcat(output,b);lines++;return 0;}
#define printf trace_printf
'''
        body = r'''
int main(void){
 struct proc p={"mount_apfs"};const char *s="mount failed\n";
 sheng_process_output(&p,2,(user_addr_t)s,strlen(s),1,"boot-filesystems");assert(!lines&&!reads);
 nabu_boot_filesystem_output=2;
 sheng_process_output(&p,2,(user_addr_t)s,strlen(s),1,"boot-filesystems");assert(!lines);
 nabu_boot_filesystem_output=1;
 sheng_process_output(&p,1,(user_addr_t)s,strlen(s),1,"boot-filesystems");assert(!lines);
 for(int i=0;i<12;i++)sheng_process_output(&p,2,(user_addr_t)s,strlen(s),1,"boot-filesystems");
 assert(lines==8);
 now=600000000000ULL;
 sheng_process_output(&p,2,(user_addr_t)s,strlen(s),1,"boot-filesystems");
 assert(lines==9&&strstr(output,"suppressed=4")&&strstr(output,"mount failed "));
 strcpy(p.p_comm,"fskitd");
 sheng_process_output(&p,2,(user_addr_t)s,strlen(s),1,"boot-filesystems");assert(lines==10);
 strcpy(p.p_comm,"xpcproxy");unsigned old=reads;
 sheng_process_output(&p,2,(user_addr_t)s,strlen(s),1,"boot-filesystems");assert(lines==10&&reads==old);
 strcpy(p.p_comm,"mount");
 struct vector v[]={{(user_addr_t)"abc",3},{(user_addr_t)"def",3}};struct uio u={v,2};
 sheng_process_outputv(&p,2,&u,1,"boot-filesystems");assert(lines==11&&strstr(output,"abcdef"));
 sheng_process_output(&p,2,0,10,1,"boot-filesystems");assert(lines==11);
 strcpy(p.p_comm,"other");
 sheng_process_output(&p,2,(user_addr_t)s,strlen(s),1,"other");assert(lines==12);
 assert(nabu_output_kind("mount.extra")<0);
 const char *names[]={"cfprefsd","digest-service","eligibilityd","endpointsecurity","opendirectoryd","loginwindow"};
 for(unsigned i=0;i<6;i++){
  strcpy(p.p_comm,names[i]);unsigned before=lines;
  sheng_process_output(&p,2,(user_addr_t)s,strlen(s),1,"boot-filesystems");
  assert(lines==before+1);
 }
 assert(nabu_output_kind("endpointsecurityd")==nabu_output_kind("endpointsecurity"));
 assert(nabu_output_kind("endpointsecurity.extra")<0);

 /* The dedicated owner diagnostic stream survives console-off and ignores
  * the old stderr selector, while rejecting other processes/fds/records. */
 strcpy(p.p_comm,"apfs_boot_util");
 const char raw[]="NABU-RAWSTACK registers tid=3 pc=0x1234 lr=0x9999\n";
 unsigned printed=lines;
 sheng_process_output(&p,2,(user_addr_t)raw,sizeof(raw)-1,0,"");assert(!captured);
 nabu_probe_output=1;
 root=0;sheng_process_output(&p,2,(user_addr_t)raw,sizeof(raw)-1,0,"");assert(!captured);root=1;
 sheng_process_output(&p,1,(user_addr_t)raw,sizeof(raw)-1,0,"");assert(!captured);
 sheng_process_output(&p,2,(user_addr_t)"NABU-RAWSTACK bad\nsecond\n",25,0,"");assert(!captured);
 sheng_process_output(&p,2,(user_addr_t)"other output\n",13,0,"");assert(!captured);
 char over[513];memset(over,'x',sizeof(over));
 sheng_process_output(&p,2,(user_addr_t)over,sizeof(over),0,"");assert(!captured);
 strcpy(p.p_comm,"bash");sheng_process_output(&p,2,(user_addr_t)raw,sizeof(raw)-1,0,"");assert(!captured);
 strcpy(p.p_comm,"apfs_boot_util");
 sheng_process_output(&p,2,0,sizeof(raw)-1,0,"");assert(!captured);
 sheng_process_output(&p,2,(user_addr_t)raw,sizeof(raw)-1,0,"");
 assert(captured==sizeof(raw)-1&&capture_calls==1&&!memcmp(ram,raw,sizeof(raw)-1)&&lines==printed);
 nabu_probe_output_bytes=1048576-(sizeof(raw)-1);
 sheng_process_output(&p,2,(user_addr_t)raw,sizeof(raw)-1,0,"");assert(nabu_probe_output_bytes==1048576&&capture_calls==2);
 nabu_probe_output_bytes=1048576-1;
 sheng_process_output(&p,2,(user_addr_t)raw,sizeof(raw)-1,0,"");assert(capture_calls==3&&strstr(ram,"global-1MiB-budget"));
 sheng_process_output(&p,2,(user_addr_t)raw,sizeof(raw)-1,0,"");assert(capture_calls==3);

 return 0;
}
'''
        with tempfile.TemporaryDirectory() as d:
            source, binary = Path(d) / 'output.c', Path(d) / 'output'
            source.write_text(harness + header + body)
            result = subprocess.run([cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                                     '-Wno-format', str(source), '-o', str(binary)],
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
