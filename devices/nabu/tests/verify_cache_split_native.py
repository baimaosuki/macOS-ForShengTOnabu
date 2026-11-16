#!/usr/bin/env python3
"""Execute the linked ARM64 split-page routine in Unicorn, without editing it."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'kits/XnuPrelink'))
from kc_image import Image, scan
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3, UC_ARM64_REG_X4, UC_ARM64_REG_X5, UC_ARM64_REG_X6, UC_ARM64_REG_X7, UC_ARM64_REG_X30, UC_ARM64_REG_SP


def verify(native):
    raw = native.read_bytes()
    im = Image(raw)
    symbols = {n: a for a, names in im.symbols().items() for n in names}
    entry = symbols['_vm_shared_region_slide_part_v5']
    starts = im.function_starts()
    end = min(a for a in starts if a > entry)
    pager = symbols['_shared_region_pager_data_request']
    pager_end = min(a for a in starts if a > pager)
    calls = [pc for pc, target, _ in scan(im.read(pager, pager_end-pager), pager)[0] if target == entry]
    assert len(calls) >= 2, 'the real pager must call the tested routine for peek and apply'
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    low = min(s['va'] for s in im.segments if s['size']) & ~4095
    high = (max(s['va']+s['size'] for s in im.segments if s['size'])+4095) & ~4095
    uc.mem_map(low, high-low)
    for s in im.segments:
        if s['filesize']:
            uc.mem_write(s['va'], raw[s['fileoff']:s['fileoff']+s['filesize']])
    # These are set by VM bootstrap on the real 4 KiB target, not file initializers.
    uc.mem_write(symbols['_PAGE_SHIFT_CONST'], struct.pack('<I', 12))
    for n, v in [('_PAGE_SIZE_CONST', 4096), ('_PAGE_MASK_CONST', 4095)]:
        if n in symbols:
            uc.mem_write(symbols[n], struct.pack('<Q', v))
    uc.mem_map(0x100000, 0x20000)
    info, slide_info, data, next_address, stop, stack = (0x100000, 0x101000, 0x102000, 0x103000, 0x104000, 0x110000)
    # vm_shared_region_slide_info_t: no hardware ptrauth, si_slide offset 0,
    # si_slide_info_entry offset 0x30 (public prepared vm_shared_region_xnu.h).
    uc.mem_write(info, struct.pack('<I', 0x4000)+bytes(0x2c)+struct.pack('<Q', slide_info))
    uc.mem_write(slide_info, struct.pack('<4IQH', 5, 16384, 1, 0, 0x180000000, 0))
    registers = [UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3,
                 UC_ARM64_REG_X4, UC_ARM64_REG_X5, UC_ARM64_REG_X6, UC_ARM64_REG_X7]

    def run(word, apply=True, part=0, start=0):
        uc.mem_write(data, struct.pack('<Q', word)+bytes(4088))
        uc.mem_write(next_address, bytes(4))
        for register, value in zip(registers, (info, data, 0x1e204c000+part*4096, part, start, apply, next_address, 0)):
            uc.reg_write(register, int(value))
        uc.reg_write(UC_ARM64_REG_X30, stop)
        uc.reg_write(UC_ARM64_REG_SP, stack)
        uc.emu_start(entry, stop, count=10000)
        assert uc.reg_read(UC_ARM64_REG_X0) == 0
        return struct.unpack('<Q', uc.mem_read(data, 8))[0], struct.unpack('<I', uc.mem_read(next_address, 4))[0]

    cases = []
    # The first real cache mismatch, plus both auth key bits and the plain high8.
    for word, expected in [(0x80140000349cedd8, 0x1b49d2dd8),
                           ((1<<63)|(1<<51)|0x1234, 0x180005234),
                           ((1<<63)|(0x5432<<34)|0x2345, 0x180006345),
                           ((0xab<<34)|0x3456, 0xab00000180007456)]:
        for part in range(4):
            got, next_value = run(word, part=part, start=part*4096)
            assert got == expected, f'part {part}: encoded {word:#x} became {got:#x}, expected {expected:#x}'
            assert next_value == 0xffff
            unchanged, _ = run(word, False, part, part*4096)
            assert unchanged == word, 'chain-only peek modified its source'
        cases.append(dict(encoded=hex(word), decoded=hex(expected), parts=4, peek_unchanged=True))
    return dict(passed=True, native_sha256=hashlib.sha256(raw).hexdigest(),
                function=hex(entry), code_sha256=hashlib.sha256(im.read(entry,end-entry)).hexdigest(),
                actual_pager_calls=[hex(a) for a in calls], instruction_replay=cases,
                target_page_size=4096, hardware_tested=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('native', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = verify(args.native)
    if args.output:
        args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))
