#!/usr/bin/env python3
"""Execute client close ownership and the exact firmware scanout import gate."""
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


CLIENT_PEER = r'''
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdio>
using IOReturn=int;
enum {kIOReturnSuccess=0,kIOReturnNotReady=1,kIOReturnBusy=2,kIOReturnBadArgument=3};
enum {NativeAdrenoAbiMaxQueues=8,NativeAdrenoClientMaxObjects=4096,NativeAdrenoSubmitMaxResources=4096,NativeAdrenoSubmitMaxIbs=512};
struct NativeAdrenoSubmitResource{};struct NativeAdrenoSubmitBuffer{};
struct NativeAdrenoMemory {static int trimBackingCache(){return 0;}};
struct Scheduler {
 unsigned closed=0,last=999;bool windowServerPending=true;
 void closeOwner(unsigned owner){++closed;last=owner;if(!owner)windowServerPending=false;}
};
struct Submit {
 Scheduler actual;
 Scheduler *scheduler(){return &actual;}
 bool rangeOutstanding(unsigned,unsigned){return false;}
};
struct Vm {unsigned removed=0;void removeClient(){++removed;}};
#define IODeleteData(p,T,n) delete[] p
class NativeAdrenoUserClient {
public:
 struct Entry{};struct Queue{unsigned id=0;bool active=false;};
 bool open_=true,scoped_=false,used_=false;
 unsigned queue_base_=0,input_bits_=32;
 uint64_t window_start_=0,window_bytes_=0,window_peak_=0;
 unsigned live_count_=0,free_head_=0;size_t entry_count_=1;
 Entry *entries_=new Entry[1];
 NativeAdrenoSubmitResource *submit_resources_=new NativeAdrenoSubmitResource[1];
 NativeAdrenoSubmitBuffer *submit_buffers_=new NativeAdrenoSubmitBuffer[1];
 Queue queues_[8]{};
 Submit submit;Submit *submit_=&submit;Vm vm;Vm *vm_=&vm;
 void *ring_=nullptr,*timer_=nullptr,*status_=nullptr,*info_=nullptr;
 size_t liveObjects()const{return live_count_;}
 bool ownRangeOutstanding()const;
 IOReturn setScope(uint32_t,uint64_t,uint64_t);
 void beginClose();IOReturn close();
};
'''
CLIENT_TEST = r'''
int main(){
 {NativeAdrenoUserClient failed;
  // setScope rejected a bad window before this client owned any range.
  assert(failed.setScope(8,1,4096)==kIOReturnBadArgument);
  failed.beginClose();assert(failed.submit.actual.windowServerPending && !failed.submit.actual.closed);
  assert(!failed.close());assert(failed.submit.actual.windowServerPending && !failed.submit.actual.closed);
  assert(failed.vm.removed==1);
 }
 {NativeAdrenoUserClient scoped;assert(!scoped.setScope(8,0x20000000,0x100000));
  scoped.beginClose();assert(scoped.submit.actual.closed==1 && scoped.submit.actual.last==8);
  assert(!scoped.close() && scoped.submit.actual.windowServerPending);
 }
 {NativeAdrenoUserClient legacy;legacy.used_=true;
  legacy.beginClose();assert(legacy.submit.actual.closed==1 && !legacy.submit.actual.windowServerPending);
  assert(!legacy.close());
 }
 {NativeAdrenoUserClient server;assert(!server.setScope(0,0x20000000,0x100000));
  assert(!server.close());assert(!server.submit.actual.windowServerPending);
 }
 puts("PASS failed unscoped teardown preserves WindowServer; scoped and legacy teardown cancel only owned queues");
}
'''

