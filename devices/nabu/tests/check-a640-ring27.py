#!/usr/bin/env python3
"""Run the actual arm64 A640 ring code against a software register/fence peer."""
from pathlib import Path
import argparse,json,re,struct,sys
from unicorn import Uc,UC_ARCH_ARM64,UC_MODE_ARM,UC_HOOK_CODE
from unicorn.arm64_const import *
p=argparse.ArgumentParser();p.add_argument('--kernel',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
s=Path(__file__).resolve().parents[3];sys.path.insert(0,str(s/'kits/XnuPrelink'));from kc_image import Image
raw=a.kernel.read_bytes();outer=Image(raw);im=Image(raw,outer.entries['com.apple.kernel'][1]) if outer.entries else outer
names={n:v for v,nn in im.symbols().items() for n in nn}
exports=a.kernel.parent/'kernel-exports.S'
if exports.exists():
 for key,value in re.findall(r'^\.set (\S+), (0x[0-9a-f]+)$',exports.read_text(),re.M):names.setdefault(key,int(value,16))
def method(fragment):
 found=[v for n,v in names.items() if n.startswith('__ZN16NativeAdrenoRing') and fragment in n]
 if len(found)!=1:raise ValueError((fragment,len(found)))
 return found[0]
u=Uc(UC_ARCH_ARM64,UC_MODE_ARM);u.ctl_set_cpu_model(UC_CPU_ARM64_MAX)
lo=min(v['va'] for v in im.segments if v['size'] and v['name']!='__PAGEZERO')&~4095;hi=(max(v['va']+v['size'] for v in im.segments if v['size'] and v['name']!='__PAGEZERO')+4095)&~4095
u.mem_map(lo,hi-lo)
for seg in im.segments:
 if seg['filesize']:u.mem_write(seg['va'],raw[seg['fileoff']:seg['fileoff']+seg['filesize']])
# The kernel's external stack canary is real data, supplied by the test peer.
for key in ('___stack_chk_guard','_IOLog'):
 at=names[key]
 if not lo<=at<hi:u.mem_map(at&~4095,4096)
 u.mem_write(at,struct.pack('<Q',0x123456789abcdef) if key.endswith('guard') else bytes.fromhex('c0035fd6'))
u.mem_map(0x100000,0x120000);u.reg_write(UC_ARM64_REG_CPACR_EL1,3<<20)
STOP=0x100000;SELF=0x110000;IO=0x112000;CACHE=0x113000;CLOCK=0x114000;OUT=0x115000;WORDS=0x116000;STACK=0x1f0000;RING=0x200000;FENCE=0x210000
CB=[0x101000+i*8 for i in range(9)]
for at in [STOP,*CB]:u.mem_write(at,bytes.fromhex('c0035fd6'))
writes=[];events=[];registers={};now=0;fail_publish=False;fail_wptr=False
X=[UC_ARM64_REG_X0,UC_ARM64_REG_X1,UC_ARM64_REG_X2,UC_ARM64_REG_X3,UC_ARM64_REG_X4,UC_ARM64_REG_X5,UC_ARM64_REG_X6,UC_ARM64_REG_X7]
def ret(value=0):u.reg_write(UC_ARM64_REG_X0,value&0xffffffffffffffff);u.reg_write(UC_ARM64_REG_PC,u.reg_read(UC_ARM64_REG_LR))
def hook(e,pc,n,unused):
 global now
 args=[e.reg_read(v) for v in X]
 if pc==CB[0]:registers[args[1]]=args[2];writes.append((args[1],args[2]));events.append('reg');ret()
 elif pc==CB[1]:e.mem_write(args[2],struct.pack('<I',registers.get(args[1],0)));ret()
 elif pc==CB[2]:events.append('barrier');ret()
 elif pc==CB[3]:events.append('wptr');ret(1 if fail_wptr else 0)
 elif pc==CB[4]:e.mem_write(args[1],struct.pack('<I',0));ret()
 elif pc==CB[5]:events.append('publish');ret(1 if fail_publish else 0)
 elif pc==CB[6]:events.append('acquire');ret()
 elif pc==CB[7]:ret(now)
 elif pc==CB[8]:now+=1000;ret()
 elif pc==names.get('_IOLog'):ret()
u.hook_add(UC_HOOK_CODE,hook)
def call(pc,*args):
 for i,v in enumerate(args[:8]):u.reg_write(X[i],v)
 u.mem_write(STACK,b''.join(struct.pack('<Q',v) for v in args[8:])+bytes(64))
 u.reg_write(UC_ARM64_REG_SP,STACK);u.reg_write(UC_ARM64_REG_LR,STOP)
 u.emu_start(pc,STOP,count=1000000)
 assert u.reg_read(UC_ARM64_REG_PC)==STOP
 return u.reg_read(UC_ARM64_REG_X0)&0xffffffff
def reset():
 global now,fail_publish,fail_wptr
 now=0;fail_publish=False;fail_wptr=False;writes.clear();events.clear();registers.clear();u.mem_write(SELF,bytes(4096))
 u.mem_write(IO,struct.pack('<7Q',1,CB[0],CB[1],CB[2],CB[3],CB[4],0))
 u.mem_write(CACHE,struct.pack('<3Q',1,CB[5],CB[6]));u.mem_write(CLOCK,struct.pack('<3Q',1,CB[7],CB[8]))
 u.mem_write(WORDS,struct.pack('<2I',0x70100001,0))
def attach():return call(method('6attachE'),SELF,IO,CACHE,RING,32768,0x2000000,FENCE,4096,0x3000000,2)
reset();assert attach()==0
assert registers[0x802]&(1<<27) # A640 NO_UPDATE bit; no A740 branch packets
assert call(method('6submitE'),SELF,WORDS,2,OUT)==0
serial=struct.unpack('<I',u.mem_read(OUT,4))[0];assert serial==1
words=struct.unpack('<16I',u.mem_read(RING,64))
assert ((words[4]>>16)&0x7f)==0x46 and words[5]==0x80000004 # A6xx CACHE_FLUSH_TS
assert words[6:9]==(0x3000004,0,1)
assert events.index('publish')<events.index('wptr')
assert call(method('4waitE'),SELF,serial,CLOCK,5000,OUT)==1 # no fabricated fence completion
assert now>=5000
u.mem_write(FENCE+4,struct.pack('<I',serial))
assert call(method('4waitE'),SELF,serial,CLOCK,5000,OUT)==0
assert call(method('6detachE'),SELF)==0
reset();assert attach()==0;fail_publish=True
assert call(method('6submitE'),SELF,WORDS,2,OUT)!=0
assert 'wptr' not in events
assert call(method('6detachE'),SELF)!=0 # retain resources after uncertain DMA state
reset();assert attach()==0;fail_wptr=True
assert call(method('6submitE'),SELF,WORDS,2,OUT)!=0
assert call(method('6detachE'),SELF)!=0
result=dict(passed=True,actual_arm64_object_executed=True,hardware_tested=False,a640_generation=True,a6xx_cache_flush_ts=True,publish_before_wptr=True,missing_fence_times_out=True,real_fence_completes=True,publish_failure_no_kick=True,uncertain_dma_retains_resources=True)
a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
