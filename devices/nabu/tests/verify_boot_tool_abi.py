#!/usr/bin/env python3
"""Run the linked kernel's Mach-O grading function on actual owner tool headers."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'kits/XnuPrelink'))
from kc_image import Image
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
                                 UC_ARM64_REG_X3, UC_ARM64_REG_X30, UC_ARM64_REG_SP,
                                 UC_ARM64_REG_PC)


def verify(kernel, tools, expected):
    raw = kernel.read_bytes()
    im = Image(raw)
    if 'com.apple.kernel' in im.entries:
        im = Image(raw, im.entries['com.apple.kernel'][1])
    names = {n: a for a, ns in im.symbols().items() for n in ns}
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    lo = min(s['va'] for s in im.segments if s['size']) & ~4095
    hi = (max(s['va'] + s['size'] for s in im.segments if s['size']) + 4095) & ~4095
    uc.mem_map(lo, hi - lo)
    for s in im.segments:
        if s['filesize']:
            uc.mem_write(s['va'], raw[s['fileoff']:s['fileoff'] + s['filesize']])
    uc.mem_map(0x100000, 0x20000)

    def cpu(emu, pc, size, unused):
        if pc == names['_cpu_subtype']:
            # Board fixture: SM8150 reports ARM64_V8 (1). The grading
            # instructions themselves are the unmodified production image.
            emu.reg_write(UC_ARM64_REG_X0, 1)
            emu.reg_write(UC_ARM64_REG_PC, emu.reg_read(UC_ARM64_REG_X30))

    uc.hook_add(UC_HOOK_CODE, cpu)
    records = []
    for tool in tools:
        data = tool.read_bytes()
        magic, cpu_type, subtype = struct.unpack_from('<3I', data)
        assert magic == 0xfeedfacf, 'owner tool must be a thin Mach-O'
        for reg, value in ((UC_ARM64_REG_X0, cpu_type), (UC_ARM64_REG_X1, subtype & 0xffffff),
                           (UC_ARM64_REG_X2, subtype & 0xff000000), (UC_ARM64_REG_X3, 1),
                           (UC_ARM64_REG_X30, 0x100000), (UC_ARM64_REG_SP, 0x11f000)):
            uc.reg_write(reg, value)
        uc.emu_start(names['_ml_grade_binary'], 0x100000, count=1000)
        grade = uc.reg_read(UC_ARM64_REG_X0)
        assert (grade > 0) == (expected == 'accepted'), (str(tool), hex(subtype), grade)
        records.append(dict(path=str(tool), sha256=hashlib.sha256(data).hexdigest(),
                            cpu_type=hex(cpu_type), cpu_subtype=hex(subtype), grade=grade,
                            kernel_exec_architecture_accepted=grade > 0))
    return dict(passed=True, kernel=str(kernel), kernel_sha256=hashlib.sha256(raw).hexdigest(),
                grade_function=hex(names['_ml_grade_binary']), cpu_subtype_fixture=1,
                actual_kernel_instructions_executed=True, expected=expected, tools=records,
                full_exec_syscall_emulated=False, device_tested=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--tool', type=Path, action='append', required=True)
    parser.add_argument('--expected', choices=('accepted', 'rejected'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.kernel, args.tool, args.expected)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
