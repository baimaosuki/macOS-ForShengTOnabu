#!/usr/bin/env python3
"""Replay exact 26C5035r CPU selection and allocation decisions, with negative controls."""
import argparse,hashlib,json,mmap,struct
from pathlib import Path
from unicorn import Uc,UC_ARCH_ARM64,UC_MODE_ARM,UC_HOOK_CODE,UC_HOOK_MEM_INVALID
from unicorn.arm64_const import *
p=argparse.ArgumentParser();p.add_argument('cache',type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
S=Path(__file__).resolve().parents[3];fixture=json.loads((S/'devices/nabu/compat/cpu-display/userland-26C5035r.json').read_text());maps=[];handles=[]
for path in a.cache.parent.glob('dyld_shared_cache_arm64e*'):
 if path.suffix in ('.map','.symbols','.a2s') or not path.is_file():continue
 f=path.open('rb');h=f.read(4096)
 if not h.startswith(b'dyld_v1'):f.close();continue
 b=mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ);handles.append((f,b));off,n=struct.unpack_from('<II',b,16)
 for i in range(n):
  va,size,fileoff,_,_=struct.unpack_from('<QQQII',b,off+i*32);maps.append((va,va+size,b,fileoff))
def read(va,n):
 for lo,hi,b,off in maps:
  if lo<=va and va+n<=hi:return bytes(b[off+va-lo:off+va-lo+n])
 raise ValueError(hex(va))
STOP=0x100000;STACK=0x120000;MEM=0x200000;TOKEN=0x1eb1b7640;STATE=0x1eb1b7638
CALLBACK=0x1828dfd58;DISPATCH=0x188076560
cases=[]
def machine(addresses,patched=True):
 u=Uc(UC_ARCH_ARM64,UC_MODE_ARM);u.ctl_set_cpu_model(UC_CPU_ARM64_MAX);pages=set()
 def mapping(va,n=1,original=True):
  for at in range(va&~4095,(va+n+4095)&~4095,4096):
   if at in pages:continue
   pages.add(at);u.mem_map(at,4096)
   if original:
    try:raw=bytearray(read(at,4096))
    except ValueError:raw=bytearray(4096)
    if patched:
     for e in fixture['patches']:
      v=int(e['va'],16)
      if v&~4095==at:
       assert hashlib.sha256(raw).hexdigest()==e['page_sha256']
       q=v&4095;old=bytes.fromhex(e['original']);new=bytes.fromhex(e['replacement']);assert raw[q:q+len(old)]==old;raw[q:q+len(old)]=new
    u.mem_write(at,bytes(raw))
 for va,n in addresses:mapping(va,n)
 for va,n in ((STOP,4096),(STACK,0x10000),(MEM,0x20000),(TOKEN&~4095,4096),(0x1eb1b6000,4096),(0x1eb1ab000,4096)):mapping(va,n,False)
 u.reg_write(UC_ARM64_REG_CPACR_EL1,3<<20);u.reg_write(UC_ARM64_REG_SP,STACK+0xf000);u.reg_write(UC_ARM64_REG_LR,STOP)
 def pac(e,pc,size,unused):
  w=struct.unpack('<I',e.mem_read(pc,4))[0]
  # Authentication tested independently by the native PAC suite; this replay checks decisions.
  if w in (0xd503237f,0xd50323ff) or (w&0xffff0000)==0xdac10000:e.reg_write(UC_ARM64_REG_PC,pc+4)
  elif w==0xd65f0fff:e.reg_write(UC_ARM64_REG_PC,e.reg_read(UC_ARM64_REG_LR))
 def fault(e,access,at,size,value,unused):
  print('FAULT',hex(e.reg_read(UC_ARM64_REG_PC)),hex(at),size);return False
 u.hook_add(UC_HOOK_MEM_INVALID,fault)
 u.hook_add(UC_HOOK_CODE,pac);return u,mapping