SCANOUT_PEER = r'''
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include "NabuGpuCopyPlan.h"
using IOByteCount=size_t;using addr64_t=uint64_t;
enum {kIOMemoryMapperNone=0};
static uint64_t gPhysBase=0xc0000000,gPhysSize=0x100000000;
struct PE_Video {uint64_t v_baseAddr=0x9c000000,v_width=1600,v_height=2560,v_rowBytes=6400;unsigned v_depth=32,v_rotate=0;};
static PE_Video video;
static int enabled=0,console_error=0;
static void PE_parse_boot_argn(const char *key,int *value,size_t){assert(!strcmp(key,"nabu-gpu-scanout"));*value=enabled;}
static int PE_current_console(PE_Video *out){*out=video;return console_error;}
struct IOMemoryDescriptor {
 uint64_t physical=0x9c000000;size_t bytes=16384000;bool scatter=false,short_page=false;
 size_t getLength(){return bytes;}
 uint64_t getPhysicalSegment(size_t offset,IOByteCount *length,unsigned){
  *length=short_page?2048:4096;return physical+offset+(scatter && offset>=4096?4096:0);
 }
};
'''
SCANOUT_TEST = r'''
int main(){IOMemoryDescriptor memory;
 assert(!firmwareImportPhysical(&memory,memory.bytes,40,false));
 enabled=1;assert(firmwareImportPhysical(&memory,memory.bytes,40,false)==0x9c000000);
 assert(!firmwareImportPhysical(&memory,memory.bytes-4096,40,false));
 assert(!firmwareImportPhysical(&memory,memory.bytes,40,true));
 memory.scatter=true;assert(!firmwareImportPhysical(&memory,memory.bytes,40,false));memory.scatter=false;
 memory.short_page=true;assert(!firmwareImportPhysical(&memory,memory.bytes,40,false));memory.short_page=false;
 memory.physical+=4096;assert(!firmwareImportPhysical(&memory,memory.bytes,40,false));memory.physical-=4096;
 video.v_baseAddr=memory.physical=gPhysBase;assert(!firmwareImportPhysical(&memory,memory.bytes,40,false));
 video.v_baseAddr=memory.physical=0x9c000000;video.v_rotate=1;
 assert(!firmwareImportPhysical(&memory,memory.bytes,40,false));video.v_rotate=0;
 console_error=1;assert(!firmwareImportPhysical(&memory,memory.bytes,40,false));
 puts("PASS real scanout import gate: disabled refuses, exact GOP allocation accepts; partial/readonly/scattered/managed RAM refuse");
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline-ref', help='Also reproduce the prior ownership failure before checking current code')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    compiler = shutil.which('clang++') or 'c++'
    client_path = GPU / 'NativeAdrenoUserClient.cpp'
    def client_code(text):
        return CLIENT_PEER + '\n'.join(function(text, signature) for signature in
            ('IOReturn NativeAdrenoUserClient::setScope(', 'bool NativeAdrenoUserClient::ownRangeOutstanding(',
             'void NativeAdrenoUserClient::beginClose(', 'IOReturn NativeAdrenoUserClient::close(')) + CLIENT_TEST
    codes = [('ownership', client_code(client_path.read_text()), True)]
    if args.baseline_ref:
        relative = client_path.relative_to(DEVICE.parents[1])
        old = subprocess.check_output(['git', 'show', args.baseline_ref + ':' + str(relative)],
                                      cwd=DEVICE.parents[1], text=True)
        codes.insert(0, ('ownership-before', client_code(old), False))
    codes.append(('scanout', SCANOUT_PEER + function((GPU / 'NativeAdrenoMemory.cpp').read_text(),
                                                  'static uint64_t firmwareImportPhysical(') + SCANOUT_TEST, True))
    results = []
    with tempfile.TemporaryDirectory(prefix='nabu-gpu-ownership-') as temporary:
        root = Path(temporary)
        for name, code, should_pass in codes:
            cpp = root / (name + '.cpp')
            executable = root / name
            cpp.write_text(code)
            subprocess.run([compiler, '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                            '-Wno-unused-parameter', '-Wno-misleading-indentation',
                            '-fsanitize=address,undefined', '-I', str(GPU), '-I', str(GPU.parent/'upstream/AdrenoPM4'),
                            str(cpp), '-o', str(executable)], check=True)
            completed = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            (args.output / (name + '.log')).write_text(completed.stdout + completed.stderr)
            assert (completed.returncode == 0) == should_pass, (name, completed.returncode, completed.stdout, completed.stderr)
            results.append(dict(name=name, returncode=completed.returncode, expected_pass=should_pass))
    report = dict(passed=True, production_function_bodies=True, results=results,
                  unscoped_close_isolated=True, exact_firmware_import_gate=True,
                  sanitizers=['ASan', 'UBSan'], hardware_tested=False)
    (args.output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
