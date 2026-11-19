#!/usr/bin/env python3
"""Exercise actual GOP connection ownership, completion and close cleanup."""
import argparse,json,subprocess,tempfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--userspace-wire',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
d=Path(__file__).resolve().parents[1];fb=d/'drivers/GopFramebuffer';text=(fb/'NativeGOPPresenter.cpp').read_text()
def method(signature):
    start=text.index(signature);brace=text.index('{',start);end=brace+1;depth=1
    while depth:
        depth+=(text[end]=='{')-(text[end]=='}');end+=1
    return text[start:end]
methods='\n'.join(method(n) for n in ['IOReturn NativeGOPPresenter::handoffCall(',
    'IOMemoryDescriptor *NativeGOPPresenter::handoffMemory(', 'IOReturn NativeGOPPresenter::ownedCall(',
    'IOMemoryDescriptor *NativeGOPPresenter::ownedMemory(', 'void NativeGOPPresenter::ownedClose(',
    'IOReturn NativeGOPPresenter::completeSwap(', 'void NativeGOPPresenter::dropKept(',
    'void NativeGOPPresenter::abandonSwap('])
wire=(fb/'NativeGOPWire.h').read_text();client=a.userspace_wire.read_text()
prefix=r'''
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <mutex>
#include <condition_variable>
#include <thread>
#include <atomic>
'''
prefix+='namespace client {\n'+client.replace('#pragma once','')+'\n}\n'+wire.replace('#pragma once','')
prefix+=r'''
#define ABI(T) static_assert(sizeof(T)==sizeof(client::T) && alignof(T)==alignof(client::T),#T)
ABI(NGPMode);ABI(NGPWait);ABI(NGPSwap);ABI(NGPComplete);
static_assert(offsetof(NGPSwap,reserved)==offsetof(client::NGPSwap,reserved));
using IOReturn=int;using task_t=void *;
enum {kIOReturnNotReady=1,kIOReturnBadArgument=2,kIOReturnSuccess=0,kIOReturnUnsupported=3,kIOReturnBusy=4,kIOReturnAborted=5,THREAD_UNINT=0};
static constexpr task_t TASK_NULL=nullptr;
struct IOLock {std::mutex m;std::condition_variable cv;};
static void IOLockLock(IOLock *p){p->m.lock();}static void IOLockUnlock(IOLock *p){p->m.unlock();}
static void IOLockWakeup(IOLock *p,void *,bool){p->cv.notify_all();}
static void IOLockSleep(IOLock *p,void *,int){std::unique_lock<std::mutex> l(p->m,std::adopt_lock);p->cv.wait(l);l.release();}
struct IOMemoryDescriptor {unsigned refs=1;void retain(){++refs;}void release(){assert(refs);--refs;}};
struct IOMemoryMap {IOMemoryDescriptor *d;unsigned refs=1;IOMemoryDescriptor *getMemoryDescriptor(){return d;}void release(){assert(refs);--refs;}};
#define OSSafeReleaseNULL(p) do{if(p){(p)->release();p=nullptr;}}while(0)
static void task_deallocate(task_t){}
static unsigned completed=0;static int completionStatus=0;
static void native_legacy_gpu_complete(void *,int s){++completed;completionStatus=s;}
static uint64_t native_gop_gpu_failures=0,native_gop_gpu_completions=0,native_gop_gpu_abandoned=0;
class NativeGOPPresenter {
public:
 bool gpuMode=true,online=true,gpuClient=true;IOLock lock,*gate=&lock;IOMemoryMap *mapping=nullptr;
 NGPMode mode={NGP_VERSION,NGP_BGRX8888,1600,2560,6400,16384000};
 struct Swap {bool offered=false,delivered=false;uint64_t sequence=0;void *display=nullptr;IOMemoryDescriptor *memory=nullptr;IOMemoryMap *map=nullptr;}swap;
 struct Kept {IOMemoryDescriptor *memory=nullptr;IOMemoryMap *map=nullptr;task_t task=nullptr;unsigned id=0,used=0;};
 static constexpr unsigned kKept=6;Kept kept[kKept]{};
 const void *handoffOwner=nullptr;task_t handoffTask=TASK_NULL;uint32_t handoffCalls=0;bool handoffClosing=false;
 static std::atomic<bool> waiting;
 static IOReturn handoffCall(uint32_t,task_t,const void *,size_t,void *,size_t);
 static IOMemoryDescriptor *handoffMemory();
 static IOReturn ownedCall(uint32_t,task_t,const void *,const void *,size_t,void *,size_t);
 static IOMemoryDescriptor *ownedMemory(task_t,const void *);
 static void ownedClose(const void *);
 IOReturn completeSwap(uint64_t,int);void dropKept();void abandonSwap();
 IOReturn waitSwap(task_t,uint64_t,NGPSwap *) {
  IOLockLock(gate);waiting=true;
  while(!handoffClosing)IOLockSleep(gate,&swap,0);
  IOLockUnlock(gate);return kIOReturnNotReady;
 }
};
std::atomic<bool> NativeGOPPresenter::waiting{false};
static NativeGOPPresenter *gHandoff;
'''
test=r'''
int main(){
 NativeGOPPresenter p;gHandoff=&p;int ownerA,ownerB,task;NGPMode mode{};
 assert(!p.ownedCall(0,&task,&ownerA,nullptr,0,&mode,sizeof mode));assert(mode.width==1600 && mode.bytes==16384000);
 assert(p.ownedCall(0,&task,&ownerB,nullptr,0,&mode,sizeof mode)==kIOReturnBusy);
 assert(p.ownedCall(0,&task,&ownerA,nullptr,1,&mode,sizeof mode)==kIOReturnBadArgument);
 IOMemoryDescriptor scanout;IOMemoryMap scanoutMap{&scanout};p.mapping=&scanoutMap;
 auto *m=p.ownedMemory(&task,&ownerA);assert(m==&scanout && m->refs==2);m->release();
 assert(!p.ownedMemory(&task,&ownerB));
 IOMemoryDescriptor source;IOMemoryMap sourceMap{&source};
 p.swap={true,true,12,&p,&source,&sourceMap};NGPComplete done{11,0,0};
 assert(p.ownedCall(3,&task,&ownerA,&done,sizeof done,nullptr,0)==kIOReturnBadArgument && source.refs==1);
 done.sequence=12;assert(!p.ownedCall(3,&task,&ownerA,&done,sizeof done,nullptr,0));
 assert(!source.refs && !sourceMap.refs && completed==1 && native_gop_gpu_completions==1);
 source.refs=sourceMap.refs=1;p.swap={true,true,13,&p,&source,&sourceMap};
 std::thread waiter([&]{NGPWait w{1000};NGPSwap s{};assert(p.ownedCall(2,&task,&ownerA,&w,sizeof w,&s,sizeof s)==kIOReturnNotReady);});
 while(!p.waiting.load())std::this_thread::yield();
 p.ownedClose(&ownerB);assert(p.handoffOwner==&ownerA);
 p.ownedClose(&ownerA);waiter.join();
 assert(!p.handoffOwner && !p.handoffCalls && !p.swap.offered && !source.refs && !sourceMap.refs);
 assert(completed==2 && completionStatus==kIOReturnAborted && native_gop_gpu_abandoned==1);
 assert(!p.ownedCall(0,&task,&ownerB,nullptr,0,&mode,sizeof mode));p.ownedClose(&ownerB);
 puts("PASS exact signed-client/GOP wire; actual owner checks, stale completion, close-during-wait and resource cleanup");
}
'''
publication=(d/'package/gpu/a640/NativeAdrenoPublish.cpp').read_text()
assert 'NativeAdrenoHandoffBase=0x7000,NativeAdrenoHandoffLast=0x7003,NativeAdrenoScanoutType=0x5343' in publication
assert 'integration.displayCall(method-NativeAdrenoHandoffBase,current_task(),this,' in publication
assert 'integration.displayMemory(current_task(),this)' in publication
a.output.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='nabu-handoff-host-') as temp:
    root=Path(temp);cpp=root/'test.cpp';cpp.write_text(prefix+methods+test)
    subprocess.run(['c++','-std=c++17','-O1','-g','-pthread','-Wall','-Wextra','-Werror','-Wno-misleading-indentation',
        '-fsanitize=address,undefined',str(cpp),'-o',str(root/'test')],check=True)
    q=subprocess.run([str(root/'test')],capture_output=True,text=True,check=True,timeout=15)
    (a.output/'run.log').write_text(q.stdout);print(q.stdout,end='')
(a.output/'verification.json').write_text(json.dumps(dict(passed=True,production_handoff_bodies=True,
    signed_client_wire=str(a.userspace_wire),tunnel=['0x7000','0x7003'],memory_type='0x5343',
    close_during_wait=True,sanitizers=['ASan','UBSan'],hardware_tested=False),indent=2)+'\n')
