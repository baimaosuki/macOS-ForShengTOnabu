#!/usr/bin/env python3
"""Replay the original legacy render wrapper, native clip and finalizer.

Runs the exact cache instructions up to the original drawing entry and its ROI
assertion guard. Allocation, locks and CA shape algebra/refcounts are modeled;
this is not a pixel renderer or a hardware display test.
"""
import argparse
import hashlib
import json
import mmap
import struct
from pathlib import Path
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_INVALID
from unicorn.arm64_const import *

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('cache', type=Path)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
S = Path(__file__).resolve().parents[3]
fixture = json.loads((S / 'devices/nabu/compat/cpu-display/backdrop-26C5035r.json').read_text())
maps = []; handles = []
for path in a.cache.parent.glob('dyld_shared_cache_arm64e*'):
    if path.suffix in ('.map', '.a2s', '.symbols') or not path.is_file(): continue
    f = path.open('rb'); h = f.read(4096)
    if not h.startswith(b'dyld_v1'): f.close(); continue
    m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ); handles.append((f, m))
    off, n = struct.unpack_from('<II', m, 16)
    for i in range(n):
        va, size, fileoff, _, _ = struct.unpack_from('<QQQII', m, off+i*32)
        maps.append((va, va+size, m, fileoff))
def read(va, n):
    for lo, hi, m, off in maps:
        if lo <= va and va+n <= hi: return bytes(m[off+va-lo:off+va-lo+n])
    raise ValueError(hex(va))
def call_target(pc):
    op = struct.unpack('<I', read(pc, 4))[0]; assert op>>26 == 0b100101
    imm = op & 0x3ffffff; imm -= 0x4000000 if imm & 0x2000000 else 0
    return pc+imm*4
calls = {call_target(pc): name for pc, name in (
    (0x18e01884c, 'unref'), (0x18e018878, 'malloc'),
    (0x18e01889c, 'lock'), (0x18e0188c0, 'intersect'),
    (0x18e0188c8, 'ref'), (0x18e0188e0, 'unlock'),
    (0x18e018914, 'union'), (0x18e01fe8c, 'intersect-in-place'),
    (0x18e036874, 'kdebug'))}
