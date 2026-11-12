# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile and execute the actual NABU cold-entry instruction stream."""
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from native_macho import macho, symbols


class NabuSecondaryEntry(unittest.TestCase):
    def patched_sources(self, work):
        for name in ('start.s', 'cpu.c'):
            target = work / 'osfmk/arm64' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / 'sources/macos26/xnu/osfmk/arm64' / name, target)
        subprocess.run(['git', 'apply', '--unsafe-paths', '--directory=' + str(work),
                        str(ROOT / 'platform/nabu/macos26/patches/0006-nabu-secondary-cold-entry.patch')],
                       check=True, capture_output=True)

    def test_physical_el1_and_el2_entries_use_no_stack_and_reach_el1_reset(self):
        try:
            from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_INSN
            from unicorn import arm64_const as arm
        except ImportError:
            self.skipTest('requires Unicorn for EL1/EL2 instruction execution')
        compiler = os.environ.get('SHENG_TEST_CLANG') or shutil.which('clang')
        linker = shutil.which('ld64.lld')
        if compiler and not linker:
            candidate = Path(compiler).parent / 'ld64.lld'
            if candidate.is_file():
                linker = str(candidate)
        if not compiler or not linker:
            self.skipTest('requires AArch64-capable Clang and ld64.lld')
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            self.patched_sources(work)
            source = (work / 'osfmk/arm64/start.s').read_text()
            start = source.index('#if defined(NABU)\n/* PSCI cold entry')
            end = source.index('#endif /* NABU */', start)
            code = source[start:end].split('\n', 1)[1]
            # MOV64 is the existing XNU macro. Exercise its instruction result
            # with an explicit reset SCTLR value; the real kernel build expands
            # the target's SCTLR_EL1_DEFAULT expression through proc_reg.h.
            code = code.replace('MOV64\tx9, (SCTLR_EL1_DEFAULT & ~(SCTLR_M_ENABLED | SCTLR_C_ENABLED | SCTLR_I_ENABLED))',
                                'movz\tx9, #0x0818\n\tmovk\tx9, #0x30d0, lsl #16')
            assembly = work / 'entry.S'
            assembly.write_text('#define EXT(x) _##x\n#define LEXT(x) _##x:\n.text\n.p2align 12\n' + code + '''
.globl _reset_vector
_reset_vector:
 b .
.data
.p2align 6
.globl _nabu_secondary_boot_state
_nabu_secondary_boot_state:
.space 512
''')
            output = work / 'entry.macho'
            subprocess.run([compiler, '--target=arm64-apple-macos26.5', '-c', str(assembly),
                            '-o', str(work / 'entry.o')], check=True, capture_output=True)
            subprocess.run([linker, '-arch', 'arm64', '-platform_version', 'macos', '26.5', '26.5',
                            '-e', '_nabu_secondary_entry', '-o', str(output), str(work / 'entry.o')],
                           check=True, capture_output=True)
            raw = output.read_bytes()
            image, names = macho(raw), symbols(output)
            self.assertEqual(names['_nabu_secondary_entry'] & 0xfff, 0)
            self.assertEqual(names['_nabu_secondary_vectors'] & 0x7ff, 0)
            self.assertGreater(names['_nabu_secondary_entry_end'], names['_nabu_secondary_vectors'] + 2048)

            for entry_el in (1, 2):
                for cpu in (1, 7):
                    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
                    for segment in image['segments']:
                        if segment['filesize']:
                            uc.mem_map(segment['va'], (segment['size'] + 4095) & ~4095)
                            uc.mem_write(segment['va'], raw[segment['fileoff']:segment['fileoff'] + segment['filesize']])
                    # Model the non-secure PSCI world; no stack mapping exists.
                    uc.cpr_write(3, 6, 1, 1, 0, 0x501)  # SCR_EL3: NS, HCE, RW
                    uc.reg_write(arm.UC_ARM64_REG_PSTATE, 0x3c0 | (entry_el << 2) | 1)
                    uc.cpr_write(3, 0, 0, 0, 5, 0x80000000 | (cpu << 8))
                    writes = []
                    def stop(engine, address, size, unused):
                        if address == names['_reset_vector']:
                            engine.emu_stop()
                    def system_register(engine, register, cp, unused):
                        if (cp.op0, cp.op1, cp.crn, cp.crm, cp.op2) == (3, 0, 0, 0, 5):
                            engine.reg_write(register, 0x80000000 | (cpu << 8))
                        elif (cp.op0, cp.op1, cp.crn, cp.crm, cp.op2) == (3, 4, 12, 9, 5):
                            # Unicorn has no GIC. Supply SRE readback and capture
                            # its write while all other system instructions run.
                            writes.append(cp.val)
                            if cp.val in (0, 3):
                                engine.reg_write(register, 0)
                        else:
                            return 0
                        engine.reg_write(arm.UC_ARM64_REG_PC, engine.reg_read(arm.UC_ARM64_REG_PC) + 4)
                        return 1
                    uc.hook_add(UC_HOOK_CODE, stop)
                    uc.hook_add(UC_HOOK_INSN, system_register, None, 1, 0, arm.UC_ARM64_INS_MRS)
                    uc.hook_add(UC_HOOK_INSN, system_register, None, 1, 0, arm.UC_ARM64_INS_MSR)
                    uc.emu_start(names['_nabu_secondary_entry'], names['_nabu_secondary_entry_end'] + 16, count=300)
                    self.assertEqual(uc.reg_read(arm.UC_ARM64_REG_PC), names['_reset_vector'])
                    self.assertEqual(uc.reg_read(arm.UC_ARM64_REG_PSTATE) & 15, 5, (entry_el, cpu, hex(uc.cpr_read(3, 4, 1, 1, 0)), writes))  # EL1h
                    state = struct.unpack('<8Q', uc.mem_read(names['_nabu_secondary_boot_state'] + cpu * 64, 64))
                    self.assertEqual(state[:3], (2, entry_el * 4, 0x80000000 | cpu << 8))
                    self.assertEqual(uc.reg_read(arm.UC_ARM64_REG_SP), 0)
                    self.assertEqual(bool(writes), entry_el == 2)
                    if entry_el == 2:
                        self.assertEqual(uc.cpr_read(3, 4, 1, 1, 0), 1 << 31)  # HCR_EL2
                        self.assertEqual(uc.cpr_read(3, 0, 1, 0, 0) & 0x1005, 0)  # SCTLR_EL1 M/C/I

    def test_translation_publish_walk_cleans_tables_not_blocks_or_leaf_data(self):
        compiler = shutil.which('g++')
        if not compiler:
            self.skipTest('requires host C++ compiler')
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            self.patched_sources(work)
            source = (work / 'osfmk/arm64/cpu.c').read_text()
            start = source.index('static void\nnabu_publish_cpu_translation_tables(')
            helper = source[start:source.index('#endif /* NABU */', start)]
            harness = work / 'tables.cpp'
            harness.write_text('''#include <cassert>
#include <cstdint>
#include <vector>
using tt_entry_t=uint64_t;
using pmap_paddr_t=uint64_t;
using vm_offset_t=uintptr_t;
#define TTE_PGENTRIES 512
#define ARM_PGBYTES 4096
#define ARM_TTE_VALID 1ULL
#define ARM_TTE_TYPE_MASK 2ULL
#define ARM_TTE_TYPE_TABLE 2ULL
#define ARM_TTE_TABLE_MASK 0x0000fffffffff000ULL
alignas(4096) tt_entry_t pages[3][512];
std::vector<unsigned> cleaned;
uintptr_t phystokv(uint64_t address) {
 assert(address>=0x1000 && address<=0x3000);
 return reinterpret_cast<uintptr_t>(pages[address/4096-1]);
}
void CleanPoC_DcacheRegion_Force(uintptr_t address,unsigned size) {
 assert(size==4096);
 cleaned.push_back((address-reinterpret_cast<uintptr_t>(pages))/4096);
}
''' + helper + '''
int main() {
 pages[0][0]=0x2003; // L1 -> L2
 pages[0][1]=0x80000001; // L1 block, must not dereference data
 pages[1][7]=0x3003; // L2 -> L3
 pages[1][8]=0x90000001; // L2 block
 pages[2][0]=0xcafe0003; // L3 leaf, type 3 is not another table
 nabu_publish_cpu_translation_tables(0x1000,1);
 assert((cleaned==std::vector<unsigned>{2,1,0}));
}
''')
            subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror', str(harness),
                            '-o', str(work / 'tables')], check=True, capture_output=True)
            subprocess.run([str(work / 'tables')], check=True)


if __name__ == '__main__':
    unittest.main()
