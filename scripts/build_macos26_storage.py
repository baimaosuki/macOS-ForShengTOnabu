#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build pinned native transports against the paired macOS storage headers."""
import hashlib
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
import uuid

from native_macho import macho
from native_log_file import validate_contract

BUNDLE = 'org.unixnu.driver.NativeVirtioBlock'
PROTOCOL_CHARACTERISTICS = {
    'Physical Interconnect': 'Virtual Interface',
    'Physical Interconnect Location': 'External',
}


def build(root, out, compile_record, source, run, record, env, *, transport='virtio', gop_display=False,
          recovery_input=False, root_mode='recovery', desktop_policy=None, board=None,
          defer_desktop_policy=False):
    board = board or record.get('board', 'sheng')
    architecture = record.get('architecture', 'arm64e')
    if root_mode not in ('recovery', 'desktop'):
        raise ValueError('UFS root mode must be recovery or desktop')
    if root_mode == 'desktop' and (transport != 'ufs' or (desktop_policy is None and not defer_desktop_policy) or board != 'nabu'):
        raise ValueError('desktop root requires NABU UFS and an explicit owner policy directory')
    if transport not in ('virtio', 'ufs'):
        raise ValueError('unsupported native storage transport')
    if gop_display and transport != 'ufs':
        raise ValueError('the board GOP companion requires the UFS bundle')
    if recovery_input and not gop_display:
        raise ValueError('the QEMU input experiment requires the GOP companion')
    component = 'NativeVirtioBlock' if transport == 'virtio' else 'NativeUfs'
    bundle = BUNDLE if transport == 'virtio' else 'org.blu-sharky.NativeUfs'
    directory = 'storage-driver' if transport == 'virtio' else 'ufs-driver'
    destination = out / directory
    destination.mkdir(exist_ok=True)
    display = None
    input_driver = None
    if gop_display:
        from prepare_macos26_gop import prepare
        display = prepare(root, destination / 'gop-display')
    if recovery_input:
        from prepare_macos26_input import prepare
        input_driver = prepare(root, destination / 'recovery-input')
    upstream = root / 'UniXNU/drivers' / component / (component + '.cpp')
    source_evidence = None
    if transport == 'ufs':
        from nabu_storage_source import stage
        integration = Path(tempfile.mkdtemp(prefix='source-', dir=destination))
        upstream, source_evidence = stage(root, integration, board=board)
    include = destination / 'include/IOKit/storage'
    include.mkdir(parents=True, exist_ok=True)
    for header in (root / 'sources/macos26/IOStorageFamily').glob('*.h'):
        (include / header.name).write_bytes(header.read_bytes())
    wrapper = destination / (component + '.cpp')
    entry_prefix = 'sheng_virtio' if transport == 'virtio' else 'sheng_ufs'
    recovery_prefix = ''
    recovery_root = None
    if transport == 'ufs' and root_mode == 'desktop':
        from prepare_nabu_desktop_policy import validate
        owner_policy = None
        if not defer_desktop_policy:
            desktop_policy = Path(desktop_policy).resolve(strict=True)
            owner_policy = validate(desktop_policy, record['build'])
        recovery_prefix = '#define NATIVE_UFS_DESKTOP_ROOT 1\n#define NATIVE_VERIFIED_BLOCK 1\n'
    if transport == 'ufs' and root_mode == 'recovery':
        recovery_root = json.loads((root / 'config/macos26-recovery-root.json').read_text())
        digest = bytes.fromhex(recovery_root['sha256'])
        identity = str(uuid.UUID(recovery_root['volume_uuid'])).upper()
        if (recovery_root['build'] != '25F71' or recovery_root['variant'] != 'recoveryos'
                or recovery_root['window'] != 'OS' or len(digest) != 32
                or recovery_root['bytes'] <= 0 or recovery_root['bytes'] % 512):
            raise ValueError('requires the paired read-only recovery root contract')
        recovery_prefix = ('#include <libkern/libkern.h>\n'
            f'#define NATIVE_UFS_RECOVERY_BYTES {recovery_root["bytes"]}ULL\n'
            '#define NATIVE_UFS_RECOVERY_DIGEST {' + ','.join(str(b) for b in digest) + '}\n'
            f'#define NATIVE_UFS_RECOVERY_UUID "{identity}"\n')
        if 'isc' in recovery_root:
            isc = recovery_root['isc']
            isc_digest = bytes.fromhex(isc['sha256'])
            isc_uuid = str(uuid.UUID(isc['volume_uuid'])).upper()
            if (isc['window'] != 'ISC' or len(isc_digest) != 32 or not 0 < isc['bytes'] <= 4 << 30
                    or isc['bytes'] % 4096 or isc['volatile_overlay_limit_bytes'] != 64 << 20
                    or isc['sep_trusted_storage'] is not False):
                raise ValueError('requires the bounded owned ISC seed contract')
            recovery_prefix += (f'#define NATIVE_UFS_ISC_BYTES {isc["bytes"]}ULL\n'
                '#define NATIVE_UFS_ISC_DIGEST {' + ','.join(str(b) for b in isc_digest) + '}\n'
                f'#define NATIVE_UFS_ISC_UUID "{isc_uuid}"\n')
        recovery_prefix += f'#include "{root / "platform/sheng/macos26/storage/recovery_root.h"}"\n'
        log = json.loads((root / 'config/macoslog.json').read_text())
        validate_contract(log)
        recovery_prefix += '#define NATIVE_MACOSLOG 1\n'
        for name, value in (('FILE', uuid.UUID(log['file_uuid']).bytes),
                            ('PARTITION', uuid.UUID(log['partition_uuid']).bytes_le),
                            ('TYPE', uuid.UUID(log['partition_type']).bytes_le)):
            recovery_prefix += '#define NATIVE_MACOSLOG_' + name + '_ID {' + ','.join(map(str, value)) + '}\n'
        recovery_prefix += f'#define NATIVE_MACOSLOG_WORKER "{root / "platform/sheng/macos26/storage/macoslog_worker.h"}"\n'
    wrapper.write_text(
        '#include <TargetConditionals.h>\n'
        'static_assert(TARGET_OS_OSX == 1, "requires macOS storage ABI");\n'
        + recovery_prefix +
        f'#include "{upstream}"\n' +
        (f'#include "{display["source"]}"\n' if display else '') +
        '#include <mach/kmod.h>\n'
        + ('#include <sys/types.h>\n'
           'extern "C" int nabu_register_ram_seed(int (*)(const char *, dev_t *, char *, size_t, uint64_t *, uint64_t *, uint8_t *));\n'
           'extern "C" int nabu_ram_data_volume_seed(const char *, dev_t *, char *, size_t, uint64_t *, uint64_t *, uint8_t *);\n'
           'extern "C" int nabu_register_ram_addon(int (*)(const uint8_t **, uint64_t *));\n'
           'extern "C" int nabu_ram_gpu_addon(const uint8_t **, uint64_t *);\n'
           if transport == 'ufs' and root_mode == 'desktop' else '') +
        f'extern "C" kern_return_t {entry_prefix}_start(kmod_info_t *, void *) {{ '
        + ('if (!nabu_register_ram_seed(nabu_ram_data_volume_seed) || !nabu_register_ram_addon(nabu_ram_gpu_addon)) return KERN_FAILURE; '
           if transport == 'ufs' and root_mode == 'desktop' else '') +
        'return KERN_SUCCESS; }\n'
        f'extern "C" kern_return_t {entry_prefix}_stop(kmod_info_t *, void *) {{ return KERN_FAILURE; }}\n'
        f'KMOD_EXPLICIT_DECL({bundle}, "1.0.0", {entry_prefix}_start, {entry_prefix}_stop)\n')
    obj = destination / (component + '.o')
    arguments = compile_record['arguments']
    command, index = [], 0
    while index < len(arguments):
        arg = arguments[index]
        if arg == compile_record['file']:
            command.append(str(wrapper))
        elif arg in ('-o', '-MF', '-MT', '-MQ', '-MJ'):
            if arg == '-o':
                command += ['-o', str(obj)]
            index += 1
        elif arg == '-Xclang' and arguments[index + 1].startswith('-W'):
            index += 1
        elif arg.startswith('-W'):
            pass
        elif arg not in ('-MMD', '-MD', '-MP'):
            command.append(arg)
        index += 1
    command[1:1] = ['-I', str(destination / 'include')]
    if root_mode == 'desktop':
        command[1:1] = ['-I', str(root / 'UniXNU/kits/VerifiedRoot/include'),
                        '-I', str(root / 'UniXNU/drivers/NativeVirtioBlock')]
    if transport == 'virtio':
        command[1:1] = ['-DNATIVE_QEMU_SCRATCH_BLOCK=1']
    else:
        command[1:1] = ['-DNATIVE_UFS_ROOT_PARTITION_NAME="macosroot"']
    sdk = Path(subprocess.check_output(['xcrun', '--sdk', 'macosx', '--show-sdk-path'], text=True).strip())
    command += ['-I', str(source / 'osfmk'), '-I', str(sdk / 'usr/include'),
                '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                '-Wno-c99-designator', '-Wno-reorder-init-list',
                '-Wno-deprecated-enum-enum-conversion', '-Wno-unnecessary-virtual-specifier']
    compile_failures = []
    def compile_unit(recipe, label):
        try:
            run(recipe, label, record, cwd=Path(compile_record['directory']), env=env)
        except (subprocess.CalledProcessError, ValueError) as error:
            compile_failures.append(label + ': ' + str(error))
    compile_unit(command, directory + '-compile')
    objects = [str(obj)]
    if transport == 'ufs' and root_mode == 'desktop':
        from prepare_nabu_desktop_policy import assembly
        if not defer_desktop_policy:
            policy_source = destination / 'owner-policies.S'
            policy_source.write_text(assembly(desktop_policy))
            policy_object = destination / 'owner-policies.o'
            run(['xcrun', 'clang', '-target', architecture + '-apple-macos26.5', '-isysroot', sdk,
                 '-c', policy_source, '-o', policy_object], directory + '-owner-policy', record, env=env)
            objects.append(str(policy_object))
        for name, original in (('verified-root', root / 'UniXNU/kits/VerifiedRoot/src/verified_root.c'),
                               ('verified-crypto', root / 'UniXNU/kits/VerifiedRoot/adapters/xnu_crypto.c')):
            target = destination / (name + '.o')
            ccommand = [str(original) if arg == str(wrapper) else str(target) if arg == str(obj) else arg
                        for arg in command if arg not in ('-fapple-kext', '-fno-rtti', '-fno-exceptions',
                                                         '-xc++', '--driver-mode=g++',
                                                         '-fsized-deallocation', '-fc++-static-destructors=none')
                        and not arg.startswith(('-std=', '-stdlib='))]
            ccommand += ['-std=gnu11']
            # -x must precede the input for Clang to select C on inherited recipes.
            ccommand[1:1] = ['-x', 'c']
            compile_unit(ccommand, directory + '-' + name)
            objects.append(str(target))
        # RAM Data is a separate C++ translation unit in the same prelinked
        # module, so its real metaclass/constructor and root query are present.
        ram_checkout = root / 'UniXNU/drivers/NativeRamBlock'
        ram_source = integration / 'drivers/NativeRamBlock/NativeRamBlock.cpp'
        ram_revision = subprocess.check_output(['git', '-C', ram_checkout, 'rev-parse', 'HEAD'], text=True).strip()
        ram_text = subprocess.check_output(['git', '-C', ram_checkout, 'show', ram_revision + ':NativeRamBlock.cpp'], text=True)
        marker = '    if(logical>0x80000000ULL){'
        if ram_text.count(marker) != 1:
            raise ValueError('unreviewed RAM Data capacity-selection source')
        ram_text = ram_text.replace(marker, '''    // NABU uses bounded volatile sparse Data, including the 3 GiB seed.
    // The large direct/store-backed implementation must never activate here.
    if (logical > (4ULL << 30)) return false;
    if(false){''')
        hash_marker = '''  if(sparse.pages){
    static const uint8_t zero[4096]={};'''
        if ram_text.count(hash_marker) != 1:
            raise ValueError('unreviewed RAM seed identity source')
        ram_text = ram_text.replace(hash_marker, '''  if(sparse.pages && logical > 0x80000000ULL) {
    // Preserve the upstream SHNGSEED2 identity without hashing gigabytes of
    // omitted zero pages. This disk remains writable sparse RAM, never direct.
    uint8_t head[17] = {'S','H','N','G','S','E','E','D','2'};
    for (unsigned i = 0; i < 8; ++i) head[9 + i] = uint8_t(logical >> (8 * i));
    SHA256_Update(&context, head, sizeof(head));
    for (uint64_t page = 0; page < logical / 4096; ++page) if (sparse.pages[page]) {
      uint8_t index[8];
      for (unsigned i = 0; i < 8; ++i) index[i] = uint8_t(page >> (8 * i));
      SHA256_Update(&context, index, sizeof(index));
      SHA256_Update(&context, sparse.pages[page], 4096);
    }
    memory = nullptr;
  } else if(sparse.pages){
    static const uint8_t zero[4096]={};''')
        ram_source.write_text(ram_text)
        ram_object = destination / 'NativeRamBlock.o'
        ram_command = [str(ram_source) if arg == str(wrapper) else str(ram_object) if arg == str(obj) else arg
                       for arg in command]
        ram_command[1:1] = ['-Dnative_ram_block_volume_seed=nabu_ram_data_volume_seed',
                            '-Dnative_ram_block_gpu_addon=nabu_ram_gpu_addon']
        compile_unit(ram_command, directory + '-ram-data')
        objects.append(str(ram_object))
        ram_evidence = dict(revision=ram_revision, source_sha256=hashlib.sha256(ram_text.encode()).hexdigest(),
                            logical_limit_bytes=4 << 30, volatile=True, persistent_store_used=False,
                            seed_identity_above_2gib='SHNGSEED2', page_table_limit_bytes=(4 << 30) // 4096 * 8)
    if input_driver:
        input_object = destination / 'recovery-input/NativeRecoveryInput.o'
        input_command = [str(input_object) if arg == str(obj) else
                         input_driver['source'] if arg == str(wrapper) else arg for arg in command]
        # Kernel HID's parser conditionally includes MacTypes whenever the user SDK is visible.
        user_headers = input_command.index(str(sdk / 'usr/include'))
        if input_command[user_headers - 1] != '-I':
            raise ValueError('unexpected user SDK include option')
        del input_command[user_headers - 1:user_headers + 1]
        input_command[1:1] = ['-I', str(destination / 'recovery-input/include')]
        input_command += ['-I', str(sdk / 'System/Library/Frameworks/Kernel.framework/Versions/A/Headers')]
        run(input_command, directory + '-input-compile', record,
            cwd=Path(compile_record['directory']), env=env)
        objects.append(str(input_object))
    if compile_failures:
        raise ValueError('; '.join(compile_failures))
    binary = destination / (component + '.macho')
    run(['xcrun', 'ld', '-arch', architecture, '-kext', '-kexts_use_stubs',
         '-platform_version', 'macos', '26.5', '26.5',
         '-undefined', 'dynamic_lookup', '-fixup_chains',
         '-no_adhoc_codesign', '-headerpad', '0x1000',
         *objects, '-o', str(binary)], directory + '-link', record, env=env)
    image = macho(binary.read_bytes(), architecture)
    if (image['kind'] != 11 or (architecture == 'arm64e' and not image['subtype'] & 0x40000000)
            or not any(c == 0x80000034 for c, _, _ in image['commands'])):
        raise ValueError('standalone transport requires an ARM64E bundle with chained imports')
    info = dict(CFBundleIdentifier=bundle, CFBundleVersion='1.0.0',
                CFBundlePackageType='KEXT', CFBundleExecutable=component,
                OSBundleRequired='Root', OSBundleLibraries={
                    'com.apple.iokit.IOStorageFamily': '2.1', 'com.apple.kpi.iokit': '25.0',
                    'com.apple.kpi.libkern': '25.0', 'com.apple.kpi.mach': '25.0',
                    'com.apple.kpi.private': '25.0'},
                IOKitPersonalities={'NativeVirtioBlock': dict(CFBundleIdentifier=BUNDLE,
                    IOClass='NativeVirtioBlock', IOProviderClass='IOPlatformDevice',
                    IONameMatch='virtio,mmio', IOProbeScore=1000,
                    **{'Protocol Characteristics': dict(PROTOCOL_CHARACTERISTICS)})})
    if transport == 'ufs':
        info['IOKitPersonalities'] = {
            'NativeUfs': dict(CFBundleIdentifier=bundle, IOClass=component,
                              IOProviderClass='IOPlatformDevice', IOProbeScore=1000),
            'NativeUfsRoot': dict(CFBundleIdentifier=bundle, IOClass=component,
                                  IOProviderClass='IOPlatformExpertDevice',
                                  IOMatchCategory='NativeUfsRoot', IOProbeScore=500),
        }
        if board == 'nabu':
            info['IOKitPersonalities']['NativeUfs']['IONameMatch'] = ['qcom,sm8150-ufshc', 'qcom,ufshc']
            # The root personality has no UFS register resources on NABU.
            del info['IOKitPersonalities']['NativeUfsRoot']
        if root_mode == 'desktop':
            info['IOKitPersonalities']['NativeRamData'] = dict(CFBundleIdentifier=bundle,
                IOClass='NativeRamBlock', IOProviderClass='IOPlatformExpertDevice',
                IOMatchCategory='NativeRamBlock', IOProbeScore=500)
    if display:
        info['IOKitPersonalities']['NativeGOPDisplay'] = dict(
            CFBundleIdentifier=bundle, IOClass='NativeGOPDisplay',
            IOProviderClass='IOPlatformExpertDevice', IOMatchCategory='NativeGOPDisplay',
            IOProbeScore=500)
        info['OSBundleLibraries'].update({
            'com.apple.iokit.IOMobileGraphicsFamily': '1.0.0',
            'com.apple.iokit.IOSurface': '1.0.0', 'com.apple.iokit.IOAVFamily': '1.0.0'})
    if input_driver:
        info['IOKitPersonalities']['NativeRecoveryInput'] = dict(
            CFBundleIdentifier=bundle, IOClass='NativeRecoveryInput',
            IOProviderClass='IOPlatformDevice', IONameMatch='native-recovery-input',
            IOMatchCategory='NativeRecoveryInput', IOProbeScore=500)
        info['IOKitPersonalities']['NativeRecoveryHIDEventDriver'] = dict(
            CFBundleIdentifier='com.apple.iokit.IOHIDFamily', IOClass='IOHIDEventDriver',
            IOProviderClass='IOHIDInterface', VendorID=6900, ProductID=4178, IOProbeScore=3000)
        info['OSBundleLibraries']['com.apple.iokit.IOHIDFamily'] = '2.0.0'
    (destination / 'Info.plist').write_bytes(plistlib.dumps(info))
    evidence = dict(bundle=bundle, upstream_source_sha256=hashlib.sha256(upstream.read_bytes()).hexdigest(),
                    binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                    architecture=architecture, deployment='macOS 26.5', read_only_transport=transport == 'ufs',
                    explicit_qemu_scratch_writes=transport == 'virtio',
                    hardware_enable_required=transport == 'ufs', root_transport_read_only=True,
                    original_storage_runtime_qualified=False, hardware_admitted=False)
    if display:
        evidence['native_gop_display'] = display
    if input_driver:
        evidence['native_recovery_input'] = input_driver
    if transport == 'ufs':
        evidence.update(board=board, root_mode=root_mode, source_integration=source_evidence,
                        kernel_integrated=False, root_partition='macosroot', root_lun=0,
                        android_userdata_used=False, root_media_layout_qualified=False,
                        root_publication_sha256=hashlib.sha256((upstream.parent / 'RootPublication.h').read_bytes()).hexdigest())
    if transport == 'ufs' and root_mode == 'desktop':
        evidence.update(desktop_owner_policy=owner_policy,
                        desktop_policy_deferred=defer_desktop_policy,
                        authentication='owner-signed-blocks-and-volume-uuid',
                        recovery_root=None, macoslog=None)
        evidence['ram_data'] = ram_evidence
    if transport == 'ufs' and root_mode == 'recovery':
        evidence['macoslog'] = dict(contract=log, default_off=True,
            boot_argument='native-ufs-macoslog', read_only_mode=1, write_mode=2,
            writes_confined_to_preallocated_file=True, filesystem_metadata_writes=False,
            source_sha256={name: hashlib.sha256((root / 'platform/sheng/macos26/storage' / name).read_bytes()).hexdigest()
                           for name in ('macoslog_layout.h', 'macoslog_worker.h')})
        evidence.update(root_partition='macosroot', root_lun=0,
                        android_userdata_used=False, root_media_layout_qualified=False,
                        recovery_root=recovery_root,
                        recovery_disk_image_protocol=dict(PROTOCOL_CHARACTERISTICS),
                        recovery_root_header_sha256=hashlib.sha256((root / 'platform/sheng/macos26/storage/recovery_root.h').read_bytes()).hexdigest(),
                        root_publication_sha256=hashlib.sha256((upstream.parent / 'RootPublication.h').read_bytes()).hexdigest(),
                        recovery_transport_is_not_authentication=True)
        if 'isc' in recovery_root:
            evidence['isc_volatile_overlay'] = dict(seed_read_only=True, disk_writes=False,
                limit_bytes=64 << 20, boot_argument='native-ufs-isc-overlay=1', persistent=False,
                production_header_sha256=hashlib.sha256((upstream.parent / 'RootWindowOverlay.h').read_bytes()).hexdigest(),
                sparse_disk_header_sha256=hashlib.sha256((upstream.parent.parent / 'NativeRamBlock/SparseDisk.h').read_bytes()).hexdigest())
    (destination / 'build.json').write_text(json.dumps(evidence, indent=2) + '\n')
    record['storage_driver' if transport == 'virtio' else 'ufs_driver'] = evidence
    return destination


def nabu_ufs_contract(root: Path | None = None) -> dict:
    """Return the verified Xiaomi Pad 5 (nabu / SM8150) UFS and NABULOG2 storage contract."""
    from nabu_log_volume import CONTRACT as nabulog, validate_contract as validate_nabulog
    base = root or Path(__file__).resolve().parents[1]
    validate_nabulog(nabulog)
    baseline = json.loads((base / 'config/nabu-baseline.json').read_text())
    ufs = baseline['ufs']
    storage_dir = base / 'platform/nabu/macos26/storage'
    log_dir = base / 'platform/nabu/macos26/log'
    headers = ('NabuBoard.h', 'NabuUfsResources.h', 'NabuUfsHostSafety.h', 'NabuUfsQueue.h', 'NabuUfsService.h')
    log_headers = ('NabuLogFormat.h', 'NabuLogCore.h', 'NabuLogVolume.h', 'NabuLogUfs.h', 'NabuLogWatch.h')
    return dict(
        board='nabu',
        soc='SM8150',
        compatible=ufs['compatible'],
        hc_base=int(ufs['hc_base'], 16),
        hc_bytes=int(ufs['hc_bytes'], 16),
        hc_map_bytes=int(ufs['hc_map_bytes'], 16),
        hc_resource_bytes=int(ufs['hc_resource_bytes'], 16),
        gcc_base=int(ufs['gcc_base'], 16),
        gcc_ufs_window=int(ufs['gcc_ufs_window'], 16),
        gcc_ufs_bytes=int(ufs['gcc_ufs_bytes'], 16),
        smmu_base=int(ufs['smmu_base'], 16),
        smmu_bytes=int(ufs['smmu_bytes'], 16),
        smmu_resource_bytes=int(ufs['smmu_resource_bytes'], 16),
        stream_id=int(ufs['stream_id'], 16),
        logical_block_bytes=ufs['logical_block_bytes'],
        writes_allowed=False,
        windows=dict(
            unallocated_first_lba=ufs['unallocated_first_lba'],
            unallocated_last_lba=ufs['unallocated_last_lba'],
            os_lba=ufs['os_lba'],
            os_sectors_512=ufs['os_sectors_512'],
            systemos_lba=ufs['systemos_lba'],
            systemos_sectors_512=ufs['systemos_sectors_512'],
            boot_args=(
                'native-ufs-root=1 '
                f'native-ufs-os-lba={ufs["os_lba"]} '
                f'native-ufs-os-sectors={ufs["os_sectors_512"]} '
                f'native-ufs-systemos-lba={ufs["systemos_lba"]} '
                f'native-ufs-systemos-sectors={ufs["systemos_sectors_512"]}'
            ),
        ),
        esp_log=dict(
            contract=nabulog,
            headers_sha256={name: hashlib.sha256((log_dir / name).read_bytes()).hexdigest()
                            for name in log_headers},
        ),
        storage_headers_sha256={name: hashlib.sha256((storage_dir / name).read_bytes()).hexdigest()
                                for name in headers},
        board_patch_sha256=hashlib.sha256(
            (base / 'patches/ShengUfs/nabu-nativeufs-board.patch').read_bytes()
        ).hexdigest(),
    )
