#!/usr/bin/env python3
"""Execute the production legacy BO view path, including cache reuse."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--source', type=Path, help='Optional old production source for regression reproduction')
a = p.parse_args()
device = Path(__file__).resolve().parents[1]
source = (a.source or device / 'package/gpu/a640/NativeAdrenoPublish.cpp').read_text()
start = source.index('IOReturn AGXDeviceUserClient::legacyMemoryForType(')
end = source.index('{', start) + 1
depth = 1
while depth:
    depth += (source[end] == '{') - (source[end] == '}')
    end += 1
method = source[start:end]
# Copy the real View declaration too: its stored options are part of the fix.
view_start = source.index('    struct View {')
view = source[view_start:source.index(';};', view_start) + 3]
peer = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
using UInt32=uint32_t;using IOOptionBits=unsigned;using IOReturn=int;
enum {kIOReturnNotFound=1,kIOReturnNotReady=2,kIOReturnNoMemory=3,kIOReturnNoResources=4,kIOReturnSuccess=0};
enum {kIOMapAnywhere=1,kIOMapWriteCombineCache=0x400,kIOMapCopybackCache=0x500,kIODirectionInOut=3,kIOMemoryTypePhysical64=0x1000};
struct IOAddressRange {uint64_t address,length;};
static unsigned physicalViews=0;
struct IOMemoryDescriptor {
 unsigned refs=1;
 void retain(){++refs;}void release(){assert(refs);if(!--refs)delete this;}
 static IOMemoryDescriptor *withAddressRanges(IOAddressRange *r,UInt32 n,IOOptionBits,void*){
  ++physicalViews;assert(n==1 && r[0].address==0x90000000 && r[0].length==4096);return new IOMemoryDescriptor;
 }
};
struct Span {uint64_t physical,bytes;};
struct NativeAdrenoMemory {
 bool wc=true,owned=true;Span span{0x90000000,4096};
 IOMemoryDescriptor *original=new IOMemoryDescriptor;
 ~NativeAdrenoMemory(){original->release();}
 IOMemoryDescriptor *descriptor(){return owned?original:nullptr;}
 bool writeCombined(){return wc;}size_t spanCount(){return 1;}Span *spans(){return &span;}
};
struct Core {
 NativeAdrenoMemory backing;
 NativeAdrenoMemory *memoryForToken(unsigned type){return type==7?&backing:nullptr;}
};
static unsigned native_adreno_view_diag[6]{};
static struct {void *lock=nullptr;} g;
static void IOLockLock(void*){}static void IOLockUnlock(void*){}
#define IONewZero(T,n) new T[n]()
#define IONewData(T,n) new T[n]
#define IODeleteData(p,T,n) delete[] p
class AGXDeviceUserClient {
public:
 bool live_=true,closing_=false;unsigned viewTeardownStatus_=0;
 static constexpr size_t MaxViews=2;
 VIEW_DECL
 View *views_=nullptr;Core core;Core *core_=&core;
 IOReturn legacyMemoryForType(UInt32,IOOptionBits*,IOMemoryDescriptor**);
 ~AGXDeviceUserClient(){if(views_){for(size_t i=0;i<MaxViews;++i)if(views_[i].descriptor)views_[i].descriptor->release();delete[] views_;}}
};
'''.replace('VIEW_DECL', view)
test = r'''
int main(){
 for(bool owned:{true,false})for(bool wc:{true,false}){
  AGXDeviceUserClient client;client.core.backing.wc=wc;client.core.backing.owned=owned;
  const unsigned before=physicalViews;
  IOMemoryDescriptor *first=nullptr,*again=nullptr;IOOptionBits one=0,two=0;
  assert(!client.legacyMemoryForType(7,&one,&first));
  assert(one==(kIOMapAnywhere|(wc?kIOMapWriteCombineCache:kIOMapCopybackCache)));
  assert(!client.legacyMemoryForType(7,&two,&again));
  assert(first==again && two==one); // no default-cache alias on reuse
  if(owned)assert(first==client.core.backing.original && physicalViews==before);
  else assert(first!=client.core.backing.original && physicalViews==before+1);
  first->release();again->release();
 }
 AGXDeviceUserClient unavailable;unavailable.closing_=true;
 IOMemoryDescriptor *result=nullptr;IOOptionBits options=0;
 assert(unavailable.legacyMemoryForType(7,&options,&result)==kIOReturnNotReady && !result);
 AGXDeviceUserClient full;full.views_=new AGXDeviceUserClient::View[2]();
 for(auto i:{0,1}){full.views_[i].token=8+i;full.views_[i].descriptor=new IOMemoryDescriptor;}
 assert(full.legacyMemoryForType(7,&options,&result)==kIOReturnNoResources && !result);
 assert(full.core.backing.original->refs==1); // failed publication releases its temporary retain
 puts("PASS production owned VM-object identity, borrowed physical fallback, WC/WB reuse and descriptor lifetime");
}
'''
a.output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='nabu-gpu-view-') as temp:
    root = Path(temp)
    cpp = root / 'test.cpp'
    cpp.write_text('#include <initializer_list>\n' + peer + method + test)
    subprocess.run(['c++', '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', str(cpp), '-o', str(root/'test')], check=True)
    run = subprocess.run([str(root/'test')], capture_output=True, text=True, timeout=10)
    (a.output/'run.log').write_text(run.stdout + run.stderr)
    if run.returncode:
        raise SystemExit('BO cache-mode regression failed; see run.log')
    print(run.stdout, end='')
(a.output/'verification.json').write_text(json.dumps(dict(passed=True,
    production_legacy_view_executed=True, wc_and_wb_reuse=True,
    owned_vm_object_preserved=True, borrowed_fallback=True, full_view_failure_releases_reference=True,
    sanitizers=['ASan', 'UBSan'], hardware_tested=False), indent=2)+'\n')
