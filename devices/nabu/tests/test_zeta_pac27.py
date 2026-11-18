#!/usr/bin/env python3
"""Execute zeta's ordinary instructions and compare the production fallback.

C is compiled from the prepared kernel's actual headers/core. Unicorn executes
zeta's replacement words independently; all GPRs, SP, PC and NZCV are compared.
"""
import argparse
import ctypes
import importlib.util
import json
from pathlib import Path
import random
import shutil
import struct
import subprocess
import sys
import tempfile

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--xnu', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=True)
device = Path(__file__).resolve().parents[1]
vendor = device / 'scripts/zeta_pac'
sys.path.insert(0, str(vendor))
import zeta_userland_pauth as pauth
spec = importlib.util.spec_from_file_location('zeta_forms', vendor / 'tests/test_zeta_userland_pauth.py')
forms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(forms)
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
from unicorn.arm64_const import *

source = (a.xnu / 'osfmk/arm64/nabu_v82_emul.c').read_text()
core = source.split('BEGIN NABU_V82_CORE', 1)[1].split('END NABU_V82_CORE', 1)[0]
core = core.split('*/', 1)[1].rsplit('/*', 1)[0]
with tempfile.TemporaryDirectory(prefix='zeta-pac-test-') as td:
    root = Path(td)
    c = root / 'model.c'
    c.write_text('#include <stdint.h>\n#include <stdbool.h>\n#include <string.h>\n' + core + r'''
#include "nabu_nopac.h"
static uint64_t address, value;
static bool readable;
static bool read64(uint64_t at, uint64_t *out, void *unused) {
    (void)unused; if (!readable || at != address) return false; *out=value; return true;
}
int model(uint32_t op, uint64_t *regs, int user, uint64_t at, uint64_t data, int valid) {
    struct nabu_nopac_frame f; memcpy(f.x,regs,31*8);f.sp=regs[31];f.pc=regs[32];
    address=at;value=data;readable=valid;
    if ((op & NABU_V82_PACGA_MASK)==NABU_V82_PACGA_BASE) {
        uint64_t in[32],out;unsigned dst;memcpy(in,regs,31*8);in[31]=0;
        if(!nabu_v82_pac_core(op,in,&dst,&out))return 0;
        if(dst<31) { f.x[dst]=out; }
        f.pc+=4;
    } else if (!nabu_nopac_step(&f,op,user,read64,0))return 0;
    memcpy(regs,f.x,31*8);regs[31]=f.sp;regs[32]=f.pc;return 1;
}
''')
    cc = shutil.which('clang') or shutil.which('cc')
    subprocess.run([cc, '-shared', '-fPIC', '-O2', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-function', '-I', str(a.xnu / 'osfmk/arm64'), str(c), '-o', str(root / 'model.so')], check=True)
    lib = ctypes.CDLL(str(root / 'model.so'))
    lib.model.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint64), ctypes.c_int,
                          ctypes.c_uint64, ctypes.c_uint64, ctypes.c_int]
    ops = forms.assemble([s for s, _, _ in forms.CASES])
    cpu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    cpu.mem_map(0x100000, 0x10000)
    cpu.mem_map(0x200000, 0x10000)
    registers = [globals()[f'UC_ARM64_REG_X{i}'] for i in range(29)] + [UC_ARM64_REG_FP, UC_ARM64_REG_LR]
    rng = random.Random(0x27c5035)
    count, failed_reads = 0, 0
    for user in (0, 1):
        for op in ops:
            name, replacement = pauth.lower(op)
            for repeat in range(64):
                regs = [rng.getrandbits(64) for _ in range(31)] + [0x208000, 0x100000]
                rn = (op >> 5) & 31
                read_at, read_value = 0, rng.getrandbits(64)
                if name.startswith(('bra', 'blra')):
                    regs[rn] = 0x201000
                if name in ('retaa', 'retab'):
                    regs[30] = 0x201000
                if name in ('ldraa', 'ldrab'):
                    regs[rn] = 0x208000
                    imm = (((op >> 22) & 1) << 9) | ((op >> 12) & 511)
                    imm = (imm - 1024 if imm & 512 else imm) * 8
                    read_at = regs[rn] + imm
                    cpu.mem_write(read_at, struct.pack('<Q', read_value))
                expected = (ctypes.c_uint64 * 33)(*regs)
                assert lib.model(op, expected, user, read_at, read_value, 1) == 1, name
                cpu.mem_write(0x100000, struct.pack('<I', replacement))
                cpu.ctl_remove_cache(0x100000, 0x100004)
                for reg, value in zip(registers, regs):
                    cpu.reg_write(reg, value)
                cpu.reg_write(UC_ARM64_REG_SP, regs[31])
                flags = (repeat % 16) << 28
                cpu.reg_write(UC_ARM64_REG_NZCV, flags)
                cpu.emu_start(0x100000, 0, count=1)
                actual = [cpu.reg_read(r) for r in registers] + [cpu.reg_read(UC_ARM64_REG_SP), cpu.reg_read(UC_ARM64_REG_PC)]
                assert actual == list(expected), (name, user, repeat, actual, list(expected))
                assert cpu.reg_read(UC_ARM64_REG_NZCV) == flags
                count += 1
                if name in ('ldraa', 'ldrab'):
                    untouched = (ctypes.c_uint64 * 33)(*regs)
                    assert lib.model(op, untouched, user, read_at, read_value, 0) == 0
                    assert list(untouched) == regs
                    failed_reads += 1
    result = dict(passed=True, abi='zeta-ordinary-v1', cases=count, load_fault_cases=failed_reads,
                  all_gprs_sp_pc_nzcv_equal=True, pacga='constant-zero', hardware_tested=False)
    (a.output / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
