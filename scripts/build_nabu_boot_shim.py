#!/usr/bin/env python3
"""Build and verify the owner DataRAM APFS boot layout adapter on macOS."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import plistlib
import re
import struct
import subprocess

import verify_page_hashes

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'platform/nabu/macos26/bootshim/apfs_boot_util.c'
ENTITLEMENTS = SOURCE.parent / 'entitlements.plist'
TARGET = 'arm64-apple-macos26.5'
IDENTIFIER = 'org.nabu.apfs-boot-layout'


def run(*args):
    return subprocess.run([str(arg) for arg in args], check=True,
                          text=True, capture_output=True)


def build(output):
    if platform.system() != 'Darwin':
        raise RuntimeError('building the shipping boot shim requires macOS and its SDK')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    metadata_path = output / 'build.json'
    metadata_path.unlink(missing_ok=True)
    binary = output / 'apfs_boot_util'
    probe = SOURCE.parent / 'session_probe.sh'
    generated = output / 'nabu_session_probe_script.h'
    generated.write_text('static const char nabu_session_probe_script[] = ' +
                         json.dumps(probe.read_text(), ensure_ascii=True) + ';\n')
    run('xcrun', '--sdk', 'macosx', 'clang', '-target', TARGET,
        '-std=c11', '-Os', '-Wall', '-Wextra', '-Werror', '-I', output, SOURCE, '-o', binary)
    run('codesign', '--force', '--sign', '-', '--timestamp=none',
        '--identifier', IDENTIFIER, '--entitlements', ENTITLEMENTS,
        '--generate-entitlement-der', binary)
    run('codesign', '--verify', '--strict', '--verbose=2', binary)
    details = run('codesign', '-d', '--verbose=4', binary)
    match = re.search(r'^CDHash=([0-9a-fA-F]{40})$',
                      details.stdout + '\n' + details.stderr, re.MULTILINE)
    if match is None:
        raise RuntimeError('codesign did not report the boot shim CDHash')
    data = binary.read_bytes()
    embedded_entitlements = run('codesign', '-d', '--entitlements', ':-', binary)
    # Check the signed bytes, not only the source plist passed to codesign.
    if plistlib.loads(embedded_entitlements.stdout.encode()) != plistlib.loads(ENTITLEMENTS.read_bytes()):
        raise RuntimeError('signed shim entitlement dictionary differs')
    if len(data) < 32 or struct.unpack_from('<II', data) != (0xfeedfacf, 0x100000c):
        raise RuntimeError('boot shim must be a thin arm64 Mach-O')
    # Reject arm64e (CPU_SUBTYPE_ARM64E = 2), including its capability bits.
    if struct.unpack_from('<I', data, 8)[0] & 0xffffff != 0:
        raise RuntimeError('boot shim must use ordinary arm64, not arm64e')
    pages = verify_page_hashes.verify(str(binary))
    if not pages.get('signed') or not pages.get('ok') or pages.get('code_slots', 0) == 0:
        raise RuntimeError('boot shim CodeDirectory page verification failed')
    cdhash = match.group(1).lower()
    if pages['cdhash'][:40] != cdhash:
        raise RuntimeError('codesign CDHash disagrees with the embedded CodeDirectory')
    metadata = dict(sha256=hashlib.sha256(data).hexdigest(), cdhash=cdhash,
                    architecture='arm64', target=TARGET, identifier=IDENTIFIER,
                    source=str(SOURCE.relative_to(ROOT)),
                    source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
                    session_probe_script_sha256=hashlib.sha256(probe.read_bytes()).hexdigest(),
                    session_probe_header_sha256=hashlib.sha256((SOURCE.parent / 'session_probe.h').read_bytes()).hexdigest(),
                    entitlements_sha256=hashlib.sha256(ENTITLEMENTS.read_bytes()).hexdigest(),
                    bindfs_entitlement_verified=True,
                    page_hashes=pages)
    metadata_path.write_text(json.dumps(metadata, indent=2) + '\n')
    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True,
                        help='directory for apfs_boot_util and build.json')
    args = parser.parse_args(argv)
    try:
        metadata = build(args.output)
    except (RuntimeError, subprocess.CalledProcessError) as error:
        if isinstance(error, subprocess.CalledProcessError):
            parser.exit(1, f'{error.cmd}:\n{error.stdout}{error.stderr}')
        parser.exit(1, f'{error}\n')
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
