#!/usr/bin/env python3
"""Bundle unchanged, manifest-verified 25F71 trust modules for DarwinBoot."""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import struct
import uuid
import zipfile

COMPONENTS = ('StaticTrustCache', 'Cryptex1,SystemTrustCache', 'Cryptex1,AppTrustCache')


def im4p_payload(data):
    def tlv(at, bound):
        if at + 2 > bound:
            raise ValueError('truncated DER header')
        tag, length = data[at:at + 2]
        at += 2
        if length & 128:
            count = length & 127
            if not 1 <= count <= 4 or at + count > bound or data[at] == 0:
                raise ValueError('invalid DER length')
            length = int.from_bytes(data[at:at + count], 'big')
            at += count
            if length < 128:
                raise ValueError('noncanonical DER length')
        if at + length > bound:
            raise ValueError('DER object exceeds its container')
        return tag, at, at + length

    tag, at, end = tlv(0, len(data))
    if tag != 0x30 or end != len(data):
        raise ValueError('expected one complete IM4P sequence')
    fields = []
    while at < end:
        tag, start, finish = tlv(at, end)
        fields.append((tag, data[start:finish]))
        at = finish
    if (len(fields) < 4 or fields[0] != (0x16, b'IM4P') or
            fields[1][0] != 0x16 or fields[1][1] not in (b'trst', b'trcs', b'trca') or
            fields[2][0] != 0x16 or fields[3][0] != 4):
        raise ValueError('unexpected trust-cache IM4P fields')
    return fields[3][1]


def validate_module(data):
    if len(data) < 24:
        raise ValueError('truncated trust-cache module')
    version, identity, count = struct.unpack_from('<I16sI', data)
    if version != 2 or len(data) != 24 + 24 * count:
        raise ValueError('requires a complete version-2 trust-cache module')
    previous = None
    for index in range(count):
        entry = data[24 + 24 * index:48 + 24 * index]
        if entry[20] not in (1, 2, 3, 4):
            raise ValueError('unsupported trust-cache hash type')
        if previous is not None and previous >= entry[:20]:
            raise ValueError('trust-cache hashes must be strictly increasing')
        previous = entry[:20]
    return dict(version=version, uuid=str(uuid.UUID(bytes=identity)).upper(),
                entries=count, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def validate_bundle(data):
    if not 8 <= len(data) <= 16 << 20:
        raise ValueError('trust bundle must fit the loader 16 MiB bound')
    count, = struct.unpack_from('<I', data)
    if not 1 <= count <= 128 or 4 + 4 * count > len(data):
        raise ValueError('invalid trust-cache offset table')
    offsets = struct.unpack_from('<' + 'I' * count, data, 4)
    if offsets[0] != 4 + 4 * count:
        raise ValueError('unexpected gap before the first trust module')
    modules = []
    for start, end in zip(offsets, (*offsets[1:], len(data))):
        if start % 4 or not 4 + 4 * count <= start < end <= len(data):
            raise ValueError('invalid trust-cache module extent')
        modules.append(dict(offset=start, **validate_module(data[start:end])))
    return dict(module_count=count, modules=modules, bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest())


def verify_macho_membership(path, modules):
    # CodeDirectory digest fields in the diagnostic dumper may be the full
    # digest. Trust-cache entries always carry its first 20 bytes (CDHash).
    from codesign_dump import dump
    result = dump(path)
    matched = []
    for cd in result['code_directories']:
        digest = bytes.fromhex(cd['cdhash'])[:20]
        for index, module in enumerate(modules):
            count, = struct.unpack_from('<I', module, 20)
            for entry in range(count):
                at = 24 + 24 * entry
                if module[at:at + 20] == digest and module[at + 20] == cd['hash_type']:
                    matched.append(dict(module=index, entry=entry, cdhash=digest.hex(),
                                        hash_type=cd['hash_type'], identifier=cd['identifier']))
    if not matched:
        raise ValueError(f'{path}: no CodeDirectory CDHash in the original trust modules')
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(), matches=matched)


def prepare(ipsw, output, config, verify_machos=()):
    cfg = json.loads(config.read_text())
    if cfg['build'] != '25F71' or cfg['version'] != '26.5':
        raise ValueError('requires the NABU macOS 26.5 / 25F71 profile')
    output.mkdir(parents=True, exist_ok=False)
    records, modules = [], []
    with zipfile.ZipFile(ipsw) as archive:
        manifest = plistlib.loads(archive.read('BuildManifest.plist'))
        if manifest['ProductBuildVersion'] != cfg['build']:
            raise ValueError('IPSW build does not match the configured root')
        identities = [x for x in manifest['BuildIdentities']
                      if x.get('Info', {}).get('DeviceClass') == cfg['device_class'] and
                      x['Info'].get('Variant') == 'Customer Erase Install (IPSW)']
        if len(identities) != 1:
            raise ValueError('requires one matching erase-install BuildIdentity')
        identity = identities[0]
        for name in COMPONENTS:
            item = identity['Manifest'][name]
            member = item['Info']['Path']
            raw = archive.read(member)  # ZipFile also verifies the member CRC.
            method = {20: 'sha1', 32: 'sha256', 48: 'sha384'}.get(len(item['Digest']))
            if method is None or hashlib.new(method, raw).digest() != item['Digest']:
                raise ValueError('IPSW BuildManifest digest mismatch: ' + name)
            module = im4p_payload(raw)
            record = validate_module(module)
            filename = name.replace(',', '-') + '.bin'
            (output / filename).write_bytes(module)
            modules.append(module)
            records.append(dict(component=name, member=member, file=filename,
                                manifest_hash=method, manifest_digest=item['Digest'].hex(),
                                im4p_sha256=hashlib.sha256(raw).hexdigest(), **record))
    offset = 4 + 4 * len(modules)
    offsets = []
    for module in modules:
        offsets.append(offset)
        offset += len(module)
    bundle = struct.pack('<I' + 'I' * len(offsets), len(offsets), *offsets) + b''.join(modules)
    validated = validate_bundle(bundle)
    matched = [verify_macho_membership(p, modules) for p in verify_machos]
    (output / 'kernel.macho.trustcache').write_bytes(bundle)
    report = dict(build=cfg['build'], device_class=cfg['device_class'], ipsw=str(ipsw.resolve()),
                  original_modules_unchanged=True, signatures_rewritten=False,
                  order=list(COMPONENTS), source_modules=records,
                  membership=matched, bundle=validated, device_tested=False)
    (output / 'build.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ipsw', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1] / 'config/macos26.json')
    parser.add_argument('--verify-macho', type=Path, action='append', default=[])
    args = parser.parse_args()
    print(json.dumps(prepare(args.ipsw, args.output, args.config, args.verify_macho), indent=2))
