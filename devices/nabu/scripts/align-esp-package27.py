#!/usr/bin/env python3
"""Repack an existing validated nabu ESP with the aligned menu and fresh hashes.

Only the menu/metadata change. Kernel, firmware, addon and trust bytes are retained.
Requires Python3.11+, mtools and dosfstools; never mounts or flashes a device.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tempfile
import tomllib
import zipfile
from nabu_boot_config27 import install, MENU_PATH


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def repack(source, output):
    if output.exists():
        raise FileExistsError(output)
    if not shutil.which('mkfs.vfat') or not shutil.which('mcopy'):
        raise RuntimeError('FAT readback requires dosfstools and mtools')
    with tempfile.TemporaryDirectory(prefix='nabu-menu-', dir=output.parent) as temporary:
        work = Path(temporary)
        stage = work / 'stage'
        stage.mkdir()
        with zipfile.ZipFile(source) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or archive.testzip() is not None:
                raise ValueError('Duplicate ZIP members or failed CRC')
            for member in archive.infolist():
                path = PurePosixPath(member.filename)
                if (path.is_absolute() or '..' in path.parts or '\\' in member.filename
                        or stat.S_ISLNK(member.external_attr >> 16)):
                    raise ValueError('Unsafe ESP ZIP member')
            archive.extractall(stage)
        files = {p.relative_to(stage).as_posix(): sha(p) for p in sorted((stage / 'EFI').rglob('*')) if p.is_file()}
        expected = {}
        for line in (stage / 'SHA256SUMS').read_text().splitlines():
            digest, name = line.split(maxsplit=1)
            name = name.lstrip('*')
            if name in expected:
                raise ValueError('Duplicate manifest path')
            expected[name] = digest
        if expected != files:
            raise ValueError('Input EFI tree does not match SHA256SUMS')
        menu = stage / MENU_PATH
        report = install(stage, tomllib.loads(menu.read_text()))
        files[MENU_PATH.as_posix()] = report['sha256']
        # Existing native/Host evidence describes unchanged kernel bytes.
        verification_path = stage / 'verification.json'
        verification = json.loads(verification_path.read_text())
        for name, digest in files.items():
            previous = verification['efi_files'].get(name)
            if previous is None or (name != MENU_PATH.as_posix() and previous['sha256'] != digest):
                raise ValueError('Input verification manifest does not match EFI')
        verification['efi_files'][MENU_PATH.as_posix()] = dict(sha256=report['sha256'], bytes=report['bytes'])
        verification['boot_menu'] = report
        verification['aligned_menu_repacked'] = True
        # Write every final EFI file to a real FAT volume and read it back.
        fat = work / 'esp.fat'
        with fat.open('wb') as stream:
            stream.truncate(256 * 1024 * 1024)
        subprocess.run(['mkfs.vfat', '-F', '32', str(fat)], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(['mcopy', '-s', '-i', str(fat), str(stage / 'EFI'), '::/'], check=True)
        readback = work / 'readback'
        readback.mkdir()
        subprocess.run(['mcopy', '-s', '-i', str(fat), '::/EFI', str(readback)], check=True)
        actual = {p.relative_to(readback).as_posix(): sha(p) for p in (readback / 'EFI').rglob('*') if p.is_file()}
        if actual != files:
            raise ValueError('FAT readback mismatch')
        verification['fat_readback'] = dict(passed=True, files=len(files), bytes=sum((stage / p).stat().st_size for p in files))
        verification_path.write_text(json.dumps(verification, ensure_ascii=False, indent=2) + '\n')
        (stage / 'SHA256SUMS').write_text(''.join(digest + '  ' + name + '\n' for name, digest in files.items()))
        metadata = dict(passed=True, original_archive_sha256=sha(source), boot_menu=report,
                        kernel_sha256=files['EFI/UniXNU/Payloads/kernel.macho'],
                        only_efi_change=MENU_PATH.as_posix(), native_rebuilt=False,
                        fat_readback_verified=True, device_tested=False)
        (stage / 'boot-menu-update.json').write_text(json.dumps(metadata, indent=2) + '\n')
        (stage / '使用说明.md').write_text(
            '本包已内置 nabu 对齐版启动菜单，默认 A640 Metal GPU、UI scale=3。\n\n'
            '将 ZIP 内整个 EFI 目录合并覆盖到已有 ESP 根目录，执行 sync 后重启。\n'
            '普通启动保持现有 Data；只有显式选择 Initialize Data 并确认才会使用 data-reset=1。\n'
            '本包 SHA256SUMS 已按最终菜单重算，全部 EFI 文件应通过校验。\n'
            '旧包先解压 EFI/* 再覆盖对齐版菜单时，仅 unixnu.toml 的旧校验和不再匹配。\n'
            '驱动状态见仓库 STATUS.md 与 PROGRESS.md；本次菜单更新未做新的硬件验证。\n', encoding='utf-8')
        (stage / 'README.txt').write_text(
            'NABU macOS27.3 ESP with aligned boot menu\n'
            'Merge EFI into the existing ESP, sync, then boot A640 Metal GPU.\n'
            'Default=a640-metal; UI scale=3; initialization is confirmation-only.\n'
            'All EFI SHA256SUMS entries match this archive, including unixnu.toml.\n'
            'Kernel/addon/trust bytes are unchanged; no new hardware validation.\n')
        candidate = work / output.name
        with zipfile.ZipFile(candidate, 'w', zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
            for path in sorted(stage.rglob('*')):
                if path.is_file():
                    archive.write(path, path.relative_to(stage))
        with zipfile.ZipFile(candidate) as archive:
            if archive.testzip() is not None:
                raise ValueError('Final ZIP CRC failed')
            for name, digest in files.items():
                if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                    raise ValueError('Final archive hash mismatch')
        candidate.rename(output)
    return dict(metadata, archive=str(output.resolve()), sha256=sha(output), bytes=output.stat().st_size,
                zip_crc_verified=True, all_efi_checksums_match=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    report = repack(args.input, args.output)
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
