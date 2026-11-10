#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Link CI's ordinary ARM64 UFS objects with the actual local owner policies."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from native_macho import macho, symbols
from prepare_nabu_desktop_policy import assembly, validate


def link(compiled: Path, policy: Path, destination: Path) -> dict:
    evidence = json.loads((compiled / 'build.json').read_text())
    if (evidence.get('architecture') != 'arm64' or evidence.get('root_mode') != 'desktop'
            or evidence.get('desktop_policy_deferred') is not True
            or evidence.get('source_integration', {}).get('board') != 'nabu'):
        raise ValueError('requires the compiled NABU ARM64 desktop UFS runtime')
    contract = validate(policy, '25F71')
    destination.mkdir(parents=True, exist_ok=False)
    objects = []
    for name in ('NativeUfs.o', 'verified-root.o', 'verified-crypto.o', 'NativeRamBlock.o'):
        path = compiled / name
        if macho(path.read_bytes(), 'arm64')['kind'] != 1:
            raise ValueError('requires actual ordinary ARM64 runtime objects')
        objects.append(str(path.resolve()))
    source = destination / 'owner-policies.S'
    source.write_text(assembly(policy))
    clang = shutil.which('clang-17') or shutil.which('clang')
    linker = shutil.which('ld64.lld-17') or shutil.which('ld64.lld')
    if not clang or not linker:
        raise ValueError('requires a Mach-O-capable clang and ld64.lld')
    obj = destination / 'owner-policies.o'
    subprocess.run([clang, '-target', 'arm64-apple-macos26.5', '-c', source, '-o', obj], check=True)
    binary = destination / 'NativeUfs.macho'
    subprocess.run([linker, '-arch', 'arm64', '-dylib', '-platform_version', 'macos', '26.5', '26.5',
                    '-undefined', 'dynamic_lookup', '-fixup_chains', '-no_adhoc_codesign',
                    '-headerpad', '0x1000', *objects, obj, '-o', binary], check=True)
    data = bytearray(binary.read_bytes())
    import struct
    image = macho(data, 'arm64')
    commands = []
    for kind, offset, size in image['commands']:
        if kind in (0xc, 0x18, 0x80000018, 0x8000001f, 0x80000023):
            raise ValueError('kernel transport must not load a userspace dynamic library')
        # ld64.lld does not implement -kext. Its -dylib output uses the same
        # chained pointer and section ABI; remove the dylib identity command
        # when constructing the explicit KMOD bundle, then validate it below.
        if kind != 0xd:  # LC_ID_DYLIB has no meaning in MH_KEXT_BUNDLE.
            commands.append(bytes(data[offset:offset + size]))
    rebuilt = b''.join(commands)
    command_end = image['commands'][-1][1] + image['commands'][-1][2]
    data[32:command_end] = rebuilt + bytes(command_end - 32 - len(rebuilt))
    struct.pack_into('<II', data, 16, len(commands), len(rebuilt))
    struct.pack_into('<I', data, 12, 11)  # same KMOD_EXPLICIT_DECL ABI, MH_KEXT_BUNDLE
    binary.write_bytes(data)
    defined = symbols(binary)
    for name in ('_verified_root_policies', '_verified_root_policy_count', '_verified_root_key',
                 '_vr_open', '_vr_verify_block', '_vr_xnu_crypto', '_kmod_info',
                 '__ZN14NativeRamBlock10gMetaClassE', '_nabu_ram_data_volume_seed'):
        if name not in defined:
            raise ValueError('final UFS module omitted required definition: ' + name)
    shutil.copy2(compiled / 'Info.plist', destination / 'Info.plist')
    evidence.update(binary_sha256=hashlib.sha256(data).hexdigest(),
                    desktop_policy_deferred=False, desktop_owner_policy=contract,
                    compiled_objects_sha256={Path(p).name: hashlib.sha256(Path(p).read_bytes()).hexdigest()
                                             for p in objects},
                    owner_policy_bytes=sum((policy / n / 'manifest.bin').stat().st_size
                                           for n in ('OS', 'SystemOS', 'App')),
                    load_commands_checked=True, user_dylib_identity_removed=True,
                    final_linker=subprocess.check_output([linker, '--version'], text=True).strip())
    (destination / 'build.json').write_text(json.dumps(evidence, indent=2) + '\n')
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiled', type=Path, required=True)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(link(args.compiled, args.policy, args.output), indent=2))
