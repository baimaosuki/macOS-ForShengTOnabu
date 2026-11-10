#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Link CI's ordinary ARM64 UFS objects with the actual local owner policies."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import plistlib

from native_macho import macho, symbols
from prepare_nabu_desktop_policy import assembly, validate


def compiled_evidence(compiled):
    """Recover the intermediate facts if Apple's ARM64 -kext used relocations.

    The CI log and actual objects establish compilation; they do not establish
    a bootable bundle. The local link below supplies and verifies that bundle.
    """
    wrapper = (compiled / 'NativeUfs.cpp').read_text()
    if ('#include "NabuStorageModule.h"' not in wrapper or
            'nabu_register_storage_start(nabu_storage_module_start)' not in wrapper):
        raise ValueError('compiled runtime omitted the paired VFS-late startup callback')
    for name in ('NabuStorageModule.h', 'NabuStorageLifecycle.h'):
        staged = list(compiled.glob('source-*/drivers/NativeUfs/' + name))
        current = Path(__file__).resolve().parents[1] / 'platform/nabu/macos26/storage' / name
        if len(staged) != 1 or staged[0].read_bytes() != current.read_bytes():
            raise ValueError('compiled late startup header differs: ' + name)
    if (compiled / 'build.json').exists():
        return json.loads((compiled / 'build.json').read_text())
    parent = json.loads((compiled.parent / 'build.json').read_text())
    if (parent.get('architecture') != 'arm64' or parent.get('machine') != 'NABU'
            or not parent.get('executable_kernel_built') or not parent.get('desktop_kernel')):
        raise ValueError('missing actual paired CI kernel build evidence')
    for label in ('compile', 'verified-root', 'verified-crypto', 'ram-data'):
        found = [c for c in parent['commands'] if c.get('log') == 'logs/ufs-driver-' + label + '.log']
        if len(found) != 1 or found[0].get('returncode') != 0:
            raise ValueError('CI did not compile actual UFS TU: ' + label)
    wrapper = (compiled / 'NativeUfs.cpp').read_text()
    if ('#define NATIVE_UFS_DESKTOP_ROOT 1' not in wrapper or
            '#define NATIVE_VERIFIED_BLOCK 1' not in wrapper or 'nabu_register_ram_seed' not in wrapper):
        raise ValueError('actual compiled wrapper is not the desktop owner runtime')
    sources = list(compiled.glob('source-*/drivers/NativeUfs/NativeUfs.cpp'))
    if len(sources) != 1:
        raise ValueError('requires one actual compiled integrated UFS source')
    source = sources[0]
    if 'NabuBoard.h' not in source.read_text() or 'nabuRootProvider' not in source.read_text():
        raise ValueError('actual runtime omitted NABU provider admission')
    bundle = 'org.blu-sharky.NativeUfs'
    info = dict(CFBundleIdentifier=bundle,CFBundleVersion='1.0.0',CFBundlePackageType='KEXT',
        CFBundleExecutable='NativeUfs',OSBundleRequired='Root',OSBundleLibraries={
        'com.apple.iokit.IOStorageFamily':'2.1','com.apple.kpi.iokit':'25.0',
        'com.apple.kpi.libkern':'25.0','com.apple.kpi.mach':'25.0','com.apple.kpi.private':'25.0'},
        IOKitPersonalities={})
    return dict(bundle=bundle,architecture='arm64',root_mode='desktop',board='nabu',
        source_integration=dict(board='nabu',source_sha256=hashlib.sha256(source.read_bytes()).hexdigest()),
        desktop_policy_deferred=True,root_partition='macosroot',root_lun=0,android_userdata_used=False,
        read_only_transport=True,hardware_enable_required=True,
        authentication='owner-signed-blocks-and-volume-uuid',kernel_integrated=False,
        derived_from_actual_ci=dict(build_sha256=hashlib.sha256((compiled.parent/'build.json').read_bytes()).hexdigest(),
            previous_failure=parent.get('error'),compiled_tus_verified=True),
        generated_info=info,runtime_qualified=False,hardware_qualified=False)


def link(compiled: Path, policy: Path, destination: Path) -> dict:
    evidence = compiled_evidence(compiled)
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
            raw = bytearray(data[offset:offset + size])
            if kind == 0x19 and data[offset + 8:offset + 24].rstrip(b'\0') == b'__LINKEDIT':
                # ld64.lld leaves the final linkedit vmsize byte-sized. Reserve
                # its full actual 16 KiB kernel page without changing file data.
                span, = struct.unpack_from('<Q', raw, 32)
                struct.pack_into('<Q', raw, 32, (span + 0x3fff) & ~0x3fff)
            commands.append(bytes(raw))
    rebuilt = b''.join(commands)
    command_end = image['commands'][-1][1] + image['commands'][-1][2]
    data[32:command_end] = rebuilt + bytes(command_end - 32 - len(rebuilt))
    struct.pack_into('<II', data, 16, len(commands), len(rebuilt))
    struct.pack_into('<I', data, 12, 11)  # same KMOD_EXPLICIT_DECL ABI, MH_KEXT_BUNDLE
    binary.write_bytes(data)
    defined = symbols(binary)
    for name in ('_verified_root_policies', '_verified_root_policy_count', '_verified_root_key',
                 '_vr_open', '_vr_verify_block', '_vr_xnu_crypto', '_kmod_info',
                 '__ZN14NativeRamBlock10gMetaClassE', '_nabu_ram_data_volume_seed',
                 '_nabu_storage_module_start'):
        if name not in defined:
            raise ValueError('final UFS module omitted required definition: ' + name)
    if 'generated_info' in evidence:
        (destination / 'Info.plist').write_bytes(plistlib.dumps(evidence.pop('generated_info')))
    else:
        shutil.copy2(compiled / 'Info.plist', destination / 'Info.plist')
    info = plistlib.loads((destination / 'Info.plist').read_bytes())
    if any(p.get('IOClass') in ('NativeUfs', 'NativeRamBlock')
           for p in info.get('IOKitPersonalities', {}).values()):
        raise ValueError('late storage cannot retain an automatic UFS/Data personality')
    evidence.update(binary_sha256=hashlib.sha256(data).hexdigest(),
                    desktop_policy_deferred=False, desktop_owner_policy=contract,
                    late_storage_start=True, automatic_storage_personalities=False,
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
