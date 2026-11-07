#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Package a Xiaomi Pad 5 (nabu / SM8150) kernel and ADT for RAM-only fastboot boot."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import struct

from native_devtree import configure_native_text_state, make_nabu_adt
from nabu_log_volume import make_config

ROOT = Path(__file__).resolve().parents[1]


def build_v0_bootimg(kernel_dtb: bytes, ramdisk: bytes, *,
                     pagesize: int = 4096, base: int = 0x00000000,
                     kernel_offset: int = 0x00008000, ramdisk_offset: int = 0x01000000,
                     tags_offset: int = 0x00000100, os_version: int = 0,
                     cmdline: str = '') -> bytes:
    """Construct a standard Android Boot Image v0 for SM8150 XBL/ABL `fastboot boot`."""
    if pagesize != 4096 or len(cmdline.encode('ascii')) > 511:
        raise ValueError('invalid Android v0 boot image page size or cmdline length')
    header = bytearray(pagesize)
    kernel_addr = (base + kernel_offset) & 0xffffffff
    ramdisk_addr = (base + ramdisk_offset) & 0xffffffff
    tags_addr = (base + tags_offset) & 0xffffffff
    struct.pack_into('<8s10I', header, 0, b'ANDROID!',
                     len(kernel_dtb), kernel_addr,
                     len(ramdisk), ramdisk_addr,
                     0, base & 0xffffffff,
                     tags_addr, pagesize, 0, os_version)
    cmd_bytes = cmdline.encode('ascii')
    header[64:64 + len(cmd_bytes)] = cmd_bytes
    digest = hashlib.sha1()
    for chunk in (kernel_dtb, ramdisk, b''):
        digest.update(chunk)
        digest.update(struct.pack('<I', len(chunk)))
    header[576:576 + 20] = digest.digest()
    pad = lambda blob: blob + bytes((-len(blob)) % pagesize)
    return bytes(header) + pad(kernel_dtb) + pad(ramdisk)


def build_v3_bootimg(kernel: bytes, ramdisk: bytes, *, pagesize: int = 4096,
                     os_version: int = 0, cmdline: str = '') -> bytes:
    """Construct an Android Boot Image v3 (header v3, no load addresses).

    nabu's ABL only hands off v3 headers: the same instruction loop under a v0
    header never executes (USB stays alive for minutes), while the v3 variant
    dies within seconds. v3 has no kernel/ramdisk/tags addresses, so ABL decides
    where each piece lands and the loader must discover the container itself.
    """
    if pagesize != 4096 or len(cmdline.encode('ascii')) > 1535:
        raise ValueError('invalid Android v3 boot image page size or cmdline length')
    header_size = 1580
    header = bytearray(header_size)
    struct.pack_into('<8s5I', header, 0, b'ANDROID!', len(kernel), len(ramdisk),
                     os_version, header_size, 0)
    struct.pack_into('<I', header, 40, 3)
    cmd_bytes = cmdline.encode('ascii')
    header[44:44 + len(cmd_bytes)] = cmd_bytes
    pad = lambda blob: blob + bytes((-len(blob)) % pagesize)
    return pad(bytes(header)) + pad(kernel) + pad(ramdisk)


