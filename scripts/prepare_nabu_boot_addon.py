#!/usr/bin/env python3
"""Pack the owner boot-layout tool and append its explicit trust module."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import uuid
import zlib
from codesign_dump import dump
from prepare_nabu_trustcache import validate_bundle


def validate_addon(data):
    if not 24 <= len(data) <= 64 << 20 or data[:8] != b'NGPUADD2':
        raise ValueError('invalid boot addon header')
    count, reserved, total = struct.unpack_from('<IIQ', data, 8)
    if count != 1 or reserved or total != len(data):
        raise ValueError('boot addon requires exactly the owner apfs_boot_util')
    namesize, mode, size, packed = struct.unpack_from('<IIQQ', data, 24)
    if not 2 <= namesize <= 160 or mode != 0o755 or not 0 < size <= 1 << 20:
        raise ValueError('invalid boot addon file metadata')
    at = 48
    name = data[at:at + namesize]
    if name != b'bootshim/apfs_boot_util\0':
        raise ValueError('unrecognized boot addon path')
    at += (namesize + 7) & ~7
    if not packed or at + ((packed + 7) & ~7) != len(data):
        raise ValueError('invalid boot addon compressed extent')
    stream = zlib.decompressobj()
    payload = stream.decompress(data[at:at + packed], size + 1)
    if len(payload) != size or not stream.eof or stream.unused_data or stream.unconsumed_tail:
        raise ValueError('invalid boot addon compression stream')
    with tempfile.TemporaryDirectory(prefix='nabu-boot-signature-') as directory:
        binary = Path(directory) / 'apfs_boot_util'
        binary.write_bytes(payload)
        signature = dump(binary)
        from verify_page_hashes import verify
        pages = verify(str(binary))
    if (signature['cputype'] != '0x100000c' or signature['subtype'] != '0x0' or
            not pages.get('ok') or not signature['code_directories']):
        raise ValueError('boot addon tool must be signed ARM64 with intact pages')
    return dict(files=1, path=name[:-1].decode(), mode=mode, file_bytes=size,
                file_sha256=hashlib.sha256(payload).hexdigest(), bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                cdhashes=[dict(cdhash=cd['cdhash'][:40], hash_type=cd['hash_type'])
                          for cd in signature['code_directories']])


def check_trust_membership(addon_record, trust):
    validated = validate_bundle(trust)
    for cd in addon_record['cdhashes']:
        wanted = bytes.fromhex(cd['cdhash'])
        for module in validated['modules']:
            start = module['offset'] + 24
            if any(trust[at:at + 20] == wanted and trust[at + 20] == cd['hash_type']
                   for at in range(start, start + module['entries'] * 24, 24)):
                return
    raise ValueError('boot addon signature is absent from the paired trust cache')


def prepare(shim_dir, original, output):
    binary = shim_dir / 'apfs_boot_util'
    raw = binary.read_bytes()
    record = json.loads((shim_dir / 'build.json').read_text())
    digest = hashlib.sha256(raw).hexdigest()
    # The build record is evidence alongside, not a replacement for parsing
    # the actual embedded CodeDirectory used by AMFI.
    if record.get('sha256', record.get('binary_sha256')) != digest:
        raise ValueError('shim differs from its compiled build record')
    signature = dump(binary)
    directories = signature['code_directories']
    if signature['cputype'] != '0x100000c' or signature['subtype'] != '0x0' or not directories:
        raise ValueError('requires a signed ordinary ARM64 owner tool')
    entries = sorted(set(bytes.fromhex(cd['cdhash'])[:20] + bytes((cd['hash_type'], 0, 0, 0))
                         for cd in directories))
    identity = uuid.uuid5(uuid.NAMESPACE_URL, 'nabu-25F71-boot-layout:' + digest)
    module = struct.pack('<I16sI', 2, identity.bytes, len(entries)) + b''.join(entries)
    source = original.read_bytes()
    validated = validate_bundle(source)
    count = validated['module_count']
    if count != 3:
        raise ValueError('requires the three original 25F71 static/system/app modules')
    old_offsets = struct.unpack_from('<3I', source, 4)
    modules = [source[start:end] for start, end in zip(old_offsets, (*old_offsets[1:], len(source)))]
    modules.append(module)
    offsets, at = [], 4 + 4 * len(modules)
    for item in modules:
        offsets.append(at)
        at += len(item)
    trust = struct.pack('<5I', 4, *offsets) + b''.join(modules)
    trusted = validate_bundle(trust)
    assert [m['sha256'] for m in trusted['modules'][:3]] == [m['sha256'] for m in validated['modules']]
    name = b'bootshim/apfs_boot_util\0'
    compressed = zlib.compress(raw, 9)
    body = (struct.pack('<IIQQ', len(name), 0o755, len(raw), len(compressed)) +
            name + bytes(-len(name) % 8) + compressed + bytes(-len(compressed) % 8))
    addon = b'NGPUADD2' + struct.pack('<IIQ', 1, 0, 24 + len(body)) + body
    checked = validate_addon(addon)
    check_trust_membership(checked, trust)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'kernel.macho.gpuaddon').write_bytes(addon)
    (output / 'kernel.macho.trustcache').write_bytes(trust)
    report = dict(build='25F71', shim=record, addon=checked, trust=trusted,
                  original_modules_unchanged=True, owner_cdhashes=[e[:20].hex() for e in entries],
                  gpu_driver_included=False, device_tested=False)
    (output / 'build.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--shim-dir', type=Path, required=True)
    p.add_argument('--original-trust', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(prepare(a.shim_dir, a.original_trust, a.output), indent=2))
