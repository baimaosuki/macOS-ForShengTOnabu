#!/usr/bin/env python3
"""Replay 26C5035r framebuffer allocation and the unchanged software format guard.

The real allocation instructions and dictionary writes run in Unicorn. CF
transport, device capability queries and the IOSurface kernel call are modeled;
this is not a hardware rendering test.
"""
import argparse
import hashlib
import json
import mmap
import struct
from pathlib import Path
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_INVALID
from unicorn.arm64_const import *

ap=argparse.ArgumentParser();ap.add_argument('cache',type=Path);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
device=Path(__file__).resolve().parents[1]
fixture=json.loads((device/'compat/cpu-display/userland-26C5035r.json').read_text())
entry=next(e for e in fixture['patches'] if e['name']=='IOMFBDisplay::CPU-final-framebuffer-BGRA')
handles=[];maps=[]
for p in a.cache.parent.glob('dyld_shared_cache_arm64e*'):
 if not p.is_file() or ':' in p.name or p.suffix in ('.a2s','.map','.symbols'):continue
 f=p.open('rb');h=f.read(4096)
 if not h.startswith(b'dyld_v1'):f.close();continue
 b=mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ);handles.append((f,b));off,n=struct.unpack_from('<II',b,16)
 for i in range(n):
  va,size,fileoff,_,_=struct.unpack_from('<QQQII',b,off+i*32);maps.append((va,va+size,b,fileoff))
def read(va,n):
 for lo,hi,b,off in maps:
  if lo<=va and va+n<=hi:return bytes(b[off+va-lo:off+va-lo+n])
 raise ValueError(hex(va))
STOP=0x100000;STACK=0x120000;MEM=0x200000;BGRA=0x42475241
rows=[]
def machine(patched=True):
 u=Uc(UC_ARCH_ARM64,UC_MODE_ARM);u.ctl_set_cpu_model(UC_CPU_ARM64_MAX);pages=set()
 def mapping(at,original=True):
  at&=~4095
  if at in pages:return
  data=bytearray(read(at,4096)) if original else bytearray(4096)
  if patched and at==(int(entry['va'],16)&~4095):
   assert hashlib.sha256(data).hexdigest()==entry['page_sha256']
   i=int(entry['va'],16)&4095;old=bytes.fromhex(entry['original']);assert data[i:i+len(old)]==old;data[i:i+len(old)]=bytes.fromhex(entry['replacement'])
  u.mem_map(at,4096);u.mem_write(at,bytes(data));pages.add(at)
 for at in range(MEM,MEM+0x30000,4096):mapping(at,False)
 for at in range(STACK,STACK+0x20000,4096):mapping(at,False)
 mapping(STOP,False)
 mapping(0x1e01cb000);u.mem_write(0x1e01cbd68,struct.pack('<Q',MEM+0x20000))
 def fault(e,access,at,size,value,unused):
  try:mapping(at);return True
  except ValueError:raise AssertionError(f'unmapped {at:#x} at {e.reg_read(UC_ARM64_REG_PC):#x}')
 def pac(e,pc,size,unused):
  w=struct.unpack('<I',e.mem_read(pc,4))[0]
  if w in (0xd503237f,0xd50323ff) or (w&0xffff0000)==0xdac10000:e.reg_write(UC_ARM64_REG_PC,pc+4)
  elif w==0xd65f0fff:e.reg_write(UC_ARM64_REG_PC,e.reg_read(UC_ARM64_REG_LR))
 u.hook_add(UC_HOOK_MEM_INVALID,fault);u.hook_add(UC_HOOK_CODE,pac)
 u.reg_write(UC_ARM64_REG_CPACR_EL1,3<<20);u.reg_write(UC_ARM64_REG_SP,STACK+0x1f000);u.reg_write(UC_ARM64_REG_LR,STOP)
 return u,mapping

def skip(e,pc,result=0):e.reg_write(UC_ARM64_REG_X0,result);e.reg_write(UC_ARM64_REG_PC,pc+4)
def call(e,pc,target):e.reg_write(UC_ARM64_REG_LR,pc+4);e.reg_write(UC_ARM64_REG_PC,target)

