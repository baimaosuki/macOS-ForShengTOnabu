"""Execute the patched trap wrapper, including its non-returning continuation."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
TREE = Path(os.environ.get('NABU_OD_WAIT_SOURCE', ROOT / '.work/fix/tree'))
SOURCE = TREE / 'osfmk/kern/sync_sema.c'
HEADER = ROOT / 'platform/nabu/macos26/log'
PATCH = ROOT / 'platform/nabu/macos26/patches/zzzzzzzz-nabu-od-wait-trace.patch'


def test_actual_relative_deadline_uses_192mhz_absolute_time():
    """The real XNU formulas must agree across ns/ticks and saturate overflow."""
    tree = TREE
    if not SOURCE.exists():
        pytest.skip('requires the published XNU clock source')

    def body(path, name):
        text = (tree / path).read_text()
        start = text.index('\n' + name + '(') + 1
        start = text.rfind('\n', 0, start - 1) + 1
        opening = text.index('{', start)
        depth, end = 1, opening + 1
        while depth:
            depth += (text[end] == '{') - (text[end] == '}')
            end += 1
        return text[start:end] + '\n'

    fixture = r'''
#include <assert.h>
#include <stdint.h>
typedef uint64_t clock_sec_t;
typedef uint32_t clock_nsec_t;
#define NSEC_PER_SEC 1000000000ULL
#define os_add_overflow __builtin_add_overflow
static uint32_t rtclock_sec_divisor;
static uint64_t counter;
static uint64_t mach_absolute_time(void) {return counter;}
''' + body('osfmk/arm/rtclock.c', 'nanotime_to_absolutetime') + body(
        'osfmk/kern/clock.c', 'clock_absolutetime_interval_to_deadline') + r'''
int main(void) {
    uint32_t frequencies[]={19200000,24000000};
    for(unsigned f=0;f<2;f++) {
        rtclock_sec_divisor=frequencies[f];
        for(unsigned sec=0;sec<10;sec++) {
            for(unsigned ns=0;ns<1000000000U;ns+=99991) {
                uint64_t interval, deadline;
                nanotime_to_absolutetime(sec,ns,&interval);
                assert(interval==((uint64_t)sec*1000000000+ns)*frequencies[f]/1000000000);
                counter=(uint64_t)frequencies[f]*238;
                clock_absolutetime_interval_to_deadline(interval,&deadline);
                assert(deadline-counter==interval);
            }
        }
    }
    uint64_t deadline;counter=UINT64_MAX-10;
    clock_absolutetime_interval_to_deadline(11,&deadline);assert(deadline==UINT64_MAX);
    clock_absolutetime_interval_to_deadline(9,&deadline);assert(deadline==UINT64_MAX-1);
}
'''
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        source, binary = work / 'test.c', work / 'test'
        source.write_text(fixture)
        subprocess.run(['cc', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=undefined', str(source), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


def test_original_trap_arguments_results_continuation_and_bounded_capture():
    if not SOURCE.exists():
        pytest.skip('requires the original published XNU sync_sema.c')
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        target = work / 'osfmk/kern/sync_sema.c'
        target.parent.mkdir(parents=True)
        shutil.copyfile(SOURCE, target)
        subprocess.run(['patch', '--fuzz=0', '-p1', '-d', str(work), '-i', str(PATCH)], check=True)
        text = target.read_text()
        start = text.index('kern_return_t\nsemaphore_timedwait_trap(')
        end = text.index('\n}\n', start) + 3
        wrapper = text[start:end]
        fixture = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <setjmp.h>
#define NABU 1
#define NABU_OD_WAIT_TRACE_HOST_TEST 1
typedef int kern_return_t;
typedef unsigned long clock_sec_t;
typedef unsigned clock_nsec_t;
typedef struct {unsigned numer, denom;} mach_timebase_info_data_t;
struct semaphore_timedwait_trap_args {unsigned wait_name,sec,nsec;};
static int enabled, selected, block, answer, returned;
static unsigned writes, forwarded;
static uint64_t ticks, tid=100;
static char journal[300000];
static size_t used;
static jmp_buf resume;
static int PE_parse_boot_argn(const char *s,void *out,int size) {
    assert(!strcmp(s,"nabu-od-wait-log") && size==4);*(unsigned *)out=enabled;return 1;
}
static int proc_selfpid(void) {return 68;}
static void proc_name(int p,char *out,int size) {
    assert(p==68);snprintf(out,size,"%s",selected?"opendirectoryd":"WindowServer");
}
static int current_thread(void) {return 1;}
static uint64_t thread_tid(int t) {assert(t==1);return tid;}
static uint64_t mach_absolute_time(void) {return ticks;}
static void absolutetime_to_nanoseconds(uint64_t t,uint64_t *out) {*out=t*625/12;}
static void clock_get_calendar_nanotime(clock_sec_t *s,clock_nsec_t *ns) {*s=1794528000;*ns=123456789;}
static void clock_timebase_info(mach_timebase_info_data_t *r) {*r=(mach_timebase_info_data_t){625,12};}
static void nabu_capture_append(const char *s,unsigned n) {
    assert(n<384 && used+n<sizeof(journal));memcpy(journal+used,s,n);used+=n;journal[used]=0;writes++;
}
static void thread_syscall_return(kern_return_t result) {
    returned=result;longjmp(resume,1);
}
#include "od_wait_trace.h"
static kern_return_t semaphore_timedwait_trap_internal(unsigned name,unsigned sec,unsigned ns,
    void (*continuation)(kern_return_t)) {
    assert(name==0x123 && sec==4 && ns==999999999);forwarded++;
    ticks+=96000000; /* exact five seconds at the real 19.2 MHz */
    if(block) continuation(answer);
    return answer;
}
''' + wrapper + r'''
int main(void) {
    struct semaphore_timedwait_trap_args args={0x123,4,999999999};
    answer=17;
    assert(semaphore_timedwait_trap(&args)==17 && forwarded==1 && writes==0);
    enabled=1;assert(semaphore_timedwait_trap(&args)==17 && writes==0);
    selected=1;ticks=19200000;
    assert(semaphore_timedwait_trap(&args)==17 && writes==2);
    assert(strstr(journal,"sec=4 nsec=999999999 abs=19200000 t_ns=1000000000"));
    assert(strstr(journal,"calendar=1794528000.123456789 numer=625 denom=12"));
    assert(strstr(journal,"result=17 continued=0 abs=115200000 elapsed_ns=5000000000"));
    block=1;answer=49;
    if (!setjmp(resume)) {semaphore_timedwait_trap(&args);assert(0);}
    assert(returned==49 && writes==4);
    assert(strstr(journal,"result=49 continued=1"));
    block=0;answer=0;
    /* Full-table admission never wraps a continuation or writes a false end. */
    for(unsigned i=0;i<NABU_OD_WAIT_SLOTS;i++) nabu_od_wait_slots[i].tid=tid+1+i;
    assert(semaphore_timedwait_trap(&args)==0 && writes==4);
    memset(nabu_od_wait_slots,0,sizeof(nabu_od_wait_slots));
    /* Admit the final entry, then prove its return survives exhausted budget. */
    nabu_od_wait_entries=NABU_OD_WAIT_LIMIT-1;
    assert(nabu_od_wait_begin(0x123,4,999999999));
    assert(!nabu_od_wait_begin(0x123,4,999999999));
    nabu_od_wait_end(14,1);assert(writes==6);
    for(unsigned i=0;i<1000;i++) assert(semaphore_timedwait_trap(&args)==0);
    assert(writes==6 && strstr(journal,"result=14 continued=1"));
}
'''
        source = work / 'test.c'
        source.write_text(fixture)
        binary = work / 'test'
        subprocess.run(['cc', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', '-I', str(HEADER), str(source),
                        '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
