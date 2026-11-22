#!/usr/bin/env python3
"""Replay the original 26C5035r ERST allocator, not our convenience override."""
import argparse
import bisect
import hashlib
import json
from pathlib import Path
import struct
import sys

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_UNMAPPED
from unicorn import arm64_const as arm

SOURCE = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SOURCE / 'kits/XnuPrelink'))
from kc_image import Image


def verify(path):
    raw = path.read_bytes()
    fixture = json.loads((SOURCE / 'devices/nabu/scripts/usb-xhci-26C5035r.json').read_text())
    assert hashlib.sha256(raw).hexdigest() == fixture['kernelcache_sha256']
    outer = Image(raw)
    images = [outer] + [Image(raw, p[1]) for p in outer.entries.values()]
    symbols = {a: ns for im in images for a, ns in im.symbols().items()}
    names = {n: a for a, ns in symbols.items() for n in ns}
    fixups = outer.fixups()
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)

    def read(va, size):
        for im in images:
            try:
                return im.read(va, size)
            except ValueError:
                pass
        raise ValueError(hex(va))

    method = '__ZN23AppleUSBXHCIInterrupter18initWithParametersEP12AppleUSBXHCIjP8IOMapper'
    target_name = '__ZN37AppleUSBHostDMABufferMemoryDescriptor17inTaskWithOptionsEP4taskjmmjP8IOMapper'
    entry = names[method]
    owner = Image(raw, outer.entries['com.apple.driver.usb.AppleUSBXHCI'][1])
    starts = sorted(owner.symbols())
    end = starts[bisect.bisect_right(starts, entry)]

    def resolve_stub(at):
        if at in symbols:
            return symbols[at][0]
        regs = {}
        for ins in md.disasm(read(at, 24), at):
            p = ins.op_str.replace(',', '').replace('[', '').replace(']', '').split()
            if ins.mnemonic == 'adrp':
                regs[p[0]] = int(p[1].lstrip('#'), 0)
            elif ins.mnemonic == 'add' and p[1] in regs:
                regs[p[0]] = regs[p[1]] + int(p[2].lstrip('#'), 0)
            elif ins.mnemonic == 'ldr' and p[1] in regs:
                site = regs[p[1]] + (int(p[2].lstrip('#'), 0) if len(p) > 2 else 0)
                if site in fixups:
                    return symbols.get(fixups[site][0], ['?'])[0]
        return '?'

    results = []
    for options in (0x10003, 0x10403):
        uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        uc.ctl_set_cpu_model(arm.UC_CPU_ARM64_MAX)
        pages = set()

        def map_pages(at, size):
            for page in range(at & ~4095, (at + size + 4095) & ~4095, 4096):
                if page not in pages:
                    uc.mem_map(page, 4096)
                    pages.add(page)

        map_pages(entry, end - entry)
        uc.mem_write(entry, read(entry, end - entry))
        obj, table, controller, stack, stop = 0x30000000, 0x30010000, 0x30020000, 0x30100000, 0x30200000
        for at in (obj, table, controller, stack, stop):
            map_pages(at, 65536)
        for at in (obj, controller):
            uc.mem_write(at, struct.pack('<Q', table))
        uc.mem_write(controller + 0x5ac, struct.pack('<I', options))
        for slot in range(427):
            uc.mem_write(table + slot * 8, struct.pack('<Q', 0x40000000 + slot * 4))
        for reg, value in [('X0', obj), ('X1', controller), ('X2', 0), ('X3', 0), ('SP', stack + 0xf000), ('LR', stop)]:
            uc.reg_write(getattr(arm, 'UC_ARM64_REG_' + reg), value)
        allocation = []

        def missing(u, access, address, size, value, unused):
            map_pages(address, size)
            page = address & ~4095
            try:
                u.mem_write(page, read(page, 4096))
            except ValueError:
                pass
            for site, fix in fixups.items():
                if page <= site < page + 4096:
                    u.mem_write(site, struct.pack('<Q', fix[0]))
            return True

        def instruction(u, pc, size, unused):
            if not entry <= pc < end:
                u.reg_write(arm.UC_ARM64_REG_X0, 0)
                u.reg_write(arm.UC_ARM64_REG_PC, u.reg_read(arm.UC_ARM64_REG_LR))
                return
            ins = next(md.disasm(bytes(u.mem_read(pc, 4)), pc))
            m = ins.mnemonic
            if m in ('pacibsp', 'autibsp', 'paciasp', 'autiasp', 'bti'):
                u.reg_write(arm.UC_ARM64_REG_PC, pc + 4)
            elif m in ('retab', 'retaa'):
                u.reg_write(arm.UC_ARM64_REG_PC, u.reg_read(arm.UC_ARM64_REG_LR))
            elif m.startswith('bl') or m in ('br', 'braa', 'brab', 'braaz', 'brabz'):
                if m == 'bl':
                    name = resolve_stub(int(ins.op_str.lstrip('#'), 0))
                else:
                    reg = ins.op_str.split(',')[0].upper()
                    target = u.reg_read(getattr(arm, 'UC_ARM64_REG_' + reg))
                    name = symbols.get(target, ['virtual:' + str((target - 0x40000000) // 4)])[0]
                if name == target_name:
                    allocation.append([u.reg_read(getattr(arm, 'UC_ARM64_REG_X' + str(i))) for i in range(6)])
                    u.emu_stop()
                    return
                result = 1 if '4initEv' in name or name == 'virtual:4' else 0
                u.reg_write(arm.UC_ARM64_REG_X0, result)
                u.reg_write(arm.UC_ARM64_REG_PC, pc + 4 if m.startswith('bl') else u.reg_read(arm.UC_ARM64_REG_LR))

        uc.hook_add(UC_HOOK_MEM_UNMAPPED, missing)
        uc.hook_add(UC_HOOK_CODE, instruction)
        uc.emu_start(entry, stop, count=10000)
        assert len(allocation) == 1
        args = allocation[0]
        assert args[1:4] == [options, 16, 64]
        results.append(dict(controller_dma_options=hex(options),real_factory_options=hex(args[1]),bytes=args[2],alignment=args[3]))
    return dict(passed=True,build='26C5035r',original_kernelcache_sha256=fixture['kernelcache_sha256'],
                actual_original_arm64_executed=True,dependencies_synthetic=True,hardware_tested=False,
                caller=method,factory=target_name,cases=results)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--kernelcache', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    args = p.parse_args()
    result = verify(args.kernelcache)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print('PASS original 26C5035r ERST allocation: native field0x5ac reaches real DMA factory, size/alignment unchanged')
