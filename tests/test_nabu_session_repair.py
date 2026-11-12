"""Run actual prepared XNU time and filesystem functions with host fixtures."""
from pathlib import Path
import os
import subprocess
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('NABU_SESSION_SOURCE', ROOT / 'out/nabu-session-repair/verify/source'))


def function(path, name):
    if not (SOURCE / path).exists():
        pytest.skip('requires the prepared 25F71 session-repair source')
    text = (SOURCE / path).read_text()
    start = text.index('\n' + name + '(') + 1
    start = text.rfind('\n', 0, start - 1) + 1
    opening = text.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end] + '\n'


def compile_run(source, include=SOURCE / 'bsd/kern'):
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        (work / 'test.c').write_text(source)
        subprocess.run(['cc', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-I', str(include), str(work / 'test.c'), '-o', str(work / 'test')], check=True)
        subprocess.run([str(work / 'test')], check=True)


def test_real_microtime_conversion():
    body = function('osfmk/arm/rtclock.c', 'absolutetime_to_microtime')
    compile_run('''
#include <assert.h>
#include <stdint.h>
typedef uint64_t clock_sec_t;
typedef uint32_t clock_usec_t;
#define USEC_PER_SEC 1000000
static uint32_t rtclock_sec_divisor;
''' + body + '''
int main(void) {
    uint64_t frequencies[] = {19200000,24000000};
    for (unsigned i=0; i<2; i++) {
        uint64_t f=frequencies[i], previous=0;
        rtclock_sec_divisor=(uint32_t)f;
        for(uint64_t tick=0; tick<=2*f; tick++) {
            clock_sec_t sec; clock_usec_t usec;
            absolutetime_to_microtime(tick,&sec,&usec);
            uint64_t got=sec*1000000+usec;
            assert(usec<1000000 && got>=previous);
            assert(got==tick*1000000/f);
            previous=got;
        }
    }
}
''')


def test_real_calendar_floor_preserves_valid_platform_time():
    body = function('bsd/kern/kern_time.c', 'inittodr')
    compile_run('''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#define NABU 1
#define __unused __attribute__((unused))
typedef int64_t time_t;
struct timeval {time_t tv_sec; int tv_usec;};
#include "nabu_boot_epoch.h"
static struct timeval supplied, applied;
static unsigned writes;
static void microtime(struct timeval *v) {*v=supplied;}
static void setthetime(struct timeval *v) {applied=*v;writes++;}
''' + body + '''
int main(void) {
    supplied=(struct timeval){0,700000}; inittodr(0);
    assert(writes==1 && applied.tv_sec==NABU_BUILD_EPOCH && !applied.tv_usec);
    supplied=(struct timeval){NABU_BUILD_EPOCH+100,123456}; inittodr(0);
    assert(writes==1); /* preserve real RTC later than the build floor */
    supplied=(struct timeval){NABU_BUILD_EPOCH,999999}; inittodr(0);
    assert(writes==1);
}
''')


def test_real_union_identity_and_distinct_kext_statfs_views():
    identity = function('bsd/vfs/vfs_subr.c', 'native_union_identity')
    visible = function('bsd/vfs/vfs_subr.c', 'native_union_visflags')
    flags = function('bsd/vfs/kpi_vfs.c', 'vfs_flags')
    compile_run('''
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#define MNT_UNION 1
#define MNT_ROOTFS 2
#define MNT_RDONLY 4
#define MNT_VISFLAGMASK 7
#define MNT_CMDFLAGS 0
#define VROOT 1
#define NULLVP NULL
struct vnode;
struct mount {uint64_t mnt_flag; struct vnode *mnt_vnodecovered;};
typedef struct mount *mount_t;
struct vnode {unsigned v_flag; mount_t v_mount;};
typedef struct vnode *vnode_t;
static vnode_t rootvnode;
''' + identity + visible + flags + '''
int main(void) {
    struct mount lower={MNT_ROOTFS|MNT_RDONLY,0}, upper={MNT_UNION|MNT_RDONLY,0}, data={0,0};
    struct vnode covered={VROOT,&lower}, root={VROOT,&upper};
    upper.mnt_vnodecovered=&covered;rootvnode=&root;
    assert(native_union_identity(&lower)==&upper);
    assert(native_union_identity(&upper)==&upper && native_union_identity(&data)==&data);
    assert(native_union_visflags(&upper)==(MNT_UNION|MNT_ROOTFS|MNT_RDONLY));
    assert(vfs_flags(&upper)==MNT_RDONLY); /* sandbox sees physical FS semantics */
    assert(upper.mnt_flag==(MNT_UNION|MNT_RDONLY)); /* libc still merges the union */
    rootvnode=0;assert(native_union_identity(&lower)==&lower);
}
''')


def test_session_observer_selection_and_bounded_output():
    compile_run(r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#define NABU_SESSION_TRACE_HOST_TEST 1
static int enabled, selected;
static unsigned writes;
static char journal[100000];
static size_t used;
static int PE_parse_boot_argn(const char *name,void *out,int size) {
 assert(!strcmp(name,"nabu-session-log") && size==4);*(unsigned *)out=enabled;return 1;
}
static int proc_selfpid(void) {return 103;}
static int current_thread(void) {return 1;}
static uint64_t thread_tid(int t) {return t+11;}
static uint64_t mach_absolute_time(void) {return 900;}
static void absolutetime_to_nanoseconds(uint64_t t,uint64_t *out) {*out=t;}
static void proc_name(int pid,char *out,int size) {
 assert(pid==103);snprintf(out,size,"%s",selected?"loginwindow":"lsd");
}
static void nabu_capture_append(const char *s,unsigned count) {
 assert(count<384 && used+count<sizeof journal);memcpy(journal+used,s,count);used+=count;journal[used]=0;writes++;
}
#include "session_trace.h"
int main(void) {
 nabu_session_emit(0,"event=kauth-submit seq=1\n");assert(!writes);
 enabled=1;nabu_session_ipc("ipc-enter",3,42,1,2,0);assert(!writes);
 selected=1;nabu_session_ipc("ipc-enter",3,42,1,2,0);
 assert(strstr(journal,"pid=103 tid=12 t_ns=900") && strstr(journal,"msgid=42"));
 for(unsigned i=0;i<1000;i++) nabu_session_ipc("ipc-enter",3,42,1,2,0);
 assert(writes==128);
 nabu_session_ipc("ipc-return",3,42,1,2,0x10004003);
 assert(writes==129 && strstr(journal,"result=10004003"));
}
''', ROOT / 'platform/nabu/macos26/log')
