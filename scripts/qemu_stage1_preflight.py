#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the nabu stage-1 loader under QEMU virt before touching the tablet.

The fastboot path executes `platform/nabu/macos26/boot/nabu_darwinboot_fastboot.c`
in bare metal. This harness compiles a diagnostic build (NABU_QEMU_SERIAL mirrors
the on-screen console to the QEMU PL011), places the NRAM container where the
Bank-0 scan expects it, and replays the whole sequence:

    banner -> EL/MIDR/SCTLR -> FDT PA -> NRAM PA -> KERNEL BYTES -> HANDOFF

QEMU has no SM8150, so XNU itself will fault afterwards; the run is a loader
contract test, not a macOS boot.
"""
import argparse
import json
import re
import struct
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GCC = '/bin/aarch64-linux-gnu-gcc'
OBJCOPY = '/bin/aarch64-linux-gnu-objcopy'
QEMU = '/usr/bin/qemu-system-aarch64'
NRAM_PA = 0x81000000


def build_diagnostic_stage1(out: Path) -> Path:
    import sys
    sys.path.insert(0, str(ROOT / 'scripts'))
    from build_nabu_fastboot_image import DARWINBOOT_LDS
    lds = out / 'stage1-diag.lds'
    lds.write_text(DARWINBOOT_LDS)
    elf, binary = out / 'stage1-diag.elf', out / 'stage1-diag.bin'
    subprocess.run([GCC, '-march=armv8.2-a', '-ffreestanding', '-nostdlib', '-fno-builtin',
                    '-fno-stack-protector', '-fPIC', '-no-pie', '-O2', '-Wall', '-Wextra',
                    '-DNABU_QEMU_SERIAL', f'-I{ROOT}',
                    f'-I{ROOT / "platform/nabu/macos26/boot"}',
                    '-Wl,--build-id=none,--no-warn-rwx-segments', f'-T{lds}',
                    str(ROOT / 'platform/nabu/macos26/boot/nabu_darwinboot_entry.S'),
                    str(ROOT / 'platform/nabu/macos26/boot/nabu_darwinboot_fastboot.c'),
                    '-o', str(elf)], check=True)
    subprocess.run([OBJCOPY, '-O', 'binary', str(elf), str(binary)], check=True)
    raw = bytearray(binary.read_bytes())
    if raw[0x38:0x3C] != b'ARM\x64':
        raise SystemExit('diagnostic stage1 lost the ARM64 Image magic')
    struct.pack_into('<Q', raw, 0x10, 0x80000)
    binary.write_bytes(bytes(raw))
    return binary


def build_nram(image_dir: Path, out: Path) -> Path:
    kernel_path = image_dir / 'kernel.macho'
    if not kernel_path.exists():
        # package_nabu_fastboot keeps the kernel only inside the boot image.
        raw = (image_dir / 'nabu-macos26-kernel.img').read_bytes()
        page = struct.unpack_from('<I', raw, 36)[0]
        kernel_len, ramdisk_len = struct.unpack_from('<I', raw, 8)[0], struct.unpack_from('<I', raw, 16)[0]
        ram_off = page + ((kernel_len + page - 1) // page) * page
        ram = raw[ram_off:ram_off + ramdisk_len]
        _, nk, _ = struct.unpack_from('<4sII', ram, 0)
        kernel_path = out / 'kernel-from-image.macho'
        kernel_path.write_bytes(ram[12:12 + nk])
    kernel = kernel_path.read_bytes()
    adt = (image_dir / 'kernel.macho.adt').read_bytes()
    menu = (image_dir / 'unixnu.toml').read_bytes()
    nram = out / 'nram.bin'
    nram.write_bytes(struct.pack('<4sII', b'NRAM', len(kernel), len(adt)) + kernel + adt + menu)
    return nram


def run(stage1: Path, nram: Path, out: Path, seconds: int) -> dict:
    serial, trace = out / 'serial.log', out / 'interrupts.log'
    for path in (serial, trace):
        path.unlink(missing_ok=True)
    command = [QEMU, '-M', 'virt,gic-version=3', '-cpu', 'cortex-a710', '-m', '4G', '-smp', '1',
               '-nic', 'none', '-display', 'none', f'-serial', f'file:{serial}',
               '-kernel', str(stage1),
               '-device', f'loader,file={nram},addr={NRAM_PA:#x},force-raw=on',
               '-d', 'int,guest_errors', '-D', str(trace)]
    try:
        subprocess.run(command, timeout=seconds, check=False)
    except subprocess.TimeoutExpired:
        pass
    text = serial.read_bytes().replace(b'\0', b'').decode('utf-8', 'replace')
    # A label line is followed by its own line holding the 0x value.
    values = dict(re.findall(r'([A-Z][A-Z0-9 >\-:]+):\s*\n(0x[0-9a-f]+)', text))
    first = re.search(r'\.\.\.with ELR (0x[0-9a-f]+)', trace.read_text(errors='replace'))
    return dict(console=[line for line in text.splitlines() if line.strip()], values=values,
                first_exception_elr=first.group(1) if first else None,
                exceptions=trace.read_text(errors='replace').count('Taking exception'))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image-dir', type=Path, default=ROOT / 'out/nabu/image')
    parser.add_argument('--output', type=Path, default=ROOT / 'out/nabu/report/qemu-preflight')
    parser.add_argument('--seconds', type=int, default=15)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    stage1 = build_diagnostic_stage1(args.output)
    nram = build_nram(args.image_dir, args.output)
    result = run(stage1, nram, args.output, args.seconds)
    (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    for line in result['console']:
        print('  ' + line)
    print('  first exception ELR: %s (%d exceptions)'
          % (result['first_exception_elr'], result['exceptions']))
    def value(label: str) -> int | None:
        raw = result['values'].get(label)
        return int(raw, 16) if raw else None
    passed = (value('NRAM PA') == NRAM_PA
              and value('KERNEL BYTES') is not None
              and value('HANDOFF -> ENTRY PA') == 0xc059b488)
    print('  stage1 contract: %s' % ('PASS' if passed else 'FAIL'))
    raise SystemExit(0 if passed else 1)


if __name__ == '__main__':
    main()
