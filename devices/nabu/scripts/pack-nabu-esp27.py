#!/usr/bin/env python3
"""Package a freshly assembled EFI directory without an older ESP ZIP.

Verifies the prelink/runtime pairing, menu, PE architecture, a real FAT
write/readback, final file hashes and ZIP CRC. This is host packaging evidence;
it does not replace binary/ABI checks or a tablet boot test.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import tomllib
import zipfile
from nabu_root27 import sha
from nabu_boot_config27 import validate


def inventory(root):
    files = {}
    for path in sorted((root / 'EFI').rglob('*')):
        if path.is_symlink():
            raise ValueError('EFI must contain regular files, not symlinks')
        if path.is_file():
            files[path.relative_to(root).as_posix()] = dict(bytes=path.stat().st_size, sha256=sha(path))
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--esp', type=Path, required=True)
    parser.add_argument('--prelink-report', type=Path, required=True)
    parser.add_argument('--payload-report', type=Path, required=True)
    parser.add_argument('--root-report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    runtime = json.loads(args.root_report.read_text())
    prelink = json.loads(args.prelink_report.read_text())
    payload = json.loads(args.payload_report.read_text())
    if not runtime.get('complete') or runtime.get('abi') != 'zeta-ordinary-v1':
        raise ValueError('Incomplete ROOT report')
    if prelink.get('missing') or prelink.get('stubbed'):
        raise ValueError('Prelink contains unresolved/stubbed symbols')
    bound = prelink.get('zeta_runtime', {})
    if bound.get('root_image_sha256') != runtime['root_image_sha256'] or bound.get('abi') != runtime['abi']:
        raise ValueError('Kernel was prelinked for a different ROOT')
    if sha(Path(runtime['root_image'])) != runtime['root_image_sha256']:
        raise ValueError('Actual ROOT changed after conversion')
    files = inventory(args.esp)
    actual_kernel = files.get('EFI/UniXNU/Payloads/kernel.macho', {}).get('sha256')
    if (not prelink.get('candidate_sha256') or payload.get('kernel_sha256') != prelink['candidate_sha256']
            or payload.get('payload_kernel_sha256') != actual_kernel):
        raise ValueError('Actual payload kernel is not the recorded prelink/entry-probe output')
    required = ['EFI/BOOT/BOOTAA64.EFI', 'EFI/UniXNU/UniXNU.efi', 'EFI/UniXNU/unixnu.toml']
    required += ['EFI/UniXNU/Payloads/' + name for name in (
        'kernel.macho', 'kernel.macho.adt', 'kernel.macho.trustcache',
        'kernel.macho.gpuaddon', 'kernel.macho.vroot', 'scratch-data.img')]
    if set(required) - files.keys():
        raise ValueError('Incomplete EFI tree: ' + ', '.join(sorted(set(required) - files.keys())))
    menu = validate((args.esp / 'EFI/UniXNU/unixnu.toml').read_bytes())
    for name in ('EFI/BOOT/BOOTAA64.EFI', 'EFI/UniXNU/UniXNU.efi'):
        data = (args.esp / name).read_bytes()
        if data[:2] != b'MZ' or len(data) < 64:
            raise ValueError('Not a PE menu binary')
        at = struct.unpack_from('<I', data, 60)[0]
        if data[at:at + 4] != b'PE\0\0' or struct.unpack_from('<H', data, at + 4)[0] != 0xaa64:
            raise ValueError('Menu must be AARCH64 PE')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='nabu-esp-', dir=args.output.parent) as td:
        work = Path(td)
        fat = work / 'esp.fat'
        capacity = max(256 << 20, ((sum(v['bytes'] for v in files.values()) + (64 << 20)) >> 20) << 20)
        with fat.open('xb') as stream:
            stream.truncate(capacity)
        subprocess.run(['mkfs.vfat', '-F', '32', str(fat)], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(['mcopy', '-s', '-i', str(fat), str(args.esp / 'EFI'), '::/'], check=True)
        readback = work / 'readback'
        readback.mkdir()
        subprocess.run(['mcopy', '-s', '-i', str(fat), '::/EFI', str(readback)], check=True)
        if inventory(readback) != files:
            raise ValueError('FAT write/readback mismatch')
        verification = dict(passed=True, efi_files=files, fat_readback=dict(passed=True),
                            root_image_sha256=runtime['root_image_sha256'],
                            root_report_sha256=sha(args.root_report), prelink_report_sha256=sha(args.prelink_report),
                            payload_report_sha256=sha(args.payload_report),
                            menu_default=menu['boot']['default'], hardware_tested=False,
                            scope='Host packaging and input pairing; no new binary/ABI or tablet qualification')
        manifest = ''.join(record['sha256'] + '  ' + name + '\n' for name, record in files.items())
        candidate = work / 'esp.zip'
        with zipfile.ZipFile(candidate, 'w', zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
            for name in files:
                archive.write(args.esp / name, name)
            archive.writestr('SHA256SUMS', manifest)
            archive.writestr('verification.json', json.dumps(verification, indent=2) + '\n')
        with zipfile.ZipFile(candidate) as archive:
            if archive.testzip() is not None:
                raise ValueError('ZIP CRC failed')
            for name, record in files.items():
                if hashlib.sha256(archive.read(name)).hexdigest() != record['sha256']:
                    raise ValueError('ZIP file readback mismatch')
        candidate.rename(args.output)
    print(json.dumps(dict(passed=True, zip_crc_verified=True, fat_readback_verified=True,
                          archive_sha256=sha(args.output), hardware_tested=False), indent=2))


if __name__ == '__main__':
    main()
