#!/usr/bin/env python3
"""Execute production v2 size/priority/query/submit bodies with bounded peers."""
import argparse,json,subprocess,tempfile,shutil
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
d=Path(__file__).resolve().parents[1];g=d/'package/gpu/a640'
source=(g/'NativeAdrenoUserClient.cpp').read_text()
def method(signature):
    start=source.index(signature);brace=source.index('{',start);depth=1;end=brace+1
    while depth:
        depth+=(source[end]=='{')-(source[end]=='}');end+=1
    return source[start:end]
names=['size_t NativeAdrenoUserClient::inputSize','size_t NativeAdrenoUserClient::outputSize',
       'IOReturn NativeAdrenoUserClient::query','IOReturn NativeAdrenoUserClient::setPriority',
       'IOReturn NativeAdrenoUserClient::submit(']
methods='\n'.join(method(n) for n in names)
reference=d/'compat/gpu/NativeAdrenoABI-v2.h'
reference_text=reference.read_text().replace('#ifndef NATIVE_ADRENO_ABI_H','').replace('#define NATIVE_ADRENO_ABI_H','')
# Drop only the final include-guard endif, retaining the C++ assertions.
reference_text=reference_text.rsplit('#endif',1)[0]
harness=r'''
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <cstdio>
#include "NativeAdrenoABI.h"
using IOReturn=int;
enum {kIOReturnSuccess=0,kIOReturnBadArgument=1,kIOReturnNotReady=2,kIOReturnUnsupported=3,kIOReturnTimeout=4};
enum {PRIO_DARWIN_GPU_DENY=0,PRIO_DARWIN_GPU_BACKGROUND=1,PRIO_DARWIN_GPU_UTILITY=2};
static int role=5;static int task_get_gpu_role(void *){return role;}static void *current_task(){return nullptr;}
struct NativeAdrenoRingClock {};
struct NativeAdrenoSubmitBuffer {uint64_t handle,iova;size_t bytes;};
struct NativeAdrenoSubmitResource {uint64_t handle;size_t offset,bytes;unsigned access;bool imported;};
struct Peer {
    size_t seen=0;unsigned priority=99;uint64_t last=0;
    Peer *scheduler(){return this;}
    void setOwnerPriority(uint32_t,unsigned p){priority=p;}
    IOReturn observeFence(uint32_t,const void *,unsigned){return 0;}
    IOReturn submitLegacy(uint32_t,const NativeAdrenoSubmitBuffer *b,size_t n,const NativeAdrenoSubmitResource *,size_t,unsigned,uint32_t *f){
        seen=n;last=b[n-1].iova;*f=17;return 0;
    }
    IOReturn describe(uint64_t,uint64_t *va,size_t *bytes,unsigned *perms){*va=0x1000;*bytes=4096;*perms=3;return 0;}
};
class NativeAdrenoUserClient {
public:
    struct Entry {bool used=true,mapped_state=true;uint64_t mapped=1;};
    struct Queue {bool active=true;};
    bool open_=true,used_=false;uint32_t input_bits_=32,output_bits_=40,queue_base_=0;
    int32_t foregroundPriority_=1,backgroundPriority_=3;
    Queue queues_[8]{};Entry entry, *entries_=&entry;size_t entry_count_=1;
    Peer peer;Peer *submit_=&peer,*vm_=&peer;
    NativeAdrenoSubmitBuffer submit_buffers_[512]{};
    NativeAdrenoSubmitResource resources[4096]{},*submit_resources_=resources;
    Entry *find(uint64_t h){return h==1?&entry:nullptr;}
    static size_t inputSize(uint32_t);static size_t outputSize(uint32_t);
    IOReturn query(NativeAdrenoAbiParams *);
    IOReturn setPriority(const NativeAdrenoAbiPriority *);
    IOReturn submit(const NativeAdrenoAbiSubmit *,NativeAdrenoAbiSubmitReply *,const NativeAdrenoRingClock *);
};
'''
test=r'''
int main(){
 static_assert(NativeAdrenoAbiVersion==2 && sizeof(NativeAdrenoAbiSubmit)==3104);
 static_assert(NativeAdrenoMethodSetPriority==17 && NativeAdrenoMethodNote==18);
 static_assert(sizeof(NativeAdrenoAbiNote)==128);
 assert(NativeAdrenoUserClient::inputSize(5)==3104);
 assert(NativeAdrenoUserClient::inputSize(17)==8 && NativeAdrenoUserClient::outputSize(17)==0);
 NativeAdrenoUserClient c;NativeAdrenoAbiParams params{};assert(!c.query(&params));
 assert(params.abi_version==2 && !(params.flags&NativeAdrenoParamsOwnedCoherent));
 NativeAdrenoAbiPriority priority{5,2};assert(!c.setPriority(&priority));
 priority.foreground=3;assert(c.setPriority(&priority)==kIOReturnBadArgument);
 NativeAdrenoAbiSubmit q{};NativeAdrenoAbiSubmitReply r{};q.buffer_count=128;
 for(auto &b:q.buffers)b={1,0x1000,4};q.buffers[127].iova=0x4321000;
 assert(!c.submit(&q,&r,nullptr) && r.fence==17 && c.peer.seen==128 && c.peer.last==0x4321000 && c.peer.priority==5);
 role=PRIO_DARWIN_GPU_BACKGROUND;assert(!c.submit(&q,&r,nullptr) && c.peer.priority==2);
 q.buffer_count=129;assert(c.submit(&q,&r,nullptr)==kIOReturnBadArgument);
 q.buffer_count=128;q.buffers[127].handle=2;assert(c.submit(&q,&r,nullptr)==kIOReturnBadArgument);
 c.open_=false;priority={1,3};assert(c.setPriority(&priority)==kIOReturnNotReady);
 puts("PASS production v2 size/query/priority/128-IB submit bodies, NC flags and rejection controls");
}
'''
reference_code='namespace client {\n'+reference_text+'\n}\n'
for name in ('Params','BoAlloc','BoAllocReply','BoMap','BoMapReply','BoFree','BoImport','SubmitBuffer',
             'Submit','SubmitReply','WaitFence','Timestamp','Metadata','BoCpuMap','BoCpuMapReply','BoCpuUnmap',
             'Queue','QueueReply','Status','DeviceInfo','Priority','Note'):
    reference_code+=f'static_assert(sizeof(NativeAdrenoAbi{name})==sizeof(client::NativeAdrenoAbi{name}),"{name}");\n'
reference_code+='static_assert(offsetof(NativeAdrenoAbiSubmit,buffers)==offsetof(client::NativeAdrenoAbiSubmit,buffers));\n'
a.output.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='nabu-wire-v2-') as temp:
    root=Path(temp);cpp=root/'test.cpp';cpp.write_text(harness+reference_code+methods+test)
    cmd=[shutil.which('clang++') or 'c++','-std=c++17','-O1','-g','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-Wno-unused-parameter',
         '-fsanitize=address,undefined','-I',str(g),str(cpp),'-o',str(root/'test')]
    subprocess.run(cmd,check=True);run=subprocess.run([str(root/'test')],capture_output=True,text=True,check=True)
    (a.output/'run.log').write_text(run.stdout);print(run.stdout,end='')
(a.output/'verification.json').write_text(json.dumps(dict(passed=True,production_method_bodies=True,
    submit_bytes=3104,max_ibs=128,selectors_17_18=["SetPriority","Note"],owned_coherent=False,
    sanitizers=['ASan','UBSan'],hardware_tested=False),indent=2)+'\n')
