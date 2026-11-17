#!/usr/bin/env python3
"""Compare exact cache blocks with NABU's production PAC emulator semantics.

The original instruction blocks run with only trapping PAC operations delegated
to the production C helpers. The replacement blocks run as native AArch64 in
Unicorn. This checks semantics, not device performance or desktop display.
"""
import argparse,ctypes,hashlib,json,random,shutil,struct,subprocess,tempfile
from pathlib import Path
from unicorn import Uc,UC_ARCH_ARM64,UC_MODE_ARM,UC_HOOK_CODE
from unicorn.arm64_const import *

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('cache',type=Path)
p.add_argument('--xnu',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args();device=Path(__file__).resolve().parents[1]
fixture=json.loads((device/'compat/cpu-display/nopac-hotpath-26C5035r.json').read_text())
emulator=a.xnu/'osfmk/arm64/nabu_v82_emul.c'
header=a.xnu/'osfmk/arm64/nabu_nopac.h'
text=emulator.read_text()
core=text.split('BEGIN NABU_V82_CORE',1)[1].split('END NABU_V82_CORE',1)[0]
core=core.split('*/',1)[1].rsplit('/*',1)[0]
with tempfile.TemporaryDirectory(prefix='nabu-hotpath-') as td:
    source=Path(td)/'model.c';library=Path(td)/'model.so'
    source.write_text('#include <stdint.h>\n#include <stdbool.h>\n'+core+'\n'+
        '#include "nabu_nopac.h"\n'+r'''
int model(uint32_t op,uint64_t *regs) {
    unsigned dst; uint64_t result;
    if ((op & NABU_V82_PACGA_MASK)==NABU_V82_PACGA_BASE) {
        uint64_t gpr[32];for(unsigned i=0;i<31;i++)gpr[i]=regs[i];gpr[31]=0;
        if(!nabu_v82_pac_core(op,gpr,&dst,&result))return 0;
        if(dst<31)regs[dst]=result;
        return 1;
    }
    struct nabu_nopac_frame f={0};
    for(unsigned i=0;i<31;i++)f.x[i]=regs[i];
    if(!nabu_nopac_step(&f,op,true,0,0))return 0;
    for(unsigned i=0;i<31;i++)regs[i]=f.x[i];
    return 1;
}
''')
    compiler=next((shutil.which(c) for c in ('clang','clang-19','clang-18','clang-17','cc') if shutil.which(c)),None)
    assert compiler,'C compiler required'
    subprocess.run([compiler,'-shared','-fPIC','-O2','-Wall','-Wextra','-Werror',
        '-Wno-unused-function','-I',str(header.parent),str(source),'-o',str(library)],check=True)
    lib=ctypes.CDLL(str(library));lib.model.argtypes=[ctypes.c_uint32,ctypes.POINTER(ctypes.c_uint64)]
    registers=[globals()[f'UC_ARM64_REG_X{i}'] for i in range(29)]+[UC_ARM64_REG_FP,UC_ARM64_REG_LR]
    cpu=Uc(UC_ARCH_ARM64,UC_MODE_ARM);cpu.ctl_set_cpu_model(UC_CPU_ARM64_MAX)
    at=0x100000;cpu.mem_map(at,4096)
    emulate=[False];traps=[0]
    def hook(emu,pc,size,unused):
        if not emulate[0]:return
        word=struct.unpack('<I',emu.mem_read(pc,4))[0]
        if word&0xffff8000==0xdac10000 or word&0xffe0fc00==0x9ac03000:
            values=(ctypes.c_uint64*31)(*(emu.reg_read(r) for r in registers))
            assert lib.model(word,values)==1
            for r,v in zip(registers,values):emu.reg_write(r,v)
            emu.reg_write(UC_ARM64_REG_PC,pc+4);traps[0]+=1
    cpu.hook_add(UC_HOOK_CODE,hook)
    rng=random.Random(0x26c5035);cases=[]
    for tag in range(256):
        vals=[rng.getrandbits(64) for _ in registers]
        vals[16]=(tag<<56)|0x00cd0007d4c00ce4;cases.append(vals)
    for bit in range(64):
        cases.append([1<<bit]*31);cases.append([((1<<64)-1)^(1<<bit)]*31)
    for _ in range(512):cases.append([rng.getrandbits(64) for _ in registers])
    rows=[]
    for entry in fixture['patches']:
        with a.cache.with_name(entry['file']).open('rb') as f:
            off=int(entry['offset'],16);f.seek(off);original=f.read(len(bytes.fromhex(entry['original'])))
        assert original==bytes.fromhex(entry['original'])
        replacement=bytes.fromhex(entry['replacement']);assert len(original)==len(replacement)
        counts=[]
        for i,values in enumerate(cases):
            results=[];flag=(i%16)<<28
            for active,code in [(True,original),(False,replacement)]:
                emulate[0]=active;traps[0]=0;cpu.mem_write(at,code)
                cpu.ctl_remove_cache(at,at+len(code))
                for reg,value in zip(registers,values):cpu.reg_write(reg,value)
                cpu.reg_write(UC_ARM64_REG_SP,0x70000000);cpu.reg_write(UC_ARM64_REG_NZCV,flag)
                cpu.emu_start(at,at+len(code),count=20)
                results.append(([cpu.reg_read(reg) for reg in registers],cpu.reg_read(UC_ARM64_REG_SP),cpu.reg_read(UC_ARM64_REG_NZCV),cpu.reg_read(UC_ARM64_REG_PC)))
                counts.append(traps[0])
            assert results[0]==results[1],(entry['name'],i)
            assert counts[-2:]==[2,0],entry['name']
        rows.append(dict(name=entry['name'],cases=len(cases),original_traps_per_block=2,replacement_traps_per_block=0,all_registers_SP_NZCV_equal=True))
report=dict(passed=True,cases=sum(r['cases'] for r in rows),blocks=rows,
    production_emulator_sha256=hashlib.sha256(emulator.read_bytes()).hexdigest(),
    production_nopac_header_sha256=hashlib.sha256(header.read_bytes()).hexdigest(),
    memory_accesses_or_branches_modified=False,device_tested=False,measured_speedup=None)
a.output.write_text(json.dumps(report,indent=2)+'\n')
print('NABU PAC native lowering:',report['cases'],'register/flags equivalence cases passed')
