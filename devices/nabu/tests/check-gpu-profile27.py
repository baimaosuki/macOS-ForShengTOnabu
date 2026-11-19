#!/usr/bin/env python3
"""Exercise lazy GPU recorder storage, failure cleanup and concurrent starts."""
import argparse,json,shutil,subprocess,tempfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
a.output.mkdir(parents=True,exist_ok=True);d=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='nabu-profile-host-') as temp:
    inc=Path(temp);kit=inc/'IOKit';kit.mkdir()
    (kit/'IOTypes.h').write_text('''#pragma once
#include <stdint.h>
using IOReturn=int;
constexpr int kIOReturnSuccess=0,kIOReturnBadArgument=1,kIOReturnNoMemory=2,
 kIOReturnNotReady=3,kIOReturnIOError=4,kIOReturnBusy=5;
''')
    (kit/'IOLocks.h').write_text('''#pragma once
#include <mutex>
using IOLock=std::mutex;
inline IOLock *IOLockAlloc(){return new IOLock;}
inline void IOLockLock(IOLock *p){p->lock();}
inline void IOLockUnlock(IOLock *p){p->unlock();}
''')
    (kit/'IOLib.h').write_text('''#pragma once
#include <IOKit/IOTypes.h>
#include <atomic>
#include <cstdlib>
inline std::atomic<int> testAllocCalls{0},testFailAt{0};
inline std::atomic<size_t> testLiveBytes{0};
inline void *testAlloc(size_t n){int call=++testAllocCalls;
 if(call==testFailAt)return nullptr;
 void *p=malloc(n);if(p)testLiveBytes+=n;return p;}
inline void testFree(void *p,size_t n){if(p){testLiveBytes-=n;free(p);}}
#define IONewData(T,n) static_cast<T *>(testAlloc(sizeof(T)*(n)))
#define IODeleteData(p,T,n) testFree(p,sizeof(T)*(n))
inline void IOLog(const char *,...){}
''')
    cmd=[shutil.which('clang++') or 'c++','-std=c++17','-O1','-g','-pthread',
         '-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-sanitize-recover=all',
         '-I',str(inc),'-I',str(d/'package/gpu/upstream/Adreno6xx'),
         '-I',str(d/'package/gpu/a640'),str(d/'tests/gpu_profile27.cpp'),'-o',str(a.output/'test')]
    subprocess.run(cmd,check=True);subprocess.run([str((a.output/'test').resolve())],check=True)
(a.output/'verification.json').write_text(json.dumps(dict(passed=True,production_recorder=True,
    allocation_failures=[1,2],concurrent_starts=12,capacity_preserved=4096,
    sanitizers=['ASan','UBSan'],hardware_tested=False),indent=2)+'\n')
print('GPU recorder: lazy allocation, failure cleanup, concurrent starts and sample/job lifecycle passed')
