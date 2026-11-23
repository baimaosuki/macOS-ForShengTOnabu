#!/usr/bin/env python3
"""Sign converted APFS windows, pack SHNGROOT and emit the prelink evidence.

Requires actual successful volume-write, userland and independent cache
verification reports. This tool never fabricates complete=true for an original
unconverted ROOT. It writes host files only and does not flash a device.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys
import uuid
from nabu_root27 import WINDOWS, converted_text_fixups, pack_root, save, sha

ROOT = Path(__file__).resolve().parents[3]
ABI = 'zeta-ordinary-v1'
PIN = json.loads((Path(__file__).with_name('zeta_pac') / 'UPSTREAM.json').read_text())['commit']


def trust_bundle(original, entries):
    count = struct.unpack_from('<I', original)[0]
    if not 0 < count <= 128 or len(original) < 4 + 4 * count:
        raise ValueError('Invalid base trust bundle')
    starts = list(struct.unpack_from('<' + 'I' * count, original, 4)) + [len(original)]
    if starts[0] != 4 + 4 * count or any(a >= b for a, b in zip(starts, starts[1:])):
        raise ValueError('Invalid base trust offsets')
    modules = [original[a:b] for a, b in zip(starts, starts[1:])]
    for module in modules:
        if len(module) < 24 or struct.unpack_from('<I', module)[0] != 2:
            raise ValueError('Require version-2 trust modules')
        size = struct.unpack_from('<I', module, 20)[0]
        if len(module) != 24 + 24 * size:
            raise ValueError('Truncated trust module')
    records = {}
    for entry in entries:
        key = bytes.fromhex(entry['cdhash'])
        value = bytes((entry['hash_type'], entry['flags'], entry['constraint_category'], 0))
        if len(key) != 20 or key in records and records[key] != value:
            raise ValueError('Invalid/conflicting rewritten cdhash')
        records[key] = value
    if not records:
        raise ValueError('No rewritten executable trust entries')
    body = b''.join(key + records[key] for key in sorted(records))
    ident = uuid.uuid5(uuid.NAMESPACE_URL, 'org.unixnu.nabu.zeta:' + hashlib.sha256(body).hexdigest())
    modules.append(struct.pack('<I16sI', 2, ident.bytes, len(records)) + body)
    offsets, cursor = [], 4 + 4 * len(modules)
    for module in modules:
        offsets.append(cursor)
        cursor += len(module)
    return struct.pack('<I', len(modules)) + struct.pack('<' + 'I' * len(modules), *offsets) + b''.join(modules)


def conversion_evidence(windows, reports, cache_report, cache_verification):
    if not cache_verification.get('passed') or not cache_verification.get('all_code_pages_hashed'):
        raise ValueError('Independent cache signature/word verification is required')
    if cache_report.get('abi') != ABI or cache_report.get('upstream_commit') != PIN:
        raise ValueError('Cache rewrite ABI/source mismatch')
    entries, written, evidence = [], {}, {}
    for name in WINDOWS:
        tree = json.loads((reports / f'{name}-tree.json').read_text())
        write = json.loads((reports / f'{name}-write.json').read_text())
        if (tree.get('abi') != ABI or tree.get('upstream_commit') != PIN or not tree.get('complete')
                or tree.get('failures') or tree.get('inventory_errors')):
            raise ValueError(f'{name}: incomplete userland conversion')
        if (not write.get('complete') or not write.get('clean_unmount')
                or write.get('container_sha256') != sha(windows[name])
                or Path(write['container']).resolve() != windows[name].resolve()):
            raise ValueError(f'{name}: volume readback/hash binding is incomplete')
        hashes = write.get('file_hashes', {})
        if len(hashes) != write.get('files') or len(hashes) != write.get('verified_hashes'):
            raise ValueError(f'{name}: incomplete APFS file readback inventory')
        for item in tree['files']:
            if any(hashes.get(path) != item['sha256'] for path in item['paths']):
                raise ValueError(f'{name}: rewritten program was not read back through APFS')
            entries.append(item['trust_cache_entry'])
        written[name] = hashes
        evidence[name] = dict(tree_report_sha256=sha(reports / f'{name}-tree.json'),
                              write_report_sha256=sha(reports / f'{name}-write.json'))
    for item in cache_report['files']:
        # Bind every rewritten subcache to the readback at its real pathname.
        source = Path(cache_report['output']) / item['name']
        if sha(source) != item['sha256']:
            raise ValueError('Rewritten cache file differs from its report')
        if not any(Path(path).name == item['name'] and digest == item['sha256']
                   for hashes in written.values() for path, digest in hashes.items()):
            raise ValueError('Rewritten cache missing from APFS readback')
        entries.append(item['trust_cache_entry'])
    if cache_verification.get('files') != len(cache_report['files']):
        raise ValueError('Independent cache verification inventory mismatch')
    return entries, evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--volumes', type=Path, required=True, help='OS.raw, SystemOS.raw, App.raw')
    parser.add_argument('--reports', type=Path, required=True, help='NAME-tree.json and NAME-write.json')
    parser.add_argument('--cache-report', type=Path, required=True)
    parser.add_argument('--cache-verification', type=Path, required=True)
    parser.add_argument('--base-trust-bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New output directory; contains private owner key')
    parser.add_argument('--openssl-prefix', type=Path, default=Path('/usr'))
    parser.add_argument('--cc', default='cc')
    parser.add_argument('--data-gib', type=int, default=8, help='Preserved Data seed size; keep tail space outside ROOT')
    args = parser.parse_args()
    out = args.output.resolve()
    if out.exists():
        raise FileExistsError('Output directory already exists')
    windows = {name: args.volumes.resolve() / (name + '.raw') for name in WINDOWS}
    cache = json.loads(args.cache_report.read_text())
    independent = json.loads(args.cache_verification.read_text())
    if independent.get('cache_report_sha256') != sha(args.cache_report):
        raise ValueError('Independent cache verification belongs to a different rewrite report')
    entries, evidence = conversion_evidence(windows, args.reports, cache,
                                           independent)
    import importlib.util
    sys.path.insert(0, str(ROOT / 'scripts'))
    from unixnu import cli
    from unixnu.config import Context
    ctx = Context(cli.parser().parse_args(['status', '-D', str(ROOT / 'devices/nabu'), '--os', 'macos-27']))
    kit = ROOT / 'kits/VerifiedRoot'
    if not (kit / 'tools/manifest.py').is_file():
        raise ValueError('Initialize the pinned kits/VerifiedRoot submodule')
    out.mkdir(parents=True, mode=0o700)
    owner = out / 'owner.pem'
    fd = os.open(owner, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:3072',
                    '-out', str(owner)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    public = out / 'owner-public.pem'
    subprocess.run(['openssl', 'pkey', '-in', str(owner), '-pubout', '-out', str(public)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    library = out / 'verified-root.dylib'
    subprocess.run([args.cc, '-std=c11', '-Wall', '-Wextra', '-Werror',
                    *(['-dynamiclib'] if platform.system() == 'Darwin' else ['-shared', '-fPIC']),
                    '-I', str(kit / 'include'), '-I', str(args.openssl_prefix / 'include'),
                    str(kit / 'src/verified_root.c'), str(kit / 'tests/openssl_provider.c'),
                    '-L', str(args.openssl_prefix / 'lib'), '-lcrypto', '-o', str(library)], check=True)
    policies, tables, pins, headers = out / 'policies', {}, {}, []
    for name in WINDOWS:
        identity = ctx.board['root'][name.lower() + '_volume_uuid']
        sys.path.insert(0, str(Path(__file__).with_name('zeta_pac')))
        from zeta_apfs_image import Container
        with windows[name].open('rb') as stream:
            volumes = list(Container(stream).volumes())
            if len(volumes) != 1 or str(uuid.UUID(bytes=volumes[0].superblock[240:256])).upper() != identity:
                raise ValueError(f'{name}: actual APFS volume UUID differs from the selected profile')
        destination = policies / name
        subprocess.run([sys.executable, str(kit / 'tools/manifest.py'), '--image', str(windows[name]),
                        '--private-key', str(owner), '--public-key', str(public), '--volume-uuid', identity,
                        '--generation', '1', '--output', str(destination)], check=True)
        subprocess.run([sys.executable, str(kit / 'tools/verify_image.py'), '--library', str(library),
                        '--manifest', str(destination / 'manifest.bin'), '--trusted-key', str(destination / 'trusted-key.bin'),
                        '--image', str(windows[name]), '--volume-uuid', identity,
                        '--minimum-generation', '1', '--output', str(destination / 'verification.json')], check=True)
        manifest = (destination / 'manifest.bin').read_bytes()
        (destination / 'manifest.header.bin').write_bytes(manifest[:544])
        tables[name] = destination / 'hashes.bin'
        headers.append(manifest[:544])
        pins[name.lower() + '_volume_uuid'] = identity
        pins[name.lower() + '_image_sha256'] = sha(windows[name])
    # Reuse the storage driver's exact handoff layout.
    offset = 32 + 16 * len(headers)
    body = b''.join(struct.pack('<QQ', offset + i * 544, 544) for i in range(len(headers))) + b''.join(headers)
    (out / 'root-policies.bin').write_bytes(struct.pack('<8sQQQ', b'VROOTB01', 1, len(headers), 32 + len(body)) + body)
    (out / 'owner-zeta.trustcache').write_bytes(trust_bundle(args.base_trust_bundle.read_bytes(), entries))
    image = out / 'macosroot-27.3-26C5035r-zeta-pac.img'
    packed = pack_root(image, windows, tables)
    capacity = ctx.board['userdata']
    partition_bytes = (capacity['last_lba'] - capacity['first_lba'] + 1) * capacity['lba_bytes']
    # NativeUfs places the persistent overlay below the default 1GiB log
    # reserve, and requires a 4MiB guard above the SHNGROOT image.
    if args.data_gib <= 0 or packed['image_bytes'] + (args.data_gib + 1) * (1 << 30) + (4 << 20) > partition_bytes:
        raise ValueError('ROOT leaves insufficient partition space for Data, log reserve and guard')
    local = '[root]\n' + ''.join(f'{key} = "{value}"\n' for key, value in pins.items())
    fixups = converted_text_fixups(ctx.board['payload'].get('text_fixups', []), cache)
    local += '\n[payload]\ntext_fixups = ' + json.dumps(fixups) + '\n'
    (out / 'root.local.toml').write_text(local)
    report = dict(schema='nabu-zeta-runtime-v1', abi=ABI, upstream_commit=PIN, complete=True,
                  root_image=str(image), root_image_sha256=packed['image_sha256'],
                  policies=str(policies), trust_bundle=str(out / 'owner-zeta.trustcache'),
                  conversion=evidence, cache_report_sha256=sha(args.cache_report),
                  cache_verification_sha256=sha(args.cache_verification), packed=packed,
                  hardware_tested=False, host_only=True, fresh_system_reproduction_verified=False,
                  scope='PAC conversion/input assembly; ACL/xattr and complete system boot are separate acceptance checks')
    save(out / 'runtime-manifest.json', report)
    print('ROOT, matching owner policies/trust and prelink report written; hardware not tested')


if __name__ == '__main__':
    main()
