#!/usr/bin/env python3
"""Run production BO allocation/release bodies over explicit descriptor peers."""
import argparse,json,subprocess,tempfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
d=Path(__file__).resolve().parents[1];g=d/'package/gpu/a640';source=(g/'NativeAdrenoMemory.cpp').read_text()
def body(signature):
    start=source.index(signature);brace=source.index('{',start);depth=1;end=brace+1
    while depth:
        depth+=(source[end]=='{')-(source[end]=='}');end+=1
    return source[start:end]
methods='\n'.join(body(x) for x in ['IOReturn destroyOwnedStorage(',
    'IOReturn NativeAdrenoMemory::ensureOwnedPageCache(', 'IOReturn NativeAdrenoMemory::allocate(size_t', 'IOReturn NativeAdrenoMemory::release(',
    'IOReturn NativeAdrenoMemory::holdForDevice(', 'IOReturn NativeAdrenoMemory::retireAfterQuiescence('])
methods=methods.replace('__asm__ volatile("dsb sy":::"memory");','hostBarrier();')
peer=r'''
#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <cstdio>
using IOReturn=int;using IOOptionBits=unsigned;using IOByteCount=size_t;using addr64_t=uint64_t;
enum {kIOReturnSuccess=0,kIOReturnBadArgument=1,kIOReturnBusy=2,kIOReturnNoResources=3,
 kIOReturnNoMemory=4,kIOReturnUnsupported=5,kIOReturnNotReady=6,kIOReturnIOError=7};
enum {kIODirectionInOut=3,kIOMemoryMapperNone=8,kIOMapWriteCombineCache=16,kIOMapCopybackCache=32,
 kIOMemoryPhysicallyContiguous=64,kIOMemoryPageable=128,kIOMapAnywhere=256};
static void *kernel_task=(void *)1;
static unsigned descriptors=0,mappings=0,prepares=0,completes=0;
static class IOMemoryMap *lastMapping=nullptr;
static unsigned policies=0,restores=0;static bool cacheOwned=false,failPolicy=false,failRestore=false;
static bool failAllocate=false,failPrepare=false,failMap=false,failComplete=false,highPhysical=false;
class IOMemoryMap {
public:
 void *p;explicit IOMemoryMap(void *v):p(v){++mappings;lastMapping=this;}
 uint64_t getVirtualAddress(){return (uintptr_t)p;}
 void release(){--mappings;delete this;}
};
class IOMemoryDescriptor {
public:
 virtual ~IOMemoryDescriptor()=default;
 virtual IOReturn complete(){assert(!cacheOwned);++completes;return failComplete?kIOReturnIOError:0;}
 virtual void release(){delete this;}
};
class IOBufferMemoryDescriptor:public IOMemoryDescriptor {
public:
 void *p;size_t bytes;bool noTask;
 IOBufferMemoryDescriptor(size_t n,bool nt):bytes(n),noTask(nt){assert(!posix_memalign(&p,4096,n));memset(p,0xa5,n);++descriptors;}
 static IOBufferMemoryDescriptor *inTaskWithOptions(void *task,unsigned flags,size_t n,size_t align){
  assert(!task && align==4096 && (flags&kIOMemoryPageable) && (flags&kIOMapWriteCombineCache));
  return failAllocate?nullptr:new IOBufferMemoryDescriptor(n,true);
 }
 static IOBufferMemoryDescriptor *inTaskWithPhysicalMask(void *task,unsigned flags,size_t n,uint64_t){
  assert(task==kernel_task && !(flags&kIOMemoryPageable));return failAllocate?nullptr:new IOBufferMemoryDescriptor(n,false);
 }
 IOReturn prepare(){++prepares;return failPrepare?kIOReturnIOError:0;}
 void *getBytesNoCopy(){return noTask?nullptr:p;}
 IOMemoryMap *createMappingInTask(void *task,uint64_t,unsigned flags){
  assert(task==kernel_task && (flags&kIOMapWriteCombineCache));return failMap?nullptr:new IOMemoryMap(p);
 }
 size_t getLength(){return bytes;}
 uint64_t getPhysicalSegment(size_t off,size_t *n,unsigned){*n=4096;return highPhysical?UINT64_C(1)<<48:0x30000000+off*2;}
 void release() override{--descriptors;free(p);delete this;}
};
#define IONewData(T,n) new T[n]
#define IODeleteData(p,T,n) delete[] p
#define OSSafeReleaseNULL(p) do{if(p){(p)->release();p=nullptr;}}while(0)
#include "NativeAdrenoMemory.h"
enum {MemoryComplete,MemoryDescriptorRelease,MemoryAllocate,MemoryPrepare,MemoryZero,MemorySpans};
struct MemoryStageTime {template<class... T>MemoryStageTime(T...) {}};
struct MemorySlowTime {template<class... T>MemorySlowTime(T...) {}};
static uint32_t native_adreno_trace_stage=0,native_adreno_backing_cache_stats[12]{},native_adreno_pool_stats[8]{};
static uint64_t native_adreno_memory_stage_stats[6][4]{};
static unsigned backing_cache_count=0;
static NativeAdrenoMemoryDiagnostics memory_diagnostics;
static bool outputWidth(unsigned n){return n==32 || n==40;}
IOReturn NativeAdrenoQueryCpu4K(NativeAdrenoCpuState *p){*p={};return 0;}
static void noteBackingRequest(size_t,bool){}
static bool backingPressure(){return false;}
static bool takeBacking(size_t,unsigned,bool,bool,NativeAdrenoBackingStorage &){return false;}
static bool cacheBacking(NativeAdrenoBackingStorage &){return false;}
static void noteBackingReject(unsigned){}
enum {BackingRejectQuarantined,BackingRejectBusy,BackingRejectPressure,BackingRejectImported,BackingRejectPooled};
struct {IOBufferMemoryDescriptor *descriptor=nullptr;size_t bytes=0;uint64_t physical=0;char *virt=nullptr;} pool;
static bool poolTake(size_t,size_t *){return false;}
static void poolGive(size_t,size_t){}
static void hostBarrier(){}
IOReturn NativeAdrenoSetOwnedPageCache(const NativeAdrenoPhysicalSpan *s,size_t n,bool wc,uint32_t *changed){
 assert(s && n);*changed=0;
 if(wc){++policies;cacheOwned=true;if(failPolicy)return kIOReturnIOError;}
 else{++restores;if(failRestore)return kIOReturnIOError;cacheOwned=false;}
 *changed=unsigned(n);return 0;
}
static void hostZero(void *p,size_t n){assert(cacheOwned);memset(p,0,n);}
#define bzero hostZero
static void PE_parse_boot_argn(const char *,int *,unsigned){}
static void IOLog(const char *,...){}
IOReturn NativeAdrenoMemory::trimBackingCache(NativeAdrenoBackingTrimReason){return 0;}
'''
test=r'''
int main(){
 {
  NativeAdrenoMemory b;assert(!b.allocate(8192,40,false,8192,true,false));
  assert(policies==1 && cacheOwned);
  assert(descriptors==1 && mappings==1 && prepares==1 && b.writeCombined() && b.spanCount()==2);
  for(size_t i=0;i<b.bytes();++i)assert(!((uint8_t *)b.mapping())[i]);
  assert(!b.holdForDevice() && b.release()==kIOReturnBusy && descriptors==1 && mappings==1);
  assert(!b.retireAfterQuiescence() && !b.release() && !descriptors && !mappings && restores==1 && !cacheOwned);
 }
 for(unsigned fault=0;fault<4;++fault){
  failAllocate=fault==0;failPrepare=fault==1;failMap=fault==2;highPhysical=fault==3;
  NativeAdrenoMemory b;assert(b.allocate(8192,40,false,8192,true,false)!=0);
  assert(!descriptors && !mappings);assert(!b.release());
 }
 failAllocate=failPrepare=failMap=highPhysical=false;
 {failPolicy=true;NativeAdrenoMemory f;
  assert(f.allocate(4096,40,false,4096,true,false)==kIOReturnIOError);
  assert(!descriptors && !mappings && !cacheOwned);failPolicy=false;}
 {NativeAdrenoMemory f;assert(!f.allocate(4096,40,false,4096,true,false));
  unsigned before=completes;failRestore=true;assert(f.release()==kIOReturnIOError && f.quarantined());
  assert(cacheOwned && completes==before && descriptors==1 && mappings==1);
  failRestore=false;cacheOwned=false;f.descriptor()->release();delete[] f.spans();
  // fixture terminal cleanup of retained mapping after quarantine check
  lastMapping->release();
 }
 NativeAdrenoMemory b;assert(!b.allocate(4096,40,false,4096,true,false));
 failComplete=true;assert(b.release()==kIOReturnIOError && b.quarantined() && descriptors==1);
 assert(b.release()==kIOReturnBusy && descriptors==1);
 // Quarantined memory is intentionally retained by production. The fixture
 // owns its terminal cleanup after checking that refusal; no production reset.
 failComplete=false;b.descriptor()->release();delete[] b.spans();
 puts("PASS production no-task pageable/WC allocation, zeroing, mapping/prepare failures, references and failed-complete quarantine");
}
'''
a.output.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='nabu-bo-host-') as temp:
    root=Path(temp);(root/'IOKit').mkdir();(root/'IOKit/IOTypes.h').write_text('#pragma once\n')
    cpp=root/'test.cpp';cpp.write_text(peer+methods+test)
    cmd=['c++','-std=c++17','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-Wno-misleading-indentation','-Wno-extra',
         '-DNABU_GPU_METAL=1','-fsanitize=address,undefined','-I',str(root),'-I',str(g),str(cpp),'-o',str(root/'test')]
    subprocess.run(cmd,check=True);q=subprocess.run([str(root/'test')],capture_output=True,text=True,check=True)
    (a.output/'run.log').write_text(q.stdout);print(q.stdout,end='')
(a.output/'verification.json').write_text(json.dumps(dict(passed=True,production_allocation_release_bodies=True,
    no_task_pageable_wc=True,failure_controls=['allocation','prepare','CPU map','physical width','cache apply','cache restore','complete'],
    sanitizers=['ASan','UBSan'],hardware_tested=False),indent=2)+'\n')
