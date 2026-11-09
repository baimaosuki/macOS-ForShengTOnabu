#!/usr/bin/env python3
"""Build a Nabu macOS ramdisk candidate: inventory the mounted Apple root,
replace the arm64e userspace entry points with verified arm64 translations,
re-sign ad hoc, and repack the DBRDISK1 container.

Runs on macOS only (hdiutil/codesign).  Every stage writes evidence into the
report; nothing is declared good because a command exited 0 -- the repacked
container is unpacked again, re-attached, and the replaced files are hashed
from inside the rebuilt image.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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


def run(*command: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(command, capture_output=True, text=True)
    if check and result.returncode:
        raise SystemExit('%s failed (%d): %s%s' % (' '.join(command), result.returncode,
                                                   result.stdout, result.stderr))
    return result


def attach(image: Path, mountpoint: Path, writable: bool) -> None:
    mountpoint.mkdir(parents=True, exist_ok=True)
    command = ['hdiutil', 'attach', '-nobrowse', '-noverify', '-mountpoint', str(mountpoint)]
    command.append('-readwrite' if writable else '-readonly')
    command.append(str(image))
    run(*command)


def detach(mountpoint: Path) -> None:
    run('hdiutil', 'detach', str(mountpoint), check=False)
    run('hdiutil', 'detach', '-force', str(mountpoint), check=False)


def describe(path: Path) -> dict:
    info = dict(path=str(path), size=path.stat().st_size, md5=md5(path), sha256=sha256(path))
    lipo = run('lipo', '-archs', str(path), check=False)
    info['architectures'] = lipo.stdout.strip() if lipo.returncode == 0 else None
    file_out = run('file', '-b', str(path), check=False)
    info['file'] = file_out.stdout.strip() if file_out.returncode == 0 else None
    signature = run('codesign', '-dv', '--verbose=2', str(path), check=False)
    info['signature'] = (signature.stderr or signature.stdout).strip().splitlines()[:6] or None
    return info


def inventory(mountpoint: Path, report_path: Path) -> dict:
    roots = {}
    hits = []
    total_files = 0
    total_bytes = 0
    for base, directories, files in os.walk(mountpoint):
        directories[:] = [d for d in directories if not os.path.islink(os.path.join(base, d))]
        relative = os.path.relpath(base, mountpoint)
        depth = 0 if relative == '.' else relative.count(os.sep) + 1
        if depth <= 2:
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
                hits.append(dict(relative=os.path.relpath(path, mountpoint),
                                 size=stat.st_size))
    detailed = []
    for hit in hits:
        path = mountpoint / hit['relative']
        if path.is_file() and path.stat().st_size < (64 << 20):
            detailed.append(describe(path))
        else:
            detailed.append(hit)
    system_version = mountpoint / 'System/Library/CoreServices/SystemVersion.plist'
    version = None
    if system_version.exists():
        version = run('plutil', '-p', str(system_version), check=False).stdout
    report = dict(mountpoint=str(mountpoint), total_files=total_files,
                  total_bytes=total_bytes, top_level=roots,
                  interesting=detailed, system_version=version)
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    return report


def convert(source: Path, work: Path, report_dir: Path) -> dict:
    """Run the instruction and pointer translation plus its independent verifier."""
    name = source.name
    stem = name[:-7] if name.endswith('.arm64e') else name
    arm64e = work / f'{stem}.arm64e'
    shutil.copy2(source, arm64e)
    arm64 = work / f'{stem}.arm64'
    strip_report = work / f'{stem}.strip.json'
    run(sys.executable, str(ROOT / 'scripts/nabu_pac_strip.py'),
        '--input', str(arm64e), '--output', str(arm64),
        '--report', str(strip_report), '--downgrade-subtype')
    verify_report = work / f'{stem}.verify.json'
    verify = run(sys.executable, str(ROOT / 'scripts/verify_userland_fixups.py'),
                 '--translated', str(arm64), '--original', str(arm64e),
                 '--report', str(verify_report), check=False)
    translation = json.loads(strip_report.read_text())
    verification = json.loads(verify_report.read_text()) if verify_report.exists() else {}
    shutil.copy2(verify_report, report_dir / verify_report.name)
    shutil.copy2(strip_report, report_dir / strip_report.name)
    if verify.returncode != 0:
        raise SystemExit('%s failed independent fixup verification: %s'
                         % (name, verify.stdout + verify.stderr))
    if translation['instructions_rewritten'] == 0:
        raise SystemExit(f'{name}: instruction translation rewrote nothing')
    chains = translation['chained_fixups'].get('userland_chains', {})
    if chains.get('pointer_format_after') != 6:
        raise SystemExit(f'{name}: pointer format was not converted to 64_OFFSET')
    if not chains.get('converted') or chains.get('converted') != chains.get('verified'):
        raise SystemExit(f'{name}: chain conversion counters disagree')
    return dict(translation=translation, verification=verification,
                arm64=arm64, before=describe(arm64e))


def resign(path: Path) -> dict:
    result = run('codesign', '--force', '--sign', '-', '--timestamp=none',
                 '--preserve-metadata=identifier,flags', str(path), check=False)
    fallback = None
    if result.returncode:
        result = run('codesign', '--force', '--sign', '-', '--timestamp=none', str(path),
                     check=False)
        fallback = 'plain ad-hoc (preserve-metadata rejected)'
    verify = run('codesign', '--verify', '--verbose=2', str(path), check=False)
    return dict(returncode=result.returncode, fallback=fallback,
                output=(result.stdout + result.stderr).strip()[:600],
                verify_output=(verify.stdout + verify.stderr).strip()[:600],
                verify_ok=verify.returncode == 0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base-raw', type=Path, required=True)
    ap.add_argument('--asset', nargs='+', required=True,
                    help='absolute in-image paths to translate and replace')
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--report', type=Path, required=True)
    ap.add_argument('--keep-raw', action='store_true')
    args = ap.parse_args()

    if sys.platform != 'darwin':
        raise SystemExit('this builder needs macOS (hdiutil, codesign)')

    work = args.work
    work.mkdir(parents=True, exist_ok=True)
    mountpoint = work / 'mnt'
    report = dict(base=dict(path=str(args.base_raw), size=args.base_raw.stat().st_size,
                            md5=md5(args.base_raw), sha256=sha256(args.base_raw)),
                  host=dict(platform=sys.platform,
                            version=run('sw_vers', '-productVersion', check=False).stdout.strip()),
                  assets=[], steps=[])

    def step(name, **payload):
        report['steps'].append(dict(step=name, **payload))

    attach(args.base_raw, mountpoint, writable=False)
    report['inventory_before'] = inventory(mountpoint, work / 'inventory-before.json')
    step('inventory', files=report['inventory_before']['total_files'],
         bytes=report['inventory_before']['total_bytes'])
    extracted = []
    for image_path in args.asset:
        source = mountpoint / image_path.lstrip('/')
        if not source.exists():
            raise SystemExit(f'{image_path} is absent from the mounted root')
        staged = work / (image_path.strip('/').replace('/', '_') + '.arm64e')
        shutil.copy2(source, staged)
        extracted.append((image_path, staged))
        step('extract', asset=image_path, size=staged.stat().st_size, md5=md5(staged))
    detach(mountpoint)

    for image_path, source in extracted:
        converted = convert(source, work, args.report.parent)
        report['assets'].append(dict(image_path=image_path, source=str(source),
                                     before=converted['before'],
                                     translation=converted['translation'],
                                     verification=converted['verification'],
                                     arm64_md5=md5(converted['arm64']),
                                     arm64_size=converted['arm64'].stat().st_size))
        step('translate', asset=image_path,
             instructions=converted['translation']['instructions_rewritten'],
             converted=converted['translation']['chained_fixups']['userland_chains'].get('converted'),
             verified=converted['verification'].get('semantic_verified'))
        (work / image_path.replace('/', '_')).write_bytes(converted['arm64'].read_bytes())

    attach(args.base_raw, mountpoint, writable=True)
    for entry in report['assets']:
        target = mountpoint / entry['image_path'].lstrip('/')
        if not target.exists():
            raise SystemExit(f'{entry["image_path"]} is absent from the mounted root')
        staged = work / entry['image_path'].replace('/', '_')
        shutil.copyfile(staged, target)
        target.chmod(0o755)
        run('xattr', '-c', str(target), check=False)
        entry['resign'] = resign(target)
        entry['after_inside_image'] = describe(target)
        step('replace', asset=entry['image_path'], md5=entry['after_inside_image']['md5'],
             verify_ok=entry['resign']['verify_ok'])
    report['inventory_after_inside'] = inventory(mountpoint, work / 'inventory-after.json')
    detach(mountpoint)

    report['modified'] = dict(size=args.base_raw.stat().st_size, md5=md5(args.base_raw))
    packed = args.out
    packed.parent.mkdir(parents=True, exist_ok=True)
    pack = run(sys.executable, str(ROOT / 'scripts/pack_ramdisk_sparse.py'),
               str(args.base_raw), str(packed))
    report['pack'] = dict(stdout=pack.stdout.strip(), size=packed.stat().st_size,
                          md5=md5(packed), sha256=sha256(packed))
    step('pack', bytes=packed.stat().st_size)

    # Round-trip: unpack the container we just produced, prove it is byte-identical
    # to the modified base, then mount it again and hash the replaced files from
    # inside the rebuilt image.
    unpacked = work / 'roundtrip.raw'
    run(sys.executable, str(ROOT / 'scripts/unpack_ramdisk_sparse.py'),
        str(packed), str(unpacked), '--report', str(work / 'roundtrip.json'))
    identical = md5(unpacked) == report['modified']['md5'] and \
        unpacked.stat().st_size == report['modified']['size']
    report['roundtrip'] = dict(raw_identical=identical,
                               unpacked=dict(size=unpacked.stat().st_size, md5=md5(unpacked)))
    if not identical:
        args.report.write_text(json.dumps(report, indent=2) + '\n')
        raise SystemExit('repacked container does not reproduce the modified image')

    attach(unpacked, mountpoint, writable=False)
    seen = []
    for entry in report['assets']:
        target = mountpoint / entry['image_path'].lstrip('/')
        now = describe(target) if target.exists() else None
        entry['after_repack'] = now
        seen.append(dict(image_path=entry['image_path'],
                         matches_staged=bool(now) and now['md5'] == entry['arm64_md5'],
                         md5=now['md5'] if now else None))
    report['roundtrip']['replaced_inside_rebuilt'] = seen
    detach(mountpoint)

    ok = all(item['matches_staged'] for item in seen) and identical
    report['verdict'] = dict(candidate_ok=ok,
                             acceptance='enters dyld userspace is still to be proven on device')
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    if not args.keep_raw:
        for leftover in (work / 'roundtrip.raw',):
            leftover.unlink(missing_ok=True)
    print(json.dumps({k: report[k] for k in ('pack', 'roundtrip', 'verdict')}, indent=2))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