# Native final selection -> reuse check -> Display allocation -> IOMFB allocation
# -> IOSurface allocation. Incompatible old buffers must not be reused.
for patched in (False,True):
 for incoming in (BGRA,0x52476841,0x26424741,0x62336138,0x77343061):
  for force in (0,1):
   for old in (None,incoming):
    u,mp=machine(patched);display=MEM;vt=MEM+0x10000;dims=MEM+0x11000;oldsurface=MEM+0x12000
    u.mem_write(display,struct.pack('<Q',vt));u.mem_write(display+0x428,struct.pack('<H',0x800));u.mem_write(dims+8,struct.pack('<II',1600,2560))
    u.reg_write(UC_ARM64_REG_X19,display);u.reg_write(UC_ARM64_REG_X23,dims);u.reg_write(UC_ARM64_REG_X24,incoming);u.reg_write(UC_ARM64_REG_X26,1)
    u.mem_write(STACK+0x1f000+0x64,struct.pack('<I',force))
    if old is not None:
     u.reg_write(UC_ARM64_REG_X22,oldsurface);u.mem_write(oldsurface,struct.pack('<Q',vt));u.mem_write(oldsurface+0x38,struct.pack('<III',1600,2560,old))
    reached=[]
    def hook(e,pc,size,unused):
     if pc==0x18e1e6710:reached.append(('reuse',e.reg_read(UC_ARM64_REG_X24)));e.emu_stop()
     elif pc==0x18e1e6434:skip(e,pc,0) # surface metadata accessor
     elif pc==0x18e1e657c:skip(e,pc,0) # display color primaries
     elif pc in (0x18dfd3eb4,0x18dfd3fb4):skip(e,pc) # empty cache lock transport
     elif pc==0x18dfd4064:call(e,pc,0x18e2002bc) # real board create_surface virtual target
     elif pc==0x18e0ed204:
      reached.append(('allocate',e.reg_read(UC_ARM64_REG_X3)));assert e.reg_read(UC_ARM64_REG_X1)==1600 and e.reg_read(UC_ARM64_REG_X2)==2560;e.emu_stop()
    u.hook_add(UC_HOOK_CODE,hook);mp(0x18e1e63bc)
    try:u.emu_start(0x18e1e63bc,STOP,count=3000)
    except Exception:
     print('CASE',patched,hex(incoming),force,old,'PC',hex(u.reg_read(UC_ARM64_REG_PC)));raise
    selected=BGRA if patched or force else incoming
    assert reached==[('reuse' if old==selected else 'allocate',selected)],(patched,hex(incoming),force,old,reached)
    rows.append(dict(test='native-framebuffer-allocation',patched=patched,incoming=hex(incoming),force=force,old=None if old is None else hex(old),path=reached[0][0],selected=hex(selected)))

# Original SkyLight guard: feed the selected format through the actual compare
# tree, retaining rejected-format controls rather than bypassing abort.
for pixel in (BGRA,0x52476841,0x26424741,0x62336138,0x77343061):
 u,mp=machine();u.reg_write(UC_ARM64_REG_X19,MEM);reached=[]
 def guard(e,pc,size,unused):
  if pc==0x1826ef88c:skip(e,pc,pixel)
  elif pc in (0x1826ef94c,0x1826ef9f4):reached.append(pc);e.emu_stop()
 u.hook_add(UC_HOOK_CODE,guard);mp(0x1826ef888);u.emu_start(0x1826ef888,STOP,count=200)
 assert reached==[0x1826ef9f4 if pixel==BGRA else 0x1826ef94c]
 rows.append(dict(test='native-destination-format-guard',pixel_format=hex(pixel),accepted=pixel==BGRA))

# Execute CAIOSurfaceCreate from entry to the real IOSurfaceCreate callsite.
# Capture the actual dictionary writes, including stride/allocation/plane keys.
u,mp=machine();u.reg_write(UC_ARM64_REG_X0,1600);u.reg_write(UC_ARM64_REG_X1,2560);u.reg_write(UC_ARM64_REG_X2,BGRA);u.reg_write(UC_ARM64_REG_X3,0x400);u.reg_write(UC_ARM64_REG_X7,MEM)
properties={};reached=[]
# Model dyld-rebased CF key/boolean pointers, which are encoded on disk.
mp(0x1e0172000);mp(0x1e0164000)
for index,off in enumerate((0x740,0x748,0xa30,0x840,0x8b8,0x750,0x720,0x868,0x858,0x8b0,0x888,0xa00)):
 key=MEM+0x21000+index*16;u.mem_write(0x1e0172000+off,struct.pack('<Q',key));u.mem_write(key,struct.pack('<Q',index+1))
for index,off in enumerate((0,0x10)):
 key=MEM+0x22000+index*16;u.mem_write(0x1e0164000+off,struct.pack('<Q',key));u.mem_write(key,struct.pack('<Q',index))
u.mem_write(MEM,struct.pack('<QQQQQ',1,64,1,4096,0))
int_calls={0x18def23b8:'Width',0x18def23d0:'Height',0x18def23e8:'PixelFormat',0x18def2400:'CacheMode',0x18def24e4:'AllocSize',0x18def24fc:'MapCacheAttribute',0x18def14ac:'BytesPerElement',0x18def14c4:'BytesPerRow'}
def props(e,pc,size,unused):
 if pc==0x18deee644:skip(e,pc,MEM+0x1000)
 elif pc==0x18deee660:skip(e,pc,0)
 elif pc in int_calls:properties[int_calls[pc]]=e.reg_read(UC_ARM64_REG_X2);skip(e,pc)
 elif pc in (0x18def2438,0x18def246c):skip(e,pc,0)
 elif pc in (0x18def2568,0x18def2540,0x18def25b4):skip(e,pc)
 elif pc==0x18def25bc:reached.append('IOSurfaceCreate');e.emu_stop()
 # Calls outside the validated plain-BGRA path are deliberately not skipped.
u.hook_add(UC_HOOK_CODE,props);mp(0x18deee5d8)
try:u.emu_start(0x18deee5d8,STOP,count=20000)
except Exception:
 print('PROPS PC',hex(u.reg_read(UC_ARM64_REG_PC)),properties);raise
assert reached==['IOSurfaceCreate'];assert properties['Width']==1600 and properties['Height']==2560 and properties['PixelFormat']==BGRA
assert properties['BytesPerElement']==4 and properties['BytesPerRow']==6400 and properties['AllocSize']==16384000
rows.append(dict(test='native-IOSurfaceCreate-properties',properties=properties))
a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(dict(passed=True,actual_original_instructions=True,native_format_guard_preserved=True,CF_and_kernel_transport_modeled=True,PAC_tested=False,device_tested=False,cases=rows),indent=2)+'\n');print('CPU framebuffer native allocation/format checks:',len(rows),'passed')
