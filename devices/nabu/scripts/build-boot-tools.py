#!/usr/bin/env python3
"""Build signed Nabu boot tools on Intel macOS, independently of Data seeds."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[3]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if (platform.system(), platform.machine()) != ('Darwin', 'x86_64'):
        raise SystemExit('requires an Intel macOS build host')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    board = tomllib.loads((ROOT / 'devices/nabu/board.toml').read_text())
    arch = board['payload']['runtime']['boot_tool_arch']
    if arch != 'arm64':
        raise SystemExit('Nabu owner boot tools require the standard arm64 ABI')
    subprocess.run([sys.executable, str(ROOT / 'devices/nabu/tests/test_session_observer27.py')], check=True)
    spec = importlib.util.spec_from_file_location('boot_runtime', ROOT / 'scripts/build-boot-runtime.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    with tempfile.TemporaryDirectory(prefix='nabu-tools-') as td:
        work = Path(td)
        builder.build_boot_shims(work, arch=arch)
        subprocess.run([sys.executable, str(ROOT / 'scripts/absent-service/build.py'),
                        str(work / 'absent'), 'macos27', '--arch', arch], check=True)
        files = {'bootshim/apfs_boot_util': work / 'boot-shims/bootshim/apfs_boot_util',
                 'bin/unixnu-absentd': work / 'absent/bin/unixnu-absentd'}
        records = {}
        for name, path in files.items():
            raw = path.read_bytes()
            magic, cpu, subtype = struct.unpack_from('<3I', raw)
            if (magic, cpu, subtype) != (0xfeedfacf, 0x100000c, 0):
                raise SystemExit(f'{name}: compiler produced an unsupported Mach-O ABI: {subtype:#x}')
            cdhash = builder.verified_cdhash(path)
            destination = out / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            records[name] = dict(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw),
                                 cpu_type=hex(cpu), cpu_subtype=hex(subtype), cdhash=cdhash,
                                 codesign_strict_verified=True)
    inputs = ['scripts/build-boot-runtime.py', 'scripts/macos-boot-shims/apfs_boot_util.c',
              'scripts/macos-boot-shims/nabu_raw_stacks.h',
              'scripts/macos-boot-shims/nabu_session_observer.h',
              'scripts/macos-boot-shims/nabu_observer_output.h',
              'scripts/macos-boot-shims/nabu_clean_shutdown.h',
              'scripts/macos-boot-shims/nabu_process_list.h',
              'scripts/macos-boot-shims/observer-entitlements.plist',
              'scripts/absent-service/build.py', 'scripts/absent-service/absentd.c',
              'devices/nabu/board.toml', 'devices/nabu/scripts/build-boot-tools.py']
    inputs.extend(['devices/nabu/tests/test_session_observer27.py',
                   'devices/nabu/tests/test_process_list27.py'])
    report = dict(host=platform.machine(), target=arch, build='26C5035r',
                  commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                  files=records, source_sha256={f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in inputs},
                  sdk=subprocess.check_output(['xcrun', '--sdk', 'macosx', '--show-sdk-version'], text=True).strip(),
                  data_seed_accessed=False, device_tested=False)
    (out / 'build.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