def package_nabu_fastboot(kernel_path: Path, uefi_payload: bytes, dtb: bytes, out_dir: Path, *,
                          cpus: int = 8, owned_memory_size: int = 0x20000000,
                          enable_ufs_windows: bool = True,
                          log_token: bytes | None = None,
                          header_version: int = 0) -> dict:
    """Create a RAM-only `nabu-macos26-kernel.img` and `build.json` for `fastboot boot`."""
    if cpus not in (1, 2, 4, 8):
        raise ValueError('nabu CPU count must be 1, 2, 4, or 8')
    baseline = json.loads((ROOT / 'config/nabu-baseline.json').read_text())
    ufs = baseline['ufs']
    out_dir.mkdir(parents=True, exist_ok=False)
    adt_path = out_dir / 'kernel.macho.adt'
    make_nabu_adt(adt_path,
                  dram_base=int(baseline['memory_window']['dram_base'], 16),
                  dram_size=int(baseline['memory_window']['dram_size'], 16),
                  owned_memory_size=owned_memory_size,
                  os_environment='recoveryos',
                  panic_log=True,
                  native_gop_display=True)
    configure_native_text_state(adt_path, development_root=True, native_gop_display=True)
    args = ['-v', 'debug=0x14e', 'keepsyms=1', f'cpus={cpus}', 'armcpu-self-ipi=1']
    if enable_ufs_windows:
        args += [
            'native-ufs-root=1',
            f'native-ufs-os-lba={ufs["os_lba"]}',
            f'native-ufs-os-sectors={ufs["os_sectors_512"]}',
            f'native-ufs-systemos-lba={ufs["systemos_lba"]}',
            f'native-ufs-systemos-sectors={ufs["systemos_sectors_512"]}',
        ]
    if log_token is not None:
        make_config(log_token)
        args += ['nabu-log=1', f'nabu-log-token={log_token.hex()}']
    boot_args = ' '.join(args)
    menu = (
        'version = 1\n[boot]\ntimeout = 3\ndefault = "nabu-fastboot-ram"\n'
        '[kits]\nautoload = false\n[[entry]]\nid = "nabu-fastboot-ram"\n'
        'title = "NABU SM8150 - fastboot boot RAM experiment"\n'
        'type = "xnu"\npath = "Payloads/kernel.macho"\n'
        'adt = "Payloads/kernel.macho.adt"\n'
        f'args = "{boot_args}"\n'
    )
    (out_dir / 'unixnu.toml').write_text(menu, encoding='utf-8')
    kernel_bytes = kernel_path.read_bytes()
    adt_bytes = adt_path.read_bytes()
    ramdisk_payload = struct.pack('<4sII', b'NRAM', len(kernel_bytes), len(adt_bytes)) + kernel_bytes + adt_bytes + menu.encode('utf-8')
    kernel_dtb = gzip.compress(uefi_payload, mtime=0) + dtb
    # SM8150/ABL load addresses: the stage-1 loader and the NRAM container must
    # land inside DRAM bank 0, otherwise the Bank-0 NRAM scan cannot see the
    # ramdisk and the header points the loader below 0x80000000 (no RAM).
    if header_version == 3:
        image_bytes = build_v3_bootimg(kernel_dtb, ramdisk_payload)
        image_name = 'nabu-macos26-kernel-v3.img'
    else:
        image_bytes = build_v0_bootimg(kernel_dtb, ramdisk_payload, base=0,
                                       kernel_offset=0x80008000,
                                       ramdisk_offset=0x81000000,
                                       tags_offset=0x80000100)
        image_name = 'nabu-macos26-kernel.img'
    image_path = out_dir / image_name
    image_path.write_bytes(image_bytes)
    container_sha = hashlib.sha256(image_bytes).hexdigest()
    record = dict(
        board='nabu',
        soc='SM8150',
        image_name=image_name,
        header_version=header_version,
        temporary_ram_boot=True,
        partition_images_written=False,
        slot_changes=False,
        preferred_transport='fastboot boot',
        container_bytes=len(image_bytes),
        container_sha256=container_sha,
        kernel_sha256=hashlib.sha256(kernel_bytes).hexdigest(),
        adt_sha256=hashlib.sha256(adt_bytes).hexdigest(),
        boot_args=boot_args,
        dram_base=baseline['memory_window']['dram_base'],
        dram_size=baseline['memory_window']['dram_size'],
        owned_memory_bytes=owned_memory_size,
        gic_ipi='armcpu-self-ipi=1',
        virtual_timer_intid=19,
        hardware_executed=False,
    )
    (out_dir / 'build.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--uefi-fd', type=Path, required=True)
    parser.add_argument('--dtb', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--cpus', type=int, default=8, choices=(1, 2, 4, 8))
    args = parser.parse_args()
    record = package_nabu_fastboot(
        args.kernel, args.uefi_fd.read_bytes(), args.dtb.read_bytes(), args.out, cpus=args.cpus
    )
    print(json.dumps(record, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
