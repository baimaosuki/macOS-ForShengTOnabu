#!/usr/bin/env python3
"""Replay actual lock policy, console ownership, changed-row scanout and Data installer."""
from pathlib import Path
import argparse,json,subprocess,tempfile,plistlib,struct,zlib
ROOT=Path(__file__).resolve().parents[3]
def build(work,name,text):
 p=work/(name+'.c');p.write_text(text);exe=work/name
 subprocess.run(['cc','-std=c11','-D_GNU_SOURCE','-Wall','-Werror','-I',str(ROOT/'scripts/macos-boot-shims'),str(p),'-o',str(exe)],check=True)
 subprocess.run([str(exe)],check=True)
def check(xnu,work):
 locks=(xnu/'osfmk/kern/locks.c').read_text();start=locks.index('__attribute__((always_inline))\nhw_spin_timeout_t\nhw_spin_compute_timeout');end=locks.index('__attribute__((noinline))',start)
 core=locks[start:end].replace('__attribute__((always_inline))','').replace('#if defined(__x86_64__)', '#if 0 /* ARM64 replay */')
 build(work,'lock-policy',r'''
#include <assert.h>
#include <stdint.h>
#define SCHED_HYGIENE_DEBUG 1
#define XNU_MONITOR 0
#define SCHED_HYGIENE_MODE_PANIC 2
#define SCHED_HYGIENE_MODE_TRACE 1
#define relaxed 0
#define os_atomic_load(p,o) (*(p))
unsigned interrupt_masked_debug_mode=2;
uint64_t interrupt_masked_timeout=12000;
static int ml_get_interrupts_enabled(void){return 0;}
static int hw_spin_in_ppl(void *p);
typedef struct {uint64_t hwst_timeout;int hwst_interruptible;} hw_spin_timeout_t;
#define hw_spin_in_ppl(x) 0
struct policy {uint64_t *hwsp_timeout;unsigned hwsp_timeout_shift;};
typedef struct policy *hw_spin_policy_t;
''' .replace('static int hw_spin_in_ppl(void *p);','')+core+r'''
int main(void){uint64_t real=3000000;struct policy p={&real,0};
 assert(hw_spin_compute_timeout(&p).hwst_timeout==12000);
 interrupt_masked_debug_mode=SCHED_HYGIENE_MODE_TRACE;
 assert(hw_spin_compute_timeout(&p).hwst_timeout==3000000);
 real=0;assert(hw_spin_compute_timeout(&p).hwst_timeout==0);
 return 0;}
''')
 arm=(xnu/'osfmk/arm/arm_init.c').read_text();assert '#if defined(NABU)' in arm and 'SCHED_HYGIENE_MODE_TRACE' in arm
 assert 'if (!spinlock_timeout_panic)' not in (xnu/'osfmk/kern/lock_ticket.c').read_text()
 video=(xnu/'osfmk/console/video_console.c').read_text();a=video.index('void\nnabu_console_claim_framebuffer');b=video.index('\n#endif',a);ownership=video[a:b]
 a=video.index('void\nvcputc_options');b=video.index('\nvoid\nvcputc(',a);putc=video[a:b]
 build(work,'console-ownership',r'''
#include <assert.h>
#include <stdbool.h>
#define NABU 1
#define TRUE true
#define FALSE false
#ifndef __unused
#define __unused
#endif
#define VCPUTC_LOCK_LOCK() do{lockdepth++;}while(0)
#define VCPUTC_LOCK_UNLOCK() do{lockdepth--;}while(0)
#define LCK_GRP_NULL 0
#define simple_lock(a,b) do{lockdepth++;}while(0)
#define simple_unlock(a) do{lockdepth--;}while(0)
static int lockdepth,draws;
static bool gc_enabled=true,gc_initialized=true,disableConsoleOutput=false,nabu_console_framebuffer_owned;
static int gc_x,gc_y;
static int boot_progress_active(void){return 0;}
static void boot_progress_putc(char c){(void)c;draws++;}
static void gc_hide_cursor(int x,int y){(void)x;(void)y;}
static void gc_show_cursor(int x,int y){(void)x;(void)y;}
static void gc_putchar(char c){(void)c;draws++;}
'''+ownership+'\n'+putc+r'''
int main(void){vcputc_options('a',false);assert(draws==1);
 nabu_console_claim_framebuffer();assert(!lockdepth && disableConsoleOutput && !gc_enabled);
 vcputc_options('b',false);assert(draws==1);
 nabu_console_panic_takeover();assert(!disableConsoleOutput && gc_enabled);
 vcputc_options('c',false);assert(draws==2);return 0;}
''')
 serial=(xnu/'osfmk/console/serial_console.c').read_text();a=serial.index('console_write(char *str, int size)');snippet=serial[a:a+500];assert snippet.index('nabu_capture_append')<snippet.index('if (disableConsoleOutput)')
 data=work/'Data';data.mkdir();build(work,'desktop-installer',f'#define NABU_DESKTOP_DATA_ROOT {json.dumps(str(data))}\n#include "nabu_static_desktop.h"\nint main(void){{if(nabu_install_static_desktop())return 1;return nabu_install_static_desktop();}}\n')
 desktop=data/'Library/Application Support/NABU/Desktop';png=(desktop/'desktop.png').read_bytes();assert png[:8]==b'\x89PNG\r\n\x1a\n';at=8;raw=b''
 while at<len(png):
  n=struct.unpack_from('>I',png,at)[0];typ=png[at+4:at+8];body=png[at+8:at+8+n];crc=struct.unpack_from('>I',png,at+8+n)[0];assert zlib.crc32(typ+body)&0xffffffff==crc
  if typ==b'IDAT':raw+=body
  at+=12+n
 assert len(zlib.decompress(raw))==16*(16*3+1)
 agent=plistlib.loads((data/'Library/LaunchAgents/org.unixnu.nabu.static-desktop.plist').read_bytes());assert agent['LimitLoadToSessionType']=='Aqua' and agent['KeepAlive']=={'SuccessfulExit':False}
 js=(desktop/'set-desktop.js').read_text();assert 'setDesktopImageURLForScreenOptionsError' in js and 'desktopImageURLForScreen' in js and 'fileExistsAtPath' in js
 scene=(ROOT/'devices/nabu/drivers/GopFramebuffer/nabu_cpu_scene.inc').read_text();assert 'nabuCpuPixelStats' not in scene and 'plane_geometry=' in scene and 'nabu_console_claim_framebuffer();' in scene
 print('PASS actual 500us clamp reproduced; TRACE retains 125ms; physical console handoff/panic; RAM capture retained; static PNG/agent installer idempotent')
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--xnu',type=Path,required=True);a=p.parse_args()
 with tempfile.TemporaryDirectory(prefix='nabu-desktop-test-') as td:check(a.xnu,Path(td))
