#!/usr/bin/env python3
"""Check the actual linked native/final kernel's GPU startup call sites."""
import argparse,json,sys,hashlib
from pathlib import Path
from capstone import Cs,CS_ARCH_ARM64,CS_MODE_ARM
p=argparse.ArgumentParser();p.add_argument('--kernel',type=Path,required=True)
p.add_argument('--output',type=Path,required=True);a=p.parse_args()
s=Path(__file__).resolve().parents[3];sys.path.insert(0,str(s/'kits/XnuPrelink'))
from kc_image import Image
raw=a.kernel.read_bytes();outer=Image(raw)
im=Image(raw,outer.entries['com.apple.kernel'][1]) if outer.entries else outer
symbols=im.symbols();names={n:v for v,ns in symbols.items() for n in ns}
assert not any('startup_' in n and 'nabu_gpu' in n for n in names)
assert '_nabu_gpu_start' not in names
md=Cs(CS_ARCH_ARM64,CS_MODE_ARM)
def instructions(name):
    at=names[name];end=min(v for v in symbols if v>at)
    return list(md.disasm(im.read(at,end-at),at))
def branches(name):
    return [(i.address,int(i.op_str.lstrip('#'),16)&((1<<64)-1))
            for i in instructions(name) if i.mnemonic in ('b','bl') and i.op_str.startswith('#')]
def calls(source,target):return [hex(at) for at,to in branches(source) if to==names[target]]
late='_native_legacy_display_late_probe'
if late not in names:
    late=next(n for n in names if n.startswith('__Z') and 'native_legacy_display_late_probe' in n)
assert calls(late,'_nabu_gpu_start_after_iobsd')
assert calls('_nabu_gpu_start_after_iobsd','_native_adreno_a6xx_start')
workers=[n for n in names if 'hardwareWorker' in n or 'hardwareEntry' in n]
# Apple Clang folds hardwareWorker into hardwareEntry at production -O flags.
# Locate the actual gate's containing function instead of requiring a local name.
guarded=[n for n in workers if calls(n,'_native_adreno_wait_log_ready')]
assert len(guarded)==1,guarded
worker=guarded[0];gate=calls(worker,'_native_adreno_wait_log_ready');assert len(gate)==1
gate_address=int(gate[0],16)
allocations=[at for at,target in branches(worker) if any('IOMalloc' in n or n.startswith('__Znw') for n in symbols.get(target,[]))]
assert allocations and all(at>gate_address for at in allocations)
assert b'NabuGPU: registration refused before genuine IOBSD' in raw
assert b'NabuGPU: startup blocked before MMIO; verified log commit unavailable' in raw
result=dict(passed=True,kernel_sha256=hashlib.sha256(raw).hexdigest(),
    no_gpu_static_startup_record=True,late_hook_calls=calls(late,'_nabu_gpu_start_after_iobsd'),
    wrapper_calls=calls('_nabu_gpu_start_after_iobsd','_native_adreno_a6xx_start'),
    worker_function=worker,worker_persistence_call=gate,worker_allocations_after_persistence=[hex(at) for at in allocations],
    final_linked_machine_code_checked=True,hardware_tested=False)
a.output.write_text(json.dumps(result,indent=2)+'\n');print('PASS linked kernel: no GPU startup record; BSD late-hook call; persistence gate precedes A640 allocations')
