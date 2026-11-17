#!/usr/bin/env python3
"""Replay 26C5035r's captured magnify block and original preference reader.

Only the optional URL call is replaced. CF/dyld boundaries are modeled; PAC
is skipped in this decision test and covered by the platform suite separately.
"""
import argparse,hashlib,json,mmap,struct
from pathlib import Path
from unicorn import Uc,UC_ARCH_ARM64,UC_MODE_ARM,UC_HOOK_CODE,UC_HOOK_MEM_INVALID
from unicorn.arm64_const import *
p=argparse.ArgumentParser();p.add_argument('cache',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
D=Path(__file__).resolve().parents[1];site=json.loads((D/'compat/cpu-display/magnify-26C5035r.json').read_text())['patches'][0]
handles=[];maps=[]
for path in a.cache.parent.glob('dyld_shared_cache_arm64e*'):
 if not path.is_file() or ':' in path.name or path.suffix in ('.a2s','.map','.symbols'):continue
 f=path.open('rb');h=f.read(4096)
 if not h.startswith(b'dyld_v1'):f.close();continue
 m=mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ);handles.append((f,m));off,n=struct.unpack_from('<II',m,16)
 for i in range(n):
  va,size,fo,_,_=struct.unpack_from('<QQQII',m,off+i*32);maps.append((va,va+size,m,fo))
def read(va,n):
 for lo,hi,m,fo in maps:
  if lo<=va and va+n<=hi:return bytes(m[fo+va-lo:fo+va-lo+n])
 raise ValueError(hex(va))
BLOCK=0x18f0f72a0;PATCH=0x18f0f7304;GLOBAL=0x1eda09d98;PREFERENCE=0x18f0f74d0
STOP=0x100000;STACK=0x200000;MEM=0x300000
rows=[]
def replay(patched,query='unavailable',preference=None,platform=1,bundle=True,url=True,cache_preference=False):
 u=Uc(UC_ARCH_ARM64,UC_MODE_ARM);u.ctl_set_cpu_model(UC_CPU_ARM64_MAX);pages=set();events=[]
 def mapping(va,original=True):
  at=va&~4095
  if at in pages:return
  data=bytearray(read(at,4096)) if original else bytearray(4096)
  if at==(PATCH&~4095):
   assert hashlib.sha256(data).hexdigest()==site['page_sha256']
   if patched:
    offset=PATCH&4095;assert data[offset:offset+4]==bytes.fromhex(site['original']);data[offset:offset+4]=bytes.fromhex(site['replacement'])
    assert hashlib.sha256(data).hexdigest()==site['patched_page_sha256']
  u.mem_map(at,4096);u.mem_write(at,bytes(data));pages.add(at)
 for at in (STOP,STACK,STACK+4096,MEM):mapping(at,False)
 mapping(GLOBAL,False);mapping(0x1eb70834c,False);mapping(0x1e01646e0,False)
 u.mem_write(0x1e01646e0,struct.pack('<Q',MEM));u.mem_write(MEM,struct.pack('<Q',MEM+0x100))
 u.mem_write(0x1eb70834c,struct.pack('<I',2 if preference is None else int(preference)))
 u.mem_write(0x1eda09daa,bytes([int(cache_preference)]))
 def fault(e,access,at,size,value,unused):mapping(at);return True
 def ret(value=None):
  if value is not None:evalue=value;u.reg_write(UC_ARM64_REG_X0,evalue)
  u.reg_write(UC_ARM64_REG_PC,u.reg_read(UC_ARM64_REG_LR))
 def hook(e,pc,size,unused):
  if pc==0x1900ff000:ret(MEM+0x200 if bundle else 0);return
  if pc==0x1901085d0:ret(platform);return
  if pc==0x1901108f0:ret(MEM+0x300 if url else 0);return
  if pc==0x1900ffaf0:
   events.append('CFURL-is-application-query')
   if query=='blocked':e.emu_stop();return
   if query=='unavailable':ret(0);return
   e.mem_write(e.reg_read(UC_ARM64_REG_X2),struct.pack('<Q',MEM+0x400));ret(1);return
  if pc==0x1900f4290:ret(1);return # CFGetTypeID of Boolean fixtures
  if pc==0x1900f4150:ret(1);return # CFBooleanGetTypeID
  if pc==0x1900f4160:
   value=e.reg_read(UC_ARM64_REG_X0);ret(int(query=='true') if value==MEM+0x400 else 1);return
  if pc==0x1900ff010:events.append('bundle-metadata');ret(MEM+0x500);return
  if pc==0x1900f4370:events.append(('CFRelease',e.reg_read(UC_ARM64_REG_X0)));ret();return
  if pc==PREFERENCE:events.append('native-AppleMagnifiedMode-reader')
  if pc==0x18f0fa030:
   events.append(('CF-preference-boundary',preference))
   e.mem_write(e.reg_read(UC_ARM64_REG_X1),bytes([int(bool(preference))]));e.mem_write(e.reg_read(UC_ARM64_REG_X2),bytes([int(preference is not None)]));ret(0);return
  word=struct.unpack('<I',e.mem_read(pc,4))[0]
  if word in (0xd503237f,0xd50323ff):e.reg_write(UC_ARM64_REG_PC,pc+4)
  elif word==0xd65f0fff:ret()
 u.hook_add(UC_HOOK_MEM_INVALID,fault);u.hook_add(UC_HOOK_CODE,hook)
 mapping(BLOCK);u.reg_write(UC_ARM64_REG_SP,STACK+0x1800);u.reg_write(UC_ARM64_REG_LR,STOP)
 u.emu_start(BLOCK,STOP,count=3000)
 return u.reg_read(UC_ARM64_REG_PC),u.mem_read(GLOBAL,1)[0],events
pc,mode,events=replay(False,query='blocked');assert pc==0x1900ffaf0 and events==['CFURL-is-application-query']
rows.append(dict(test='original-reaches-captured-LS-call',passed=True))
for preference in (None,False,True):
 for platform in (1,2,6):
  for bundle,url in ((True,True),(False,True),(True,False)):
   for cached in (False,True):
    original=replay(False,preference=preference,platform=platform,bundle=bundle,url=url,cache_preference=cached)
    patched=replay(True,query='blocked',preference=preference,platform=platform,bundle=bundle,url=url,cache_preference=cached)
    assert original[0]==patched[0]==STOP and original[1]==patched[1]==int(preference is True)
    assert patched[2]==[x for x in original[2] if x!='CFURL-is-application-query']
    assert 'bundle-metadata' not in patched[2]
    assert 'native-AppleMagnifiedMode-reader' in patched[2]
    if platform==1 and bundle and url:assert ('CFRelease',MEM+0x300) in patched[2]
    rows.append(dict(test='native-unavailable-and-patched-paths-match',preference=preference,platform=platform,bundle=bundle,url=url,cached_preference=cached,passed=True))
for query in ('false','true'):
 pc,mode,events=replay(False,query=query,preference=None)
 assert pc==STOP and mode==0 and ('CFRelease',MEM+0x400) in events and ('CFRelease',MEM+0x300) in events
 assert ('bundle-metadata' in events)==(query=='true')
 rows.append(dict(test='original-available-property-control',query=query,passed=True))
report=dict(passed=True,build='26C5035r',cases=len(rows),actual_captured_block_and_preference_reader_executed=True,modeled_boundaries=['CoreFoundation bundle/URL/property functions','dyld platform','CF preferences backend'],PAC_emulated=False,device_tested=False,rows=rows)
a.output.write_text(json.dumps(report,indent=2)+'\n');print('Magnify block and native preference replay:',len(rows),'cases passed')
