#!/usr/bin/env python3
"""Assemble paired NABU 25F71 UFS/root test files without deploying them."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
from audit_adt import parse_adt
from native_devtree import make_nabu_adt, configure_native_text_state
from native_macho import macho
from prepare_nabu_desktop_policy import validate


def assemble(kernel, prelink_report, policy, data, root, output):
    report = json.loads(prelink_report.read_text())
    raw = kernel.read_bytes()
    image = macho(raw, 'arm64')
    storage = report.get('storage_driver', {})
    if (report.get('output_sha256') != hashlib.sha256(raw).hexdigest()
            or report.get('unresolved_sites') != 0 or report.get('loadable') is not True
            or storage.get('kernel_integrated') is not True
            or storage.get('build', {}).get('root_mode') != 'desktop'):
        raise ValueError('requires the actual owner-linked ARM64 desktop storage image')
    contract = validate(policy, '25F71')
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
    owned = 512 << 20
    # DarwinBoot keeps PackedRAMDisk compressed, NativeRamBlock imports its
    # bounded nonzero extents into volatile pages. Do not allocate 3 GiB here.
    if extent + len(seed) + (2 << 20) > owned: raise ValueError('loader owned window too small')
    output.mkdir(parents=True, exist_ok=False)
    payload = output / 'Payloads'; payload.mkdir()
    shutil.copy2(kernel, payload / 'kernel.macho')
    shutil.copy2(data, payload / 'data.dbr')
    shutil.copy2(policy / 'owner-public.pem', output / 'owner-public.pem')
    (output / 'root_image.bin').symlink_to(root_image.resolve())
    adt = payload / 'kernel.macho.adt'
    make_nabu_adt(adt, owned_memory_size=owned, os_environment='main',
                  native_ufs=True, packed_ramdisk=True)
    configure_native_text_state(adt, development_root=True)
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
    args = '-v keepsyms=1 debug=0x14e cpus=1 armcpu-self-ipi=1 native-ufs-root=1 rd=uuid boot-uuid=47719F50-5527-44A3-9F59-8170528D8C14'
    menu = ('version = 1\n[boot]\ntimeout = 5\ndefault = "nabu-ufs-root-test"\n'
            '[[driver]]\npath = "Drivers/DarwinBoot.efi"\n'
            '[[entry]]\nid = "nabu-ufs-root-test"\ntitle = "NABU 25F71 UFS/root test"\n'
            'type = "xnu"\npath = "Payloads/kernel.macho"\nadt = "Payloads/kernel.macho.adt"\n'
            'initrd = "Payloads/data.dbr"\nargs = "' + args + '"\n')
    (output / 'unixnu.toml').write_text(menu)
    files = {str(p.relative_to(output)):dict(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
             for p in output.rglob('*') if p.is_file() and not p.is_symlink()}
    result = dict(schema='nabu-25F71-ufs-root-test-v1',build='25F71',files=files,
        kernel_span=extent,owned_memory_bytes=owned,packed_seed_bytes=len(seed),data_logical_bytes=logical,
        loader_preserves_packed=True,ufs_resource_contract_checked=True,boot_args=args,
        kernel_architecture='arm64',root_image=dict(path=str(root_image.resolve()), **root_record),
        root_uuid='47719F50-5527-44A3-9F59-8170528D8C14',
        systemos_uuid='C7DFE43C-0A23-43F5-B3E2-471BEAC4539D',app_uuid='F894E20A-CE25-41F4-ABE2-C00E9E922B83',
        data_uuid='0A5EED00-0000-4000-8000-000000000001',owner_contract=contract,
        hardware_executed=False,root_mount_verified=False,userspace_verified=False,desktop_verified=False,
        expected_userspace_blocker='Original arm64e launchd is rejected by the ARM64 CPU grade (EBADARCH).',
        deployment_performed=False,loader_binary_included=False)
    (output / 'candidate.json').write_text(json.dumps(result,indent=2)+'\n')
    return result

if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('kernel','prelink-report','policy','data','root','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args(); print(json.dumps(assemble(a.kernel,a.prelink_report,a.policy,a.data,a.root,a.output),indent=2))
