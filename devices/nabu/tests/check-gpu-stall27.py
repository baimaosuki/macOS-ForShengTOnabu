#!/usr/bin/env python3
"""Execute A640 two-sample confirmation and locked active-IB capture."""
import argparse,json,subprocess,tempfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();d=Path(__file__).resolve().parents[1];gpu=d/'package/gpu'
def body(t,sig):
 start=t.index(sig);at=t.index('{',start)+1;depth=1
 while depth:depth+=(t[at]=='{')-(t[at]=='}');at+=1
 return t[start:at]
peer=r'''
#include <cassert>
#include <cstdint>
#include <cstddef>
#include <cstdarg>
#include <cstring>
#include <cstdio>
#include <vector>
#include "NabuGpuFault.h"
#include "NabuGpuWatchdog.h"
#include <NativeAdrenoVm.h>
#include "NabuGpuActiveIb.h"
static bool locked=false;static unsigned peeks=0;static std::vector<unsigned> offsets;
using IOReturn=int;enum{kIOReturnBadArgument=1,kIOReturnNotReady=2,kIOReturnSuccess=0,kIOReturnIOError=3};
struct Backing{uint32_t words[512];bool imported_=false;void *mapping(){return words;}size_t bytes(){return sizeof(words);}bool imported(){return imported_;}};
class NativeAdrenoVm {
public:
 struct Mapping{Backing *memory;uint64_t handle=0x1234,iova=0x3037d000;unsigned mapped_spans=1,spans=1,jobs=2;};
 Backing backing{};Mapping map{&backing};bool attached_=true,poisoned_=false,failRead=false;
 const Mapping *findAddressRecord(uint64_t at)const{return locked && at>=map.iova && at-map.iova<sizeof(backing.words)?&map:nullptr;}
 IOReturn describeAddress(uint64_t,NativeAdrenoVmAddress*)const;
 IOReturn peekWords(uint64_t at,uint32_t *out,size_t n)const{
  assert(locked && !backing.imported_);++peeks;unsigned off=(at-map.iova)/4;offsets.push_back(off);assert(off+n<=512);
  if(failRead)return 77;for(size_t i=0;i<n;++i)out[i]=backing.words[off+i];return 0;
 }
};
static NativeAdrenoVm vm;
struct Status{void noteFault(){assert(locked);}};
struct Ring{void recordFault(){assert(locked);}};
struct Scheduler{void fault(int,bool){assert(locked && peeks);vm.poisoned_=true;}};
struct Submit{Scheduler scheduler_;Scheduler *scheduler(){return &scheduler_;}};
struct Global{bool published=true,failed=false;void *lock=(void*)1,*completionCall=(void*)2;Status status;Ring ring_;Submit submit_;Ring *ring=&ring_;Submit *submit=&submit_;NativeAdrenoVm *vm=&::vm;} g;
static void IOLockLock(void*){assert(!locked);locked=true;}static void IOLockUnlock(void*){assert(locked);locked=false;}
static void IOLockWakeup(void*,void*,bool){assert(locked);}static void thread_call_enter(void*){assert(!locked);}
static void IOLog(const char*,...){assert(!locked);}
'''
test=r'''
int main(){
 NabuGpuExecutionState state;state.valid=(1u<<NabuGpuExecutionState::Count)-1;state.words[2]=0x3037d000;state.words[5]=0x3037d000;state.words[8]=state.words[9]=512;
 state.words[4]=state.words[7]=128;state.words[10]=state.words[11]=16u<<16;
 NabuGpuStallConfirmation confirm;
 assert(confirm.sample(450000,450000,5540,state)==NabuGpuStallConfirmation::Waiting);
 assert(confirm.sample(470000,470000,5540,state)==NabuGpuStallConfirmation::Waiting);
 assert(confirm.sample(509701,509701,5540,state)==NabuGpuStallConfirmation::Stopped);
 auto moving=state;--moving.words[4];assert(confirm.sample(509701,509701,5540,moving)==NabuGpuStallConfirmation::Moving);
 moving=state;moving.words[12]^=1u<<18;assert(confirm.sample(509701,509701,5540,moving)==NabuGpuStallConfirmation::Stopped);
 moving.valid&=~(1u<<16);assert(confirm.sample(509701,509701,5540,moving)==NabuGpuStallConfirmation::Unreadable);
 assert(confirm.sample(600000,0,5541,state)==NabuGpuStallConfirmation::Waiting);
 assert(confirm.sample(1200000,600000,5541,state)==NabuGpuStallConfirmation::Waiting);
 assert(confirm.sample(1220000,620000,5541,state)==NabuGpuStallConfirmation::Waiting);
 assert(confirm.sample(1230000,630000,5541,state)==NabuGpuStallConfirmation::Stopped);
 NabuGpuWatchdog watchdog;assert(!watchdog.sample(0,5543,5540,500000));
 for(unsigned i=0;i<10;++i){assert(watchdog.executionProgress(100+i));assert(watchdog.executionRetries()==i+1);}
 assert(!watchdog.executionProgress(200));assert(watchdog.sample(500110,5543,5540,500000));
 assert(!watchdog.sample(600000,5543,5541,500000) && !watchdog.executionRetries());
 for(unsigned i=0;i<512;++i)vm.backing.words[i]=0x70000000+i;
 locked=true;auto ib=NabuGpuCaptureActiveIb(vm,state,0);locked=false;
 assert(!ib.status && ib.address==0x3037d000 && ib.count==3 && ib.cursorHint==368 && ib.boundedWords==512);
 assert(ib.windows[0].at==0 && ib.windows[1].at==352 && ib.windows[2].at==480);
 for(auto &w:ib.windows)assert(w.count==32 && w.words[0]==0x70000000+w.at);
 peeks=0;locked=true;vm.backing.imported_=true;assert(NabuGpuCaptureActiveIb(vm,state,0).status==3 && !peeks);
 vm.backing.imported_=false;vm.failRead=true;ib=NabuGpuCaptureActiveIb(vm,state,0);for(auto &w:ib.windows)assert(!w.count && w.status==77);
 vm.failRead=false;auto edge=state;edge.words[2]+=511*4;edge.words[8]=1;edge.words[4]=1;edge.words[10]=0;
 ib=NabuGpuCaptureActiveIb(vm,edge,0);assert(ib.count==1 && ib.windows[0].count==1 && ib.boundedWords==1);
 edge.words[2]+=4;assert(NabuGpuCaptureActiveIb(vm,edge,0).status);locked=false;
 peeks=0;NabuGpuCpFault fault;fault.execution=state;native_adreno_fault_snapshot(&fault);
 assert(g.failed && vm.poisoned_ && peeks==6 && !locked);
 puts("PASS actual two-sample confirmation, execution-only progress, bounded retries, locked owned-BO head/cursor/tail capture before teardown");
}
'''
a.output.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='nabu-stall-') as td:
 t=Path(td)
 (t/'NativeAdrenoVm.h').write_text('#pragma once\n#define NATIVE_ADRENO_VM_H\n#include <stdint.h>\n#include <stddef.h>\nstruct NativeAdrenoVmAddress{uint64_t handle=0,base=0;size_t bytes=0,offset=0;unsigned jobs=0;bool owned=false;};\n')
 vm_src=(gpu/'a640/NativeAdrenoVm.cpp').read_text();pub=(gpu/'a640/NativeAdrenoPublish.cpp').read_text()
 # The publication function also wakes its completion event.
 source=peer.replace('struct Global{','struct Global{int completionEvent=0;')+body(vm_src,'IOReturn NativeAdrenoVm::describeAddress(')+body(pub,'extern "C" void native_adreno_fault_snapshot(')+test
 (t/'test.cpp').write_text(source)
 subprocess.run(['c++','-std=c++17','-O1','-g','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-fsanitize=address,undefined','-I',str(t),'-I',str(gpu/'a640'),str(t/'test.cpp'),'-o',str(t/'test')],check=True)
 q=subprocess.run([str(t/'test')],capture_output=True,text=True);(a.output/'run.log').write_text(q.stdout+q.stderr);assert not q.returncode,q.stderr;print(q.stdout,end='')
(a.output/'verification.json').write_text(json.dumps(dict(passed=True,two_samples_minimum_us=30000,fence_budget_us=500000,
 actual_vm_lookup=True,actual_publication_fault_path=True,head_cursor_tail=True,locked_capture_before_teardown=True,
 no_new_physical_mapping=True,failed_reads_capture_zero_words=True,imported_mmio_refused=True,
 busy_flags_not_execution_progress=True,execution_retries_bounded=10,sanitizers=['ASan','UBSan'],hardware_tested=False),indent=2)+'\n')
