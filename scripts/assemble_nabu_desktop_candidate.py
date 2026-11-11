#!/usr/bin/env python3
"""Assemble paired NABU 25F71 UFS/root test files without deploying them."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import uuid
from audit_adt import parse_adt
from native_devtree import make_nabu_adt, configure_native_text_state
from native_macho import macho, symbols
from prepare_nabu_desktop_policy import validate


def validate_owned_memory_size(owned_memory_size, kernel_span, packed_seed_bytes, trust_cache_bytes=0):
    if not (256 << 20) <= owned_memory_size <= (5 << 30):
        raise ValueError('owned memory request must be between 256 MiB and 5 GiB')
    if owned_memory_size % (2 << 20):
        raise ValueError('owned memory request must be 2 MiB aligned')
    if kernel_span + packed_seed_bytes + trust_cache_bytes + (2 << 20) > owned_memory_size:
        raise ValueError('loader owned window too small for kernel, packed Data, trust cache and 2 MiB headroom')
    return owned_memory_size


def assemble(kernel, prelink_report, policy, data, root, loader_build, output, *,
             owned_memory_size=2 << 30, trust_cache: Path | None = None,
             boot_addon: Path | None = None, disable_watchdogd=False):
    validate_owned_memory_size(owned_memory_size, 0, 0)
    trust_data = trust_cache.read_bytes() if trust_cache is not None else None
    trust_record = None
    if trust_data is not None:
        from prepare_nabu_trustcache import validate_bundle
        trust_record = dict(validate_bundle(trust_data), bytes=len(trust_data),
                            sha256=hashlib.sha256(trust_data).hexdigest())
    addon_data = boot_addon.read_bytes() if boot_addon is not None else None
    addon_record = None
    if addon_data is not None:
        if trust_data is None:
            raise ValueError('owner boot addon requires its paired trust cache')
        from prepare_nabu_boot_addon import validate_addon, check_trust_membership
        addon_record = validate_addon(addon_data)
        check_trust_membership(addon_record, trust_data)
    report = json.loads(prelink_report.read_text())
    raw = kernel.read_bytes()
    image = macho(raw, 'arm64')
    if disable_watchdogd and not {'_native_owner_watchdog_policy_status',
                                  '_native_owner_watchdog_spawn_blocked'} <= symbols(kernel).keys():
        raise ValueError('explicit watchdogd policy requires the kernel watchdog policy reader and spawn gate')
    storage = report.get('storage_driver', {})
    if (report.get('output_sha256') != hashlib.sha256(raw).hexdigest()
            or report.get('unresolved_sites') != 0 or report.get('loadable') is not True
            or storage.get('kernel_integrated') is not True
            or storage.get('build', {}).get('root_mode') != 'desktop'):
        raise ValueError('requires the actual owner-linked ARM64 desktop storage image')
    contract = validate(policy, '25F71')
    root_identity = uuid.UUID(next(w['volume_uuid'] for w in contract['windows'] if w['name'] == 'OS'))
    root_uuid = str(root_identity).upper()
    root_record = json.loads((root / 'build.json').read_text())
    root_image = root / 'root_image.bin'
    if (root_record.get('build') != '25F71' or root_record.get('final_image_readback_verified') is not True
            or root_image.stat().st_size != root_record['image_bytes']
            or root_record['manifest_sha256'] != contract['manifest_sha256']):
        raise ValueError('root image is not the paired fully read-back desktop image')
    seed = data.read_bytes()
    if seed[:8] != b'DBRDISK1': raise ValueError('requires packed Data')
    version, page, logical, count = struct.unpack_from('<IIQQ', seed, 8)
    if (version, page, logical) != (1, 4096, 3 << 30): raise ValueError('unpaired Data geometry')
    cursor, end = 32 + 16 * count, 0
    for i in range(count):
        offset, size = struct.unpack_from('<QQ', seed, 32 + 16 * i)
        if offset < end or size == 0 or offset % page or size % page or offset + size > logical:
            raise ValueError('invalid packed Data extent')
        cursor += size; end = offset + size
    if cursor != len(seed): raise ValueError('unbounded packed Data')
    base = min(s['va'] for s in image['segments'] if s['size'])
    extent = max(s['va'] + s['size'] for s in image['segments']) - base
    owned = validate_owned_memory_size(owned_memory_size, extent, len(seed),
                                       (len(trust_data) if trust_data is not None else 0) +
                                       (len(addon_data) if addon_data is not None else 0))
    # DarwinBoot keeps PackedRAMDisk compressed, NativeRamBlock imports its
    # bounded nonzero extents into volatile pages. Do not allocate 3 GiB here.
    output.mkdir(parents=True, exist_ok=False)
    entry = output / 'EFI/UniXNU'; entry.mkdir(parents=True)
    payload = entry / 'Payloads'; payload.mkdir()
    existing = Path(__file__).resolve().parents[1] / 'platform/nabu/macos26/esp/EFI/UniXNU'
    driver = loader_build / 'build/RELEASE_CLANGPDB/AARCH64/DarwinBoot.efi'
    loader = driver.read_bytes()
    loader_record = json.loads((loader_build / 'build.json').read_text())
    if loader_record['efi_sha256'] != hashlib.sha256(loader).hexdigest():
        raise ValueError('loader binary differs from actual build evidence')
    if not all(marker in loader for marker in (b'PackedRAMDisk', b'DBRDISK1')):
        raise ValueError('loader lacks the actual packed Data contract')
    (entry / 'Drivers').mkdir()
    shutil.copy2(driver, entry / 'Drivers/DarwinBoot.efi')
    shutil.copy2(existing / 'UniXNU.efi', entry / 'UniXNU.efi')
    shutil.copy2(kernel, payload / 'kernel.macho')
    shutil.copy2(data, payload / 'data.dbr')
    if trust_data is not None:
        (payload / 'kernel.macho.trustcache').write_bytes(trust_data)
    if addon_data is not None:
        if b'GpuAddon' not in loader:
            raise ValueError('loader lacks the boot addon memory handoff')
        (payload / 'kernel.macho.gpuaddon').write_bytes(addon_data)
    shutil.copy2(policy / 'owner-public.pem', output / 'owner-public.pem')
    (output / 'root_image.bin').symlink_to(root_image.resolve())
    adt = payload / 'kernel.macho.adt'
    make_nabu_adt(adt, owned_memory_size=owned, os_environment='main',
                  native_ufs=True, packed_ramdisk=True, trust_cache=trust_data is not None,
                  boot_addon=addon_data is not None)
    configure_native_text_state(adt, development_root=True, root_match_volume_uuid=root_identity)
    tree, parsed = parse_adt(adt.read_bytes())
    if parsed != adt.stat().st_size: raise ValueError('ADT has trailing bytes')
    ufs = tree['/device-tree/arm-io/ufs']
    expected = {'nabu,ufs-hc': struct.pack('<QQ',0x1d84000,0x2500),
                'nabu,ufs-gcc': struct.pack('<QQ',0x177000,0x1000),
                'nabu,ufs-smmu': struct.pack('<QQ',0x15000000,0x100000),
                'nabu,ufs-stream': struct.pack('<I',0x300),
                'nabu,ufs-block-bytes': struct.pack('<I',4096)}
    if any(ufs.get(k) != v for k,v in expected.items()): raise ValueError('ADT resources differ from NabuUfsResources contract')
    mm = tree['/device-tree/chosen/memory-map']
    if mm.get('PackedRAMDisk') != bytes(16) or 'RAMDisk' in mm: raise ValueError('loader packed slot mismatch')
    if trust_data is not None:
        if mm.get('TrustCache') != bytes(16): raise ValueError('loader trust cache slot mismatch')
    elif 'TrustCache' in mm:
        raise ValueError('loader trust cache slot has no sidecar')
    # A root-test candidate must expose transport/layout failures on the panic
    # panel instead of silently returning false and waiting forever for its UUID.
    args = '-v keepsyms=1 debug=0x14e cpus=1 armcpu-self-ipi=1 nabu-source-text=1 native-ufs-root=1 native-ufs-diagnostics=1 rd=uuid boot-uuid=' + root_uuid
    if disable_watchdogd:
        args += ' disable-watchdogd=1 exception_log_max_pid=255'
    if addon_data is not None:
        if mm.get('GpuAddon') != bytes(16):
            raise ValueError('boot addon sidecar/ADT slot mismatch')
        args += ' apfs-boot-shim=1'
    menu = ('version = 1\n[boot]\ntimeout = 5\ndefault = "nabu-ufs-root-test"\n'
            '[[driver]]\npath = "Drivers/DarwinBoot.efi"\n'
            '[[entry]]\nid = "nabu-ufs-root-test"\ntitle = "NABU 25F71 UFS/root test"\n'
            'type = "xnu"\npath = "Payloads/kernel.macho"\nadt = "Payloads/kernel.macho.adt"\n'
            'initrd = "Payloads/data.dbr"\nargs = "' + args + '"\n')
    (entry / 'unixnu.toml').write_text(menu)
    files = {str(p.relative_to(output)):dict(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
             for p in output.rglob('*') if p.is_file() and not p.is_symlink()}
    result = dict(schema='nabu-25F71-ufs-root-test-v1',build='25F71',files=files,
        kernel_span=extent,owned_memory_bytes=owned,packed_seed_bytes=len(seed),data_logical_bytes=logical,
        owned_memory_request_bytes=owned,
        owned_memory_contract='ADT request only; DarwinBoot must successfully AllocatePages for the complete contiguous window before passing it to XNU. Host assembly does not prove runtime ownership.',
        loader=loader_record,source_module_pointer_formats=storage['pointer_formats'],
        trust_cache=trust_record,
        boot_addon=addon_record,
        watchdog_launch_policy=dict(enabled=bool(disable_watchdogd),
            job='com.apple.watchdogd', explicit_boot_arg='disable-watchdogd=1',
            backend_available=False, crash_and_panic_policy_modified=False,
            spawn_gate_parent_pid=1, spawn_gate_path='/usr/libexec/watchdogd',
            spawn_gate_errno='ENOENT', spawn_gate_before_child_creation=True,
            policy_kind='explicit bring-up bypass', launchd_retry_possible=True),
        ufs_pointer_readback_count=len(storage['pointer_relocations']),
        external_branch_readback_count=report['branch_relocation_count'],constructors=storage['constructors'],
        esp_bundle_bytes=sum(p['bytes'] for n,p in files.items() if n.startswith('EFI/')),
        loader_preserves_packed=True,ufs_resource_contract_checked=True,boot_args=args,
        kernel_architecture='arm64',root_image=dict(path=str(root_image.resolve()), **root_record),
        root_uuid=root_uuid,root_matching='IOMedia IOPropertyMatch UUID from validated OS policy',
        systemos_uuid='C7DFE43C-0A23-43F5-B3E2-471BEAC4539D',app_uuid='F894E20A-CE25-41F4-ABE2-C00E9E922B83',
        data_uuid='0A5EED00-0000-4000-8000-000000000001',owner_contract=contract,
        hardware_executed=False,root_mount_verified=False,userspace_verified=False,desktop_verified=False,
        userspace_qualification='Unverified for this image; host assembly does not establish successful exec, dyld initialization or desktop startup.',
        deployment_performed=False,loader_binary_included=True,
        packed_slot_fill="DarwinBoot validates zero slot then writes allocated physical base and exact packed file length.",
        artifact_status="host-validated UFS/root hardware test candidate; userspace execution requires device validation")
    (output / 'candidate.json').write_text(json.dumps(result,indent=2)+'\n')
    return result

if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('kernel','prelink-report','policy','data','root','loader-build','output'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--owned-memory-size', type=lambda value: int(value, 0), default=2 << 30,
                   help='contiguous memory request in bytes (decimal or 0x hex); default 2 GiB, subject to real firmware allocation')
    p.add_argument('--trust-cache', type=Path, help='validated original trust module bundle for the loader sidecar')
    p.add_argument('--boot-addon', type=Path, help='owner boot-layout tool in the loader addon format')
    p.add_argument('--disable-watchdogd', action='store_true',
                   help='explicitly gate watchdogd through a verified root launchd policy when IOWatchdog is unavailable')
    a=p.parse_args(); print(json.dumps(assemble(a.kernel,a.prelink_report,a.policy,a.data,a.root,a.loader_build,a.output,
        owned_memory_size=a.owned_memory_size, trust_cache=a.trust_cache,
        boot_addon=a.boot_addon, disable_watchdogd=a.disable_watchdogd),indent=2))
