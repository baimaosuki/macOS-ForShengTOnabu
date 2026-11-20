#!/usr/bin/env python3
"""Execute bounded A640 first-fault ownership and IRQ acknowledgement failure."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

DEVICE = Path(__file__).resolve().parents[1]
GPU = DEVICE / 'package/gpu/a640'


def function(text, signature):
    start = text.index(signature)
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


TEST = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <vector>
#include <thread>
#include <atomic>
#include "NabuGpuFault.h"
struct Peer {
 unsigned reads=0,writes=0,barriers=0,fail=0;uint32_t selected=0;
 static int read(void *cookie,uint32_t reg,uint32_t *out){auto &p=*static_cast<Peer *>(cookie);++p.reads;
  if(p.fail==reg)return 1;
  if(reg==0x909){assert(p.selected==1 && p.barriers==1);*out=0x70170001;}
  else if(reg==0x806)*out=31;else if(reg==0x807)*out=57;else if(reg==0x821)*out=0x1234;else assert(false);
  return 0;
 }
 static int write(void *cookie,uint32_t reg,uint32_t value){auto &p=*static_cast<Peer *>(cookie);++p.writes;
  assert(reg==0x908 && value==1);p.selected=value;return p.fail==reg?1:0;
 }
 static int barrier(void *cookie){++static_cast<Peer *>(cookie)->barriers;return 0;}
};
struct Io {void *cookie;int(*read32)(void*,uint32_t,uint32_t*);int(*write32)(void*,uint32_t,uint32_t);int(*barrier)(void*);};
static void captureTests(){
 Peer peer;Io io{&peer,Peer::read,Peer::write,Peer::barrier};NabuGpuFaultCapture fault;NabuGpuCpFault record;
 assert(!fault.snapshot(&record));assert(fault.capture(io,0x200,5,0));assert(fault.snapshot(&record));
 assert(record.irq==0x200 && record.cp==5 && record.opcode==0x70170001 && record.rptr==31 && record.wptr==57 && record.hw_fault==0x1234 && record.valid==15);
 const unsigned reads=peer.reads,writes=peer.writes;assert(!fault.capture(io,0,0,0));
 assert(peer.reads==reads && peer.writes==writes);assert(fault.snapshot(&record) && record.cp==5);
 for(unsigned failed:{0x908u,0x909u,0x806u,0x807u,0x821u}){
  Peer broken;broken.fail=failed;Io other{&broken,Peer::read,Peer::write,Peer::barrier};NabuGpuFaultCapture partial;
  assert(partial.capture(other,0x200,5,0));assert(partial.snapshot(&record));
  const unsigned bit=(failed==0x908 || failed==0x909)?1:failed==0x806?2:failed==0x807?4:8;
  assert(!(record.valid&bit) && record.irq==0x200 && record.cp==5);
  assert(broken.reads<=4 && broken.writes<=1); // failures never retry or poll
 }
 Peer shared;Io same{&shared,Peer::read,Peer::write,Peer::barrier};NabuGpuFaultCapture one;
 std::atomic<unsigned> winners{0};std::vector<std::thread> callers;
 for(unsigned i=0;i<8;++i)callers.emplace_back([&]{if(one.capture(same,0x200,1,0))++winners;});
 for(auto &caller:callers)caller.join();assert(winners==1 && shared.writes==1);
 assert(one.snapshot(&record) && record.valid==7 && record.cp==1);
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    irq = (GPU / 'NativeAdrenoIrq.cpp').read_text()
    begin = irq.index('enum : uint32_t')
    end = irq.index('IOReturn NativeAdrenoIrq::poison()')
    constants = irq[begin:end]
    # The actual A640 decoder is executed against a failed clear operation.
    peer = r'''
using IOReturn=int;enum{kIOReturnIOError=1,kIOReturnBadArgument=2,kIOReturnNotReady=3,kIOReturnSuccess=0};
enum NativeAdrenoGeneration{NativeAdrenoA740,NativeAdrenoA642L,NativeAdrenoA640};
static bool nativeAdrenoA6xx(NativeAdrenoGeneration g){return g!=NativeAdrenoA740;}
enum{NativeAdrenoIrqFence=1,NativeAdrenoIrqHang=2,NativeAdrenoIrqCpError=4,NativeAdrenoIrqUche=8,
 NativeAdrenoIrqBusError=16,NativeAdrenoIrqPreempt=32,NativeAdrenoIrqIdle=64,NativeAdrenoIrqSecurity=128,
 NativeAdrenoIrqUnknown=256,NativeAdrenoIrqFatal=512};
struct NativeAdrenoIrqResult{uint32_t flags,status,unknown,cp_status,protect_status;};
static bool failClear=false;
static int irqRead(void*,uint32_t reg,uint32_t *value){*value=reg==0x201?0x200:reg==0x823?1:0;return 0;}
static int irqWrite(void*,uint32_t reg,uint32_t){assert(reg==0x37);return failClear?1:0;}
static int irqBarrier(void*){return 0;}
class NativeAdrenoIrq {
public:
 Io io_{nullptr,irqRead,irqWrite,irqBarrier};bool attached_=true,poisoned_=false;NativeAdrenoGeneration generation_=NativeAdrenoA640;
 IOReturn poison();IOReturn handle(NativeAdrenoIrqResult*);
 static uint32_t mask(NativeAdrenoGeneration);static uint32_t cpErrorMask(NativeAdrenoGeneration);
};
'''
    methods = '\n'.join(function(irq, signature) for signature in
                       ('IOReturn NativeAdrenoIrq::poison(', 'uint32_t NativeAdrenoIrq::mask(',
                        'uint32_t NativeAdrenoIrq::cpErrorMask(', 'IOReturn NativeAdrenoIrq::handle('))
    tail = r'''
int main(){captureTests();NativeAdrenoIrq irq;NativeAdrenoIrqResult result;
 assert(!irq.handle(&result) && result.status==0x200 && result.cp_status==1 && (result.flags&NativeAdrenoIrqFatal));
 failClear=true;assert(irq.handle(&result)==kIOReturnIOError && result.status==0x200 && irq.poisoned_);
 puts("PASS bounded first-error CP opcode capture, concurrent owner, partial read validity and IRQ clear failure preservation");}
'''
    with tempfile.TemporaryDirectory(prefix='nabu-gpu-fault-') as temporary:
        root = Path(temporary)
        cpp = root / 'test.cpp'
        cpp.write_text(TEST + constants + peer + methods + tail)
        executable = root / 'test'
        subprocess.run([shutil.which('clang++') or 'c++', '-std=c++17', '-O1', '-g', '-pthread',
                        '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-misleading-indentation',
                        '-fsanitize=address,undefined', '-I', str(GPU), str(cpp), '-o', str(executable)], check=True)
        result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10, check=True)
        (args.output / 'run.log').write_text(result.stdout)
        print(result.stdout, end='')
    report = dict(passed=True, production_fault_capture=True, production_irq_decode=True,
                  first_fault_preserved=True, bounded_mmio=True, failed_reads_never_report_valid=True,
                  concurrent_capture_single_owner=True, irq_clear_failure_preserves_source=True,
                  hardware_tested=False, sanitizers=['ASan', 'UBSan'])
    (args.output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
