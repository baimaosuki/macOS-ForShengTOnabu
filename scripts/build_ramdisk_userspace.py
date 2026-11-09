#!/usr/bin/env python3
"""Build a Nabu macOS ramdisk candidate on macOS.

Pipeline (all stages record evidence into the JSON report):

 1. wrap the bare APFS container in a minimal GPT so `hdiutil` can attach it
    deterministically, instead of depending on whole-disk filesystem probing;
 2. attach read-write, inventory the root, copy the userspace entry points out;
 3. translate them (instructions + chained fixups), verify with the independent
    parser against the arm64e originals, put them back and ad-hoc re-sign;
 4. detach, cut the container bytes back out of the wrapper, repack DBRDISK1;
 5. unpack the container we just produced, wrap and mount it again, and hash the
    replaced files from inside the rebuilt image.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import uuid
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECTOR = 512
ALIGN = 2048                      # 1 MiB partition start, standard for APFS
APFS_TYPE = uuid.UUID('7c3457ef-0000-11aa-aa11-00306543ecac')
INTERESTING = ('launchd', 'dyld', 'dyld_shared_cache', 'libSystem', 'libdyld',
               'SystemVersion.plist', 'libobjc', 'libc++.', 'libdiagnostic')


def md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def run(*command, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run([str(part) for part in command], capture_output=True, text=True)
    if check and result.returncode:
        raise SystemExit('%s failed (%d):\n%s\n%s' % (' '.join(str(c) for c in command),
                                                      result.returncode,
                                                      result.stdout, result.stderr))
    return result


ENTRIES = 128
ENTRY_SIZE = 128
HEADER_SIZE = 92


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def gpt_entry(first: int, last: int, name: str, unique: uuid.UUID) -> bytes:
    encoded = name.encode('utf-16-le')[:72]
    entry = (APFS_TYPE.bytes_le + unique.bytes_le
             + struct.pack('<QQQ', first, last, 0)
             + encoded.ljust(72, b'\0'))
    if len(entry) != ENTRY_SIZE:
        raise SystemExit(f'GPT entry is {len(entry)} bytes, expected {ENTRY_SIZE}')
    return entry


def gpt_header(current: int, backup: int, entries_lba: int, entries_crc: int,
               first_usable: int, last_usable: int, disk_guid: uuid.UUID) -> bytes:
    """A GPT header with the CRC stored *in* the header, computed over 92 bytes."""
    fields = [b'EFI PART', 0x00010000, HEADER_SIZE, 0, 0,
              current, backup, first_usable, last_usable,
              disk_guid.bytes_le, entries_lba, ENTRIES, ENTRY_SIZE, entries_crc]
    body = struct.pack('<8sIIIIQQQQ16sQIII', *fields)
    if len(body) != HEADER_SIZE:
        raise SystemExit(f'GPT header is {len(body)} bytes, expected {HEADER_SIZE}')
    fields[3] = crc32(body)                     # the CRC field sits at offset 16
    body = struct.pack('<8sIIIIQQQQ16sQIII', *fields)
    if struct.unpack_from('<I', body, 16)[0] == 0:
        raise SystemExit('GPT header CRC was not written into the header')
    return body.ljust(SECTOR, b'\0')


def protective_mbr(disk_lba: int) -> bytes:
    entry = (b'\0' + b'\0\x02\0' + b'\xEE' + b'\xFF\xFF\xFF'
             + struct.pack('<II', 1, min(disk_lba - 1, 0xFFFFFFFF)))
    mbr = b'\0' * 446 + entry
    if len(mbr) != 462:
        raise SystemExit(f'protective MBR body is {len(mbr)} bytes, expected 462')
    return mbr.ljust(510, b'\0') + b'\x55\xAA'


def wrap_gpt(container: Path, wrapper: Path) -> dict:
    """Write a GPT wrapper whose single Apple_APFS partition is the container verbatim."""
    payload = container.read_bytes()
    if len(payload) % SECTOR:
        payload += b'\0' * (SECTOR - len(payload) % SECTOR)
    sectors = len(payload) // SECTOR
    first = ALIGN
    last = first + sectors - 1
    entries_lba = 2
    backup_entries_lba = last + 1
    backup_header_lba = backup_entries_lba + ENTRIES * ENTRY_SIZE // SECTOR
    disk_lba = backup_header_lba + 1
    disk_guid = uuid.uuid4()

    entries = gpt_entry(first, last, 'NabuRoot', uuid.uuid4())
    entries += b'\0' * (ENTRIES * ENTRY_SIZE - ENTRY_SIZE)
    entries_crc = crc32(entries)

    image = bytearray(SECTOR * disk_lba)
    image[0:SECTOR] = protective_mbr(disk_lba)
    image[SECTOR * 1:SECTOR * 2] = gpt_header(1, backup_header_lba, entries_lba, entries_crc,
                                              first, last, disk_guid)
    image[SECTOR * entries_lba:SECTOR * entries_lba + len(entries)] = entries
    image[SECTOR * first:SECTOR * first + len(payload)] = payload
    image[SECTOR * backup_entries_lba:SECTOR * backup_entries_lba + len(entries)] = entries
    image[SECTOR * backup_header_lba:SECTOR * (backup_header_lba + 1)] = gpt_header(
        backup_header_lba, 1, backup_entries_lba, entries_crc, first, last, disk_guid)

    if len(image) != SECTOR * disk_lba:
        raise SystemExit(f'wrapper is {len(image)} bytes, expected {SECTOR * disk_lba}')
    if image[510:512] != b'\x55\xAA':
        raise SystemExit('protective MBR signature is not at offset 510')
    if image[SECTOR * first:SECTOR * first + len(payload)] != payload:
        raise SystemExit('partition payload does not match the container')
    wrapper.write_bytes(image)
    checked = verify_gpt(wrapper, len(payload), first)
    return dict(checked, wrapper=str(wrapper), wrapper_bytes=len(image), payload_bytes=len(payload),
                payload_offset=SECTOR * first, partition_first_lba=first,
                partition_last_lba=last, backup_entries_lba=backup_entries_lba,
                backup_header_lba=backup_header_lba, disk_lba=disk_lba,
                disk_guid=str(disk_guid), entries_crc=hex(entries_crc))


def verify_gpt(wrapper: Path, payload_bytes: int, first_lba: int) -> dict:
    """Re-read the wrapper we just wrote and assert its geometry and CRCs."""
    data = wrapper.read_bytes()
    if len(data) % SECTOR:
        raise SystemExit('wrapper length is not a whole number of sectors')
    disk_lba = len(data) // SECTOR
    if data[510:512] != b'\x55\xAA' or data[450] != 0xEE:
        raise SystemExit('protective MBR is malformed')
    primary = data[SECTOR:SECTOR + HEADER_SIZE + 4]
    stored = struct.unpack_from('<I', primary, 16)[0]
    if crc32(primary[:16] + b'\0\0\0\0' + primary[20:92]) != stored:
        raise SystemExit('primary GPT header CRC is wrong')
    current, backup = struct.unpack_from('<QQ', primary, 24)
    entries_lba, count, size, entries_crc = struct.unpack_from('<QIII', primary, 72)
    if (current, backup, count, size) != (1, disk_lba - 1, ENTRIES, ENTRY_SIZE):
        raise SystemExit(f'primary header geometry is wrong: {current}/{backup}/{count}/{size}')
    entries = data[SECTOR * entries_lba:SECTOR * entries_lba + count * size]
    if crc32(entries) != entries_crc:
        raise SystemExit('primary partition entry array CRC is wrong')
    first, last = struct.unpack_from('<QQ', entries, 32)
    if (entries[:16] != APFS_TYPE.bytes_le or first != first_lba
            or last + 34 != disk_lba):
        raise SystemExit('partition 1 is not the Apple_APFS extent we asked for')
    backup_header = data[SECTOR * (disk_lba - 1):]
    if backup_header[0:8] != b'EFI PART':
        raise SystemExit('backup GPT header is missing')
    backup_stored = struct.unpack_from('<I', backup_header, 16)[0]
    if crc32(backup_header[:16] + b'\0\0\0\0' + backup_header[20:92]) != backup_stored:
        raise SystemExit('backup GPT header CRC is wrong')
    backup_current, backup_other = struct.unpack_from('<QQ', backup_header, 24)
    if (backup_current, backup_other) != (disk_lba - 1, 1):
        raise SystemExit('backup header does not point back at the primary header')
    if backup_header[56:72] != primary[56:72]:
        raise SystemExit('primary and backup headers disagree about the disk GUID')
    offset = SECTOR * first_lba
    if data[offset:offset + payload_bytes][0x20:0x24] != b'NXSB':
        raise SystemExit('payload at the partition offset is not an APFS container')
    return dict(disk_lba=disk_lba, payload_offset=offset, header_crc=hex(stored),
                entries_crc=hex(entries_crc))



def unwrap_gpt(wrapper: Path, offset: int, length: int, out: Path) -> None:
    with wrapper.open('rb') as stream:
        stream.seek(offset)
        data = stream.read(length)
    if len(data) != length:
        raise SystemExit('wrapper is shorter than the recorded container extent')
    out.write_bytes(data)


def attach(wrapper: Path, mountpoint: Path, writable: bool) -> str:
    mountpoint.mkdir(parents=True, exist_ok=True)
    result = run('hdiutil', 'attach', '-nobrowse', '-noverify',
                 '-readwrite' if writable else '-readonly',
                 '-mountpoint', mountpoint, wrapper, check=False)
    if result.returncode:
        raise SystemExit('hdiutil attach failed:\n%s\n%s' % (result.stdout, result.stderr))
    return result.stdout


def detach(mountpoint: Path) -> None:
    for _ in range(3):
        result = run('hdiutil', 'detach', '-force', mountpoint, check=False)
        if result.returncode == 0:
            return
        run('sleep', '2', check=False)
    raise SystemExit(f'could not detach {mountpoint}')


def describe(path: Path) -> dict:
    info = dict(path=str(path), size=path.stat().st_size, md5=md5(path), sha256=sha256(path))
    lipo = run('lipo', '-archs', path, check=False)
    info['architectures'] = lipo.stdout.strip() if lipo.returncode == 0 else None
    file_out = run('file', '-b', path, check=False)
    info['file'] = file_out.stdout.strip() if file_out.returncode == 0 else None
    signature = run('codesign', '-dv', '--verbose=2', path, check=False)
    info['signature'] = (signature.stderr or signature.stdout).strip().splitlines()[:4] or None
    return info


def inventory(mountpoint: Path, report_path: Path) -> dict:
    roots, hits = {}, []
    total_files = total_bytes = 0
    for base, directories, files in os.walk(mountpoint):
        directories[:] = [d for d in directories if not os.path.islink(os.path.join(base, d))]
        relative = os.path.relpath(base, mountpoint)
        if relative == '.' or relative.count(os.sep) <= 1:
            roots[relative] = len(files)
        for name in files:
            path = Path(base) / name
            try:
                stat = path.lstat()
            except OSError:
                continue
            total_files += 1
            total_bytes += stat.st_size
            if any(token in name for token in INTERESTING):
                hits.append(dict(relative=os.path.relpath(path, mountpoint), size=stat.st_size))
    detailed = []
    for hit in hits:
        path = mountpoint / hit['relative']
        detailed.append(describe(path) if path.is_file() and path.stat().st_size < (48 << 20)
                        else hit)
    version = None
    plist = mountpoint / 'System/Library/CoreServices/SystemVersion.plist'
    if plist.exists():
        version = run('plutil', '-p', plist, check=False).stdout
    report = dict(total_files=total_files, total_bytes=total_bytes, top_level=roots,
                  interesting=detailed, system_version=version)
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    return report


def convert(source: Path, work: Path, report_dir: Path) -> dict:
    stem = source.name[:-7] if source.name.endswith('.arm64e') else source.name
    arm64e = work / f'{stem}.arm64e'
    if arm64e != source:
        shutil.copy2(source, arm64e)
    arm64 = work / f'{stem}.arm64'
    strip_report = work / f'{stem}.strip.json'
    run(sys.executable, ROOT / 'scripts/nabu_pac_strip.py',
        '--input', arm64e, '--output', arm64, '--report', strip_report, '--downgrade-subtype')
    verify_report = work / f'{stem}.verify.json'
    verify = run(sys.executable, ROOT / 'scripts/verify_userland_fixups.py',
                 '--translated', arm64, '--original', arm64e, '--report', verify_report,
                 check=False)
    report_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(strip_report, report_dir / strip_report.name)
    if verify_report.exists():
        shutil.copy2(verify_report, report_dir / verify_report.name)
    translation = json.loads(strip_report.read_text())
    if verify.returncode:
        raise SystemExit('%s failed independent fixup verification:\n%s%s'
                         % (stem, verify.stdout, verify.stderr))
    verification = json.loads(verify_report.read_text())
    chains = translation['chained_fixups'].get('userland_chains', {})
    if translation['instructions_rewritten'] == 0:
        raise SystemExit(f'{stem}: instruction translation rewrote nothing')
    if chains.get('pointer_format_after') != 6:
        raise SystemExit(f'{stem}: pointer format was not converted to 64_OFFSET')
    if not chains.get('converted') or chains['converted'] != chains['verified']:
        raise SystemExit(f'{stem}: chain conversion counters disagree')
    if verification.get('mismatch_count'):
        raise SystemExit(f'{stem}: verifier reported {verification["mismatch_count"]} mismatches')
    return dict(translation=translation, verification=verification, arm64=arm64,
                before=describe(arm64e))


def resign(path: Path) -> dict:
    result = run('codesign', '--force', '--sign', '-', '--timestamp=none',
                 '--preserve-metadata=identifier,flags', path, check=False)
    fallback = None
    if result.returncode:
        result = run('codesign', '--force', '--sign', '-', '--timestamp=none', path, check=False)
        fallback = 'plain ad-hoc (preserve-metadata rejected)'
    verify = run('codesign', '--verify', '--verbose=2', path, check=False)
    return dict(returncode=result.returncode, fallback=fallback,
                output=(result.stdout + result.stderr).strip()[:400],
                verify_ok=verify.returncode == 0,
                verify_output=(verify.stdout + verify.stderr).strip()[:400])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base-raw', type=Path, required=True)
    ap.add_argument('--asset', nargs='+', required=True, help='absolute in-image paths')
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--report', type=Path, required=True)
    args = ap.parse_args()
    if sys.platform != 'darwin':
        raise SystemExit('this builder needs macOS (hdiutil, codesign)')

    args.report.parent.mkdir(parents=True, exist_ok=True)
    work = args.work
    work.mkdir(parents=True, exist_ok=True)
    mountpoint = work / 'mnt'
    report = dict(base=dict(path=str(args.base_raw), size=args.base_raw.stat().st_size,
                            md5=md5(args.base_raw), sha256=sha256(args.base_raw)),
                  host=dict(platform=sys.platform,
                            version=run('sw_vers', '-productVersion', check=False).stdout.strip(),
                            build=run('sw_vers', '-buildVersion', check=False).stdout.strip()),
                  assets=[], steps=[])

    def step(name, **payload):
        report['steps'].append(dict(step=name, **payload))

    wrapper = work / 'base.wrapper.img'
    report['wrapper'] = wrap_gpt(args.base_raw, wrapper)
    step('wrap', **report['wrapper'])
    attach(wrapper, mountpoint, writable=True)
    report['inventory_before'] = inventory(mountpoint, work / 'inventory-before.json')
    step('inventory', files=report['inventory_before']['total_files'],
         bytes=report['inventory_before']['total_bytes'])

    staged = []
    for image_path in args.asset:
        source = mountpoint / image_path.lstrip('/')
        if not source.exists():
            raise SystemExit(f'{image_path} is absent from the mounted root')
        copy = work / (image_path.strip('/').replace('/', '_') + '.arm64e')
        shutil.copy2(source, copy)
        staged.append((image_path, copy))
        step('extract', asset=image_path, size=copy.stat().st_size, md5=md5(copy))

    converted = []
    for image_path, copy in staged:
        result = convert(copy, work, args.report.parent)
        converted.append((image_path, result))
        entry = dict(image_path=image_path, before=result['before'],
                     translation=result['translation'], verification=result['verification'],
                     arm64_md5=md5(result['arm64']), arm64_size=result['arm64'].stat().st_size)
        report['assets'].append(entry)
        step('translate', asset=image_path,
             instructions=result['translation']['instructions_rewritten'],
             pointers=result['translation']['chained_fixups']['userland_chains'].get('converted'),
             verified=result['verification'].get('semantic_verified'))

    for entry, (image_path, result) in zip(report['assets'], converted):
        target = mountpoint / image_path.lstrip('/')
        shutil.copyfile(result['arm64'], target)
        target.chmod(0o755)
        run('xattr', '-c', target, check=False)
        entry['resign'] = resign(target)
        if not entry['resign']['verify_ok']:
            raise SystemExit(f'{image_path}: codesign verification failed, refusing to ship it')
        entry['signed_inside_image'] = describe(target)
        step('replace', asset=image_path, signed_md5=entry['signed_inside_image']['md5'],
             verify_ok=entry['resign']['verify_ok'])

    report['inventory_after_inside'] = inventory(mountpoint, work / 'inventory-after.json')
    detach(mountpoint)

    modified = work / 'modified.raw'
    unwrap_gpt(wrapper, report['wrapper']['payload_offset'],
               report['wrapper']['payload_bytes'], modified)
    report['modified'] = dict(size=modified.stat().st_size, md5=md5(modified),
                              sha256=sha256(modified),
                              unchanged_prefix=md5(modified) != report['base']['md5'])
    if modified.stat().st_size != report['base']['size']:
        raise SystemExit('extracted container changed size after the edit')

    args.out.parent.mkdir(parents=True, exist_ok=True)
    pack = run(sys.executable, ROOT / 'scripts/pack_ramdisk_sparse.py', modified, args.out)
    report['pack'] = dict(stdout=pack.stdout.strip(), size=args.out.stat().st_size,
                          md5=md5(args.out), sha256=sha256(args.out))
    step('pack', bytes=args.out.stat().st_size)

    unpacked = work / 'roundtrip.raw'
    run(sys.executable, ROOT / 'scripts/unpack_ramdisk_sparse.py', args.out, unpacked,
        '--report', work / 'roundtrip.json')
    identical = unpacked.stat().st_size == modified.stat().st_size and md5(unpacked) == md5(modified)
    report['roundtrip'] = dict(raw_identical=identical,
                               unpacked=dict(size=unpacked.stat().st_size, md5=md5(unpacked)))
    if not identical:
        args.report.write_text(json.dumps(report, indent=2) + '\n')
        raise SystemExit('repacked container does not reproduce the modified image')

    wrapper2 = work / 'roundtrip.wrapper.img'
    report['roundtrip']['wrapper'] = wrap_gpt(unpacked, wrapper2)
    attach(wrapper2, mountpoint, writable=False)
    seen = []
    for entry in report['assets']:
        target = mountpoint / entry['image_path'].lstrip('/')
        now = describe(target) if target.exists() else None
        entry['after_repack'] = now
        expected = entry['signed_inside_image']['md5']
        seen.append(dict(image_path=entry['image_path'],
                         present=bool(now),
                         matches_signed=bool(now) and now['md5'] == expected,
                         expected_signed_md5=expected,
                         md5=now['md5'] if now else None,
                         architectures=now['architectures'] if now else None))
    report['roundtrip']['replaced_inside_rebuilt'] = seen
    report['inventory_after_repack'] = inventory(mountpoint, work / 'inventory-repacked.json')
    detach(mountpoint)

    ok = all(item['matches_signed'] for item in seen) and identical
    report['verdict'] = dict(candidate_ok=ok,
                             acceptance='entering dyld userspace still has to be shown on device',
                             unsigned_disclaimer='replacements are ad-hoc signed: AMFI may reject '
                                                 'them at exec even though the arch check passes')
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('pack', 'roundtrip', 'verdict')}, indent=2)[:2000])
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