STOP=0x100000; MEM=0x200000; STACK=0x300000
UPDATE=MEM; RENDERER=MEM+0x1000; SHAPES=MEM+0x2000; ARRAY=MEM+0x3000
CLIP=0x18e01fff0; FINALIZE=0x18e01880c; DRAW=0x18df3d8d4
rows=[]
for patched, count, existing, clipped, ready in (
    (False,0,False,True,False), (False,3,False,True,False),
    (True,0,False,True,False), (True,3,False,True,False),
    (True,3,True,True,False), (True,3,True,True,True),
    (True,3,False,False,True), (True,3,False,False,False)):
    u=Uc(UC_ARCH_ARM64, UC_MODE_ARM); u.ctl_set_cpu_model(UC_CPU_ARM64_MAX)
    pages=set(); events=[]; locks=[]; refs={}; sets={1:set()}; next_shape=[SHAPES]
    def mapping(va, n=1, original=True):
        for at in range(va&~4095, (va+n+4095)&~4095, 4096):
            if at in pages: continue
            pages.add(at); u.mem_map(at,4096)
            if original:
                raw=bytearray(read(at,4096))
                if patched:
                    for entry in fixture['patches']:
                        v=int(entry['va'],16)
                        if v&~4095==at:
                            old=bytes.fromhex(entry['original']); q=v&4095
                            assert raw[q:q+len(old)]==old
                            raw[q:q+len(old)]=bytes.fromhex(entry['replacement'])
                u.mem_write(at,bytes(raw))
    def fault(e, access, at, size, value, unused):
        mapping(at,size); return True
    u.hook_add(UC_HOOK_MEM_INVALID,fault)
    for at,n in ((STOP,4096),(MEM,0x20000),(STACK,0x10000)): mapping(at,n,False)
    def put(at,v,size=8): u.mem_write(at,int(v).to_bytes(size,'little'))
    def get(at,size=8): return int.from_bytes(u.mem_read(at,size),'little')
    def shape(values):
        at=next_shape[0]; next_shape[0]+=16; sets[at]=set(values); refs[at]=1; return at
    def ret(value=0): u.reg_write(UC_ARM64_REG_X0,value); u.reg_write(UC_ARM64_REG_PC,u.reg_read(UC_ARM64_REG_LR))
    def release(at):
        if at not in (1,0x18e255938): refs[at]-=1; assert refs[at]>=0
    # Three native Backdrop entries; list order is reversed into the array.
    expected=[]
    for i in range(count):
        entry=MEM+0x4000+i*0x100; link=MEM+0x5000+i*0x100; ctx=MEM+0x6000+i*0x400
        put(entry,MEM+0x4000+(i+1)*0x100 if i+1<count else 0)
        put(entry+0x10,link); put(link+8,ctx)
        value=shape({i,i+1}); put(ctx+0x2d0,value)
        # The false finalizer argument must select +0x2d0, not +0x2a8.
        put(ctx+0x2a8,shape({100+i})); put(entry+0x7f,8,1)
        expected.insert(0,{i,i+1})
    put(UPDATE+0x220,MEM+0x4000 if count else 0); put(UPDATE+0x228,count,4)
    put(UPDATE+0x590,(1<<26 if clipped else 0)|(1<<28 if ready else 0))
    if existing:
        put(UPDATE+0x258,ARRAY)
        for i in range(count): put(ARRAY+8*i,shape({200+i}))
    mapping(0x1e01cbd68); put(0x1e01cbd68,MEM+0x18000)
    mapping(0x18e255000)
    # A matching native BackdropGroup exercises the clipping pass as well.
    if clipped and count:
        node=MEM+0x9000; group=MEM+0xa000
        put(UPDATE+0x260,node); put(node+8,group)
        put(group+0x2d8,1); put(group+0x2d0,shape({1,2})); put(group+0x2a8,shape({2,3}))
    def hook(e,pc,size,unused):
        if pc in (CLIP,FINALIZE):
            events.append('clip' if pc==CLIP else 'finalize')
            if pc==FINALIZE: assert e.reg_read(UC_ARM64_REG_X1)==0
        if pc==DRAW:
            assert e.reg_read(UC_ARM64_REG_X0)==RENDERER and e.reg_read(UC_ARM64_REG_X1)==UPDATE
            assert e.reg_read(UC_ARM64_REG_X2)==0
            events.append('draw'); e.emu_stop(); return
        name=calls.get(pc)
        if name:
            x=e.reg_read(UC_ARM64_REG_X0); y=e.reg_read(UC_ARM64_REG_X1)
            if name=='malloc': assert x==count*8; ret(ARRAY)
            elif name=='lock': locks.append(x); ret()
            elif name=='unlock': assert locks.pop()==x; ret()
            elif name=='ref':
                if x not in (1,0x18e255938): refs[x]+=1
                ret(x)
            elif name=='unref': release(x); ret()
            elif name=='intersect': ret(shape(sets[x]&sets[y]))
            elif name in ('union','intersect-in-place'):
                old=get(x); values=sets[old]|sets[y] if name=='union' else sets[old]&sets[y]
                new=shape(values); release(old); put(x,new); ret()
            elif name=='kdebug': ret()
            return
        op=struct.unpack('<I',e.mem_read(pc,4))[0]
        # PAC transport is tested separately; no changes to signed return flow.
        if op in (0xd503237f,0xd50323ff): e.reg_write(UC_ARM64_REG_PC,pc+4)
        elif op==0xd65f0fff: e.reg_write(UC_ARM64_REG_PC,e.reg_read(UC_ARM64_REG_LR))
    u.hook_add(UC_HOOK_CODE,hook)
    u.reg_write(UC_ARM64_REG_SP,STACK+0xf000); u.reg_write(UC_ARM64_REG_LR,STOP)
    u.reg_write(UC_ARM64_REG_X0,RENDERER); u.reg_write(UC_ARM64_REG_X1,UPDATE)
    mapping(0x18e036838,0x100); u.emu_start(0x18e036838,STOP,count=20000)
    assert events[-1]=='draw' and not locks
    finalized=bool(get(UPDATE+0x590)&(1<<28))
    want=ready or (patched and clipped)
    assert finalized==want
    assert events.count('clip')==int(patched and not ready)
    assert events.count('finalize')==int(patched and clipped and not ready)
    if patched and count and clipped and not ready:
        actual=[sets[get(ARRAY+i*8)] for i in range(count)]
        cumulative=set()
        for i,s in enumerate(expected): cumulative|=s; assert actual[i]==cumulative
    # Execute the actual ROI load/branch that led to the captured assertion.
    holder=MEM+0x19000; put(holder+8,UPDATE); u.reg_write(UC_ARM64_REG_X21,holder)
    mapping(0x18df6ce7c,32); reached=[]
    def guard(e,pc,size,unused):
        if pc in (0x18df6ce88,0x18df6dd94): reached.append(pc); e.emu_stop()
    u.hook_add(UC_HOOK_CODE,guard); u.emu_start(0x18df6ce7c,STOP,count=8)
    assert reached==[0x18df6ce88 if want else 0x18df6dd94]
    rows.append(dict(patched=patched,backdrops=count,existing_array=existing,
        will_clip=clipped,already_finalized=ready,events=events,
        original_roi_assertion_passed=want,shape_algebra_modeled=True))
report=dict(passed=True,actual_wrapper_clip_finalizer_and_roi_guard=True,
    model_boundaries=['allocator','locks','CA shape algebra/refcounts','PAC transport'],
    actual_pixel_rendering_tested=False,device_tested=False,cases=rows)
a.output.parent.mkdir(parents=True,exist_ok=True)
a.output.write_text(json.dumps(report,indent=2)+'\n')
print('8 original-instruction backdrop lifecycle cases passed; no device/pixel claim')