def ret(u,v=0):u.reg_write(UC_ARM64_REG_X0,v);u.reg_write(UC_ARM64_REG_PC,u.reg_read(UC_ARM64_REG_LR))
# Original Metal timeout reaches its real abort; patched callback simply preserves no-device state.
for patched in (False,True):
 stubs=[0x188076610,0x188080260,0x188000fa0,0x1880770b0,0x1880766e0,0x188076630,0x1880765e0,0x18806c2b0,0x18806c270,0x182951bf8]
 u,mp=machine([(CALLBACK,0x600),*( (v,4) for v in stubs),(0x1e01cb000,4096),(0x1ed947000,4096),(0x182c55000,4096),(0x1eb1ae000,4096)],patched)
 u.mem_write(0x1e01cbd68,struct.pack('<Q',MEM));u.mem_write(MEM,b'\0'*8);hit=[]
 def abort_hook(e,pc,size,unused):
  if pc==0x18806c270:hit.append('original-abort');e.emu_stop()
  elif pc in stubs:ret(e,{0x188076610:MEM+0x100,0x188076630:1,0x188080260:MEM+0x200}.get(pc,0))
 u.hook_add(UC_HOOK_CODE,abort_hook);u.emu_start(CALLBACK,STOP,count=5000)
 assert hit==([] if patched else ['original-abort']);assert bytes(u.mem_read(STATE,1))==b'\0'
 cases.append(dict(test='capability',patched=patched,original_abort_reached=bool(hit),metal_state=0))
# Execute the actual default block; dispatch_once transport invokes the actual patched callback.
u,mp=machine([(0x182836000,0x3000),(CALLBACK,32),(DISPATCH,4)])
u.mem_write(TOKEN,b'\0'*8);once=[]
def once_hook(e,pc,size,unused):
 if pc==DISPATCH:
  assert e.reg_read(UC_ARM64_REG_X0)==TOKEN
  cb,_=machine([(CALLBACK,32)]);cb.emu_start(CALLBACK,STOP,count=32);assert bytes(cb.mem_read(STATE,1))==b'\0'
  e.mem_write(TOKEN,b'\xff'*8);once.append('callback-completed');ret(e)
u.hook_add(UC_HOOK_CODE,once_hook);u.emu_start(0x1828382b0,STOP,count=300)
assert once==['callback-completed'];assert bytes(u.mem_read(0x1eb1b6fb0,1))==b'\1'
u.mem_write(0x1eb1b6f98,b'\xff'*8);u.mem_write(0x1eb1b6fa8,b'\xff'*8);reached=[]
def select_hook(e,pc,size,unused):
 if pc in (0x182836bd8,0x182836b48):reached.append(hex(pc));e.emu_stop()
u.hook_add(UC_HOOK_CODE,select_hook);u.reg_write(UC_ARM64_REG_LR,STOP);u.emu_start(0x182837070,STOP,count=300);assert reached==['0x182836bd8']
cases.append(dict(test='original-selector',dispatch_once_completion_modeled=True,actual_activate_sw_reached=True,metal_state=0))
# The original shadow initializer consumes the same zero capability and disables Metal shadows.
s,_=machine([(0x182838240,128)]);s.mem_write(TOKEN,b'\xff'*8);s.mem_write(0x1eb1abad0,b'\1');s.emu_start(0x182838240,STOP,count=100);assert bytes(s.mem_read(0x1eb1abad0,1))==b'\0'
cases.append(dict(test='original-shadow-initializer',disabled=True))
# Pixel format query remains original. Run from its BL through actual pool matching/allocation arguments.
for patched in (False,True):
 u,mp=machine([(0x182acde6c,0x150),(0x18801ad70,4),(0x18801b070,4),(0x182805a44,4)],patched)
 u.reg_write(UC_ARM64_REG_X19,MEM+0x100);u.reg_write(UC_ARM64_REG_X21,MEM+0x400);u.reg_write(UC_ARM64_REG_X22,MEM+0x800);u.reg_write(UC_ARM64_REG_X29,STACK+0xe000)
 u.reg_write(UC_ARM64_REG_D12,struct.unpack('<Q',struct.pack('<d',1600.0))[0]);u.reg_write(UC_ARM64_REG_D14,struct.unpack('<Q',struct.pack('<d',2560.0))[0]);u.reg_write(UC_ARM64_REG_S13,0x3f800000)
 # no old surface: drive the actual new-allocation branch
 u.mem_write(MEM+0x800+0x558,b'\0'*16);result=[]
 def pool_hook(e,pc,size,unused):
  if pc==0x18801ad70:ret(e,115)
  elif pc==0x18801b070:ret(e,0)
  elif pc==0x182805a44:result.append(e.reg_read(UC_ARM64_REG_X4));e.emu_stop()
 u.hook_add(UC_HOOK_CODE,pool_hook);u.emu_start(0x182acde6c,STOP,count=300)
 assert result==([80] if patched else [115]),result
 cases.append(dict(test='actual-SurfacePool-Acquire-format',patched=patched,pixel_format=result[0],query_before_edit=True))
a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(dict(passed=True,build='26C5035r',actual_original_instructions=True,PAC_not_tested=True,dispatch_once_transport_modeled=True,device_tested=False,cases=cases),indent=2)+'\n');print(a.output.read_text())
