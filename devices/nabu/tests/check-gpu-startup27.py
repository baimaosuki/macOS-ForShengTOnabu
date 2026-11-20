#!/usr/bin/env python3
"""Exercise actual late GPU launch guards and the fixed-snapshot persistence gate."""
import argparse,json,subprocess,tempfile,shutil,re
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--xnu',type=Path,required=True)
p.add_argument('--output',type=Path,required=True);a=p.parse_args()
d=Path(__file__).resolve().parents[1];g=d/'package/gpu'
service=(g/'a640/NativeAdrenoService.cpp').read_text()
def method(text,signature):
    begin=text.index(signature);at=text.index('{',begin);depth=1;end=at+1
    while depth:
        depth+=(text[end]=='{')-(text[end]=='}');end+=1
    return text[begin:end]
start=method(service,'extern "C" void native_adreno_a6xx_start(void)')
wrapper=(g/'nabu/NabuGpuStart.c').read_text()
assert 'STARTUP' not in wrapper and 'kernel_thread' not in wrapper
late=method((d/'package/LegacyDisplayProbe.cpp').read_text(),'void native_legacy_display_late_probe(void) {')
assert late.index('nabu_gpu_start_after_iobsd();')<late.index('pendingLegacyDisplay')
worker=method(service,'void hardwareWorker(')
assert worker.index('native_adreno_wait_log_ready()')<worker.index('new A640State')<worker.index('state->start()')
resource=(g/'nabu/NabuGpuResources.cpp').read_text()
assert 'waitPersistence("startup-log",120000)' in resource
assert 'waitPersistence(phase,5000)' in resource
# Do not repeat the audit's enum misread: startup records use EARLY_BOOT=18,
# while arm_init's first #4 barrier is KPRINTF, before the video console.
header=(a.xnu/'osfmk/kern/startup.h').read_text()
enum=header[header.index('__enum_decl(startup_subsystem_id_t'):header.index('STARTUP_SUB_LOCKDOWN')]
names=re.findall(r'^\s*(STARTUP_SUB_\w+)\b',enum,re.M)
assert names.index('STARTUP_SUB_KPRINTF')==4 and names.index('STARTUP_SUB_EARLY_BOOT')==18
arm=(a.xnu/'osfmk/arm/arm_init.c').read_text()
assert 'kernel_startup_initialize_upto(STARTUP_SUB_EARLY_BOOT)' not in arm
startup=(a.xnu/'osfmk/kern/startup.c').read_text()
assert startup.index('task_init();')<startup.index('thread_init();')<startup.index('kernel_startup_initialize_upto(STARTUP_SUB_EARLY_BOOT);')
prefix=r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include "NabuGpuPersistence.h"
static bool enabled=false,resourceAvailable=false,iobsd=false,threadFails=false;
static unsigned threads=0,deallocated=0;static uint32_t launched=0;
using wait_result_t=int;using thread_t=void *;using kern_return_t=int;
enum {KERN_SUCCESS=0};
struct IOService {
 static IOService *getResourceService(){static IOService resource;return resourceAvailable?&resource:nullptr;}
 void *getProperty(const char *){return iobsd?this:nullptr;}
};
static unsigned native_adreno_a6xx_stats[32];
static bool PE_parse_boot_argn(const char *,void *out,int){*static_cast<unsigned *>(out)=enabled;return true;}
static void registrationEntry(void *,wait_result_t){}
static int kernel_thread_start(void (*entry)(void *,wait_result_t),void *,thread_t *thread){
 assert(resourceAvailable && iobsd && entry==registrationEntry);++threads;*thread=reinterpret_cast<void *>(1);return threadFails?5:0;
}
static void thread_deallocate(thread_t){++deallocated;}
static void IOLog(const char *,...){}
'''
test=r'''
struct Peer {
 unsigned now=0,readyAt=8000,commitAt=10000,stopAt=~0u,calls=0;bool neverSnapshot=false;
 unsigned writerState(){return now>=stopAt?3:now>=readyAt?2:now>=1000?1:0;}
 bool snapshotEnd(uint64_t &value){++calls;if(neverSnapshot || calls<=2)return false;value=100+calls;return true;}
 uint64_t committedEnd(){return now>=commitAt?103:0;}
 void sleepMs(unsigned ms){assert(ms && ms<=20);now+=ms;}
};
int main(){
 nabu_gpu_start_after_iobsd();assert(!threads); // CPU boot, before resources
 enabled=true;nabu_gpu_start_after_iobsd();assert(!threads && !launched);
 resourceAvailable=true;nabu_gpu_start_after_iobsd();assert(!threads && !launched);
 iobsd=true;threadFails=true;nabu_gpu_start_after_iobsd();assert(threads==1 && !launched && !deallocated);
 threadFails=false;nabu_gpu_start_after_iobsd();assert(threads==2 && launched==1 && deallocated==1);
 nabu_gpu_start_after_iobsd();assert(threads==2 && deallocated==1);
 Peer peer;NabuGpuPersistenceReport report;
 assert(nabuGpuWaitPersistence(peer,120000,report)==NabuGpuPersistenceResult::Ready);
 assert(peer.now==10000 && report.target==103 && report.committed==103 && peer.calls==3);
 peer={};peer.readyAt=140000;
 assert(nabuGpuWaitPersistence(peer,120000,report)==NabuGpuPersistenceResult::Timeout && peer.now==120000 && !report.target);
 peer={};peer.stopAt=3000;
 assert(nabuGpuWaitPersistence(peer,120000,report)==NabuGpuPersistenceResult::Stopped && peer.now==3000);
 peer={};peer.readyAt=0;peer.neverSnapshot=true;
 assert(nabuGpuWaitPersistence(peer,5000,report)==NabuGpuPersistenceResult::Timeout && peer.now==5000);
 peer={};peer.readyAt=0;peer.commitAt=2000;
 assert(nabuGpuWaitPersistence(peer,5000,report)==NabuGpuPersistenceResult::Ready && peer.now==2000 && peer.calls==3);
 peer={};peer.readyAt=0;peer.commitAt=20000;
 assert(nabuGpuWaitPersistence(peer,5000,report)==NabuGpuPersistenceResult::Timeout && peer.now==5000 && report.target==103);
 puts("PASS actual CPU/GPU launch guards: no early callback, real IOBSD, once/retry; delayed logger, fixed target, commit, contention and bounded failure paths");
}
'''
a.output.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='nabu-gpu-startup-') as temporary:
    root=Path(temporary);cpp=root/'test.cpp';cpp.write_text(prefix+start+wrapper+test)
    cmd=[shutil.which('clang++') or 'c++','-std=c++17','-O1','-g','-Wall','-Wextra','-Werror',
         '-fsanitize=address,undefined','-I',str(g/'nabu'),str(cpp),'-o',str(root/'test')]
    subprocess.run(cmd,check=True)
    result=subprocess.run([str(root/'test')],check=True,capture_output=True,text=True)
    (a.output/'run.log').write_text(result.stdout);print(result.stdout,end='')
(a.output/'verification.json').write_text(json.dumps(dict(passed=True,actual_start_guard_executed=True,
    cpu_entry_creates_no_gpu_threads=True,requires_genuine_iobsd=True,thread_creation_failure_retry=True,
    no_gpu_startup_record=True,kprintf_subsystem=4,early_boot_subsystem=18,
    task_thread_init_precede_early_boot=True,fixed_snapshot_commit_gate=True,
    log_startup_timeout_ms=120000,phase_checkpoint_timeout_ms=5000,
    log_ready_delayed_10000ms_passed=True,sanitizers=['ASan','UBSan'],hardware_tested=False),indent=2)+'\n')
