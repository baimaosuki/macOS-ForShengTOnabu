#!/usr/bin/env python3
"""Replay the captured 26C5035r SafeAperture caller and URL getter.

Actual original instructions run in Unicorn; ObjC/Foundation boundaries are
modeled. PAC is skipped here and covered by the separate platform tests.
"""
import argparse,hashlib,json,mmap,struct
from pathlib import Path
from unicorn import Uc,UC_ARCH_ARM64,UC_MODE_ARM,UC_HOOK_CODE,UC_HOOK_MEM_INVALID
from unicorn.arm64_const import *
ap=argparse.ArgumentParser();ap.add_argument('cache',type=Path);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
D=Path(__file__).resolve().parents[1]
fixture=json.loads((D/'compat/cpu-display/safe-aperture-26C5035r.json').read_text());site=fixture['patches'][0]
handles=[];maps=[]
for p in a.cache.parent.glob('dyld_shared_cache_arm64e*'):
 if not p.is_file() or ':' in p.name or p.suffix in ('.a2s','.map','.symbols'):continue
 f=p.open('rb');h=f.read(4096)
 if not h.startswith(b'dyld_v1'):f.close();continue
 m=mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ);handles.append((f,m));off,n=struct.unpack_from('<II',m,16)
 for i in range(n):
  va,size,fo,_,_=struct.unpack_from('<QQQII',m,off+i*32);maps.append((va,va+size,m,fo))
def read(va,n):
 for lo,hi,m,fo in maps:
  if lo<=va and va+n<=hi:return bytes(m[fo+va-lo:fo+va-lo+n])
 raise ValueError(hex(va))
GETTER=0x18acbb5e8;CALLER=0x18acbaeec;PATCH=0x18acbb658;STOP=0x100000;MEM=0x200000;STACK=0x300000
rows=[]
def replay(patched,mode,bit,available=True,boolean=True,responds=True):
 u=Uc(UC_ARCH_ARM64,UC_MODE_ARM);u.ctl_set_cpu_model(UC_CPU_ARM64_MAX);pages=set();events=[]
 def mapping(at,original=True):
  at&=~4095
  if at in pages:return
  data=bytearray(read(at,4096)) if original else bytearray(4096)
  if at==(PATCH&~4095):
   assert hashlib.sha256(data).hexdigest()==site['page_sha256']
   if patched:
    index=PATCH&4095;assert data[index:index+4]==bytes.fromhex(site['original']);data[index:index+4]=bytes.fromhex(site['replacement'])
    assert hashlib.sha256(data).hexdigest()==site['patched_page_sha256']
  u.mem_map(at,4096);u.mem_write(at,bytes(data));pages.add(at)
 for at in (STOP,MEM,STACK,STACK+4096):mapping(at,False)
 mapping(0x1e0171a20);u.mem_write(0x1e0171a20,struct.pack('<Q',MEM+0x600))
 def fault(e,access,at,size,value,unused):mapping(at);return True
 def ret(value=None):
  if value is not None:u.reg_write(UC_ARM64_REG_X0,value)
  u.reg_write(UC_ARM64_REG_PC,u.reg_read(UC_ARM64_REG_LR))
 def hook(e,pc,size,unused):
  if pc in (0x190108f20,0x190108d50,0x190108ef0):ret();return
  if pc==0x190108f10:events.append(('release',e.reg_read(UC_ARM64_REG_X0)));ret();return
  if pc==0x190108ee0:ret(int(responds));return
  if pc==0x190055670:events.append(('optional-getter',GETTER));e.reg_write(UC_ARM64_REG_PC,GETTER);return
  if pc==0x190062e20:ret(MEM+0x200);return
  if pc==0x190045d30:ret(MEM+0x300);return
  if pc==0x190055590:
   events.append(('LS-dependent-URL-query',pc))
   if available:e.mem_write(e.reg_read(UC_ARM64_REG_X2),struct.pack('<Q',MEM+0x500));ret(1)
   else:e.emu_stop()
   return
  if pc==0x190045590:ret(int(boolean));return
  word=struct.unpack('<I',e.mem_read(pc,4))[0]
  if word in (0xd503237f,0xd50323ff):e.reg_write(UC_ARM64_REG_PC,pc+4)
  elif word==0xd65f0fff:ret()
 u.hook_add(UC_HOOK_MEM_INVALID,fault);u.hook_add(UC_HOOK_CODE,hook)
 mapping(CALLER);u.mem_write(MEM,struct.pack('<I',mode))
 for register,value in ((UC_ARM64_REG_X0,MEM+0x400),(UC_ARM64_REG_X1,MEM),(UC_ARM64_REG_X2,MEM+0x100),(UC_ARM64_REG_X3,bit),(UC_ARM64_REG_X4,MEM+0x800),(UC_ARM64_REG_LR,STOP),(UC_ARM64_REG_SP,STACK+0x1800)):u.reg_write(register,value)
 u.emu_start(CALLER,STOP,count=2000)
 return u.reg_read(UC_ARM64_REG_PC),struct.unpack('<I',u.mem_read(MEM,4))[0],events
pc,mode,events=replay(False,7,1,available=False);assert pc==0x190055590
rows.append(dict(test='original-stops-at-captured-LS-edge',passed=True))
for mode in range(16):
 for bit in (1,2,4,8):
  pc,result,events=replay(True,mode,bit,available=False)
  assert pc==STOP and result==mode and not any(e[0]=='LS-dependent-URL-query' for e in events)
  assert ('release',MEM+0x100) in events and ('release',MEM+0x300) in events
  rows.append(dict(test='patched-native-failure-keeps-mode-and-releases',mode=mode,bit=bit,passed=True))
for boolean in (False,True):
 for mode in range(16):
  pc,result,events=replay(False,mode,4,boolean=boolean);assert pc==STOP and result==(mode|4 if boolean else mode&~4)
  rows.append(dict(test='original-success-bit-control',mode=mode,boolean=boolean,passed=True))
for patched in (False,True):
 pc,result,events=replay(patched,9,2,responds=False);assert pc==STOP and result==9 and not any(e[0]=='optional-getter' for e in events)
 rows.append(dict(test='native-no-provider-control',patched=patched,passed=True))
report=dict(passed=True,build='26C5035r',actual_original_caller_and_getter_executed=True,modeled_boundaries=['ObjC dispatch and reference counting','Foundation URL query'],PAC_emulated=False,device_tested=False,cases=len(rows),rows=rows)
a.output.write_text(json.dumps(report,indent=2)+'\n');print('SafeAperture native instruction replay:',len(rows),'cases passed')
