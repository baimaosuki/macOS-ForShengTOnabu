#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bind desktop windows to owner-signed block policies and an independent key.

The SHNGROOT digest is only transport metadata. These policies authenticate
every 4096-byte disk read and the APFS UUID using the existing VerifiedRoot
runtime. Apple code signatures and trust caches remain separate inputs.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]


def sha(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def create(spec_path: Path, image_build: Path, destination: Path,
           private_key: Path, public_key: Path) -> dict:
    spec, image = json.loads(spec_path.read_text()), json.loads(image_build.read_text())
    if (image.get('root_mode') != 'desktop' or image.get('build') not in ('25F71', '26C5035r')
            or spec.get('build') != image['build'] or spec.get('os_profile') != image['os_profile']
            or set(w['name'] for w in image['windows']) != {'OS', 'SystemOS', 'App'}):
        raise ValueError('requires the paired three-window desktop image build')
    loader = importlib.util.spec_from_file_location('owner_manifest',
        ROOT / 'UniXNU/kits/VerifiedRoot/tools/manifest.py')
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copy2(public_key, destination / 'owner-public.pem')
    entries, sizes, ids = [], set(), set()
    inputs = {w['name']: w for w in spec['windows']}
    for window in image['windows']:
        name = window['name']
        source = Path(inputs[name]['source']).resolve(strict=True)
        identity = str(uuid.UUID(inputs[name]['volume_uuid'])).upper()
        # The existing runtime chooses exactly one policy by image capacity.
        if window['size_bytes'] in sizes or identity in ids:
            raise ValueError('desktop policies require distinct image capacities and volume UUIDs')
        sizes.add(window['size_bytes'])
        ids.add(identity)
        if source.stat().st_size != window['size_bytes'] or sha(source) != window['sha256']:
            raise ValueError(name + ': source differs from the prepared image')
        with source.open('rb') as stream:
            signed = module.make(stream, source.stat().st_size, private_key, public_key,
                                 identity, 1, destination / name)
        entries.append(dict(name=name, bytes=window['size_bytes'], sha256=window['sha256'],
                            volume_uuid=identity, policy_sha256=sha(destination / name / 'manifest.bin')))
    shutil.copy2(destination / 'OS/trusted-key.bin', destination / 'trusted-key.bin')
    contract = dict(schema='nabu-desktop-owner-policy-v1', build=image['build'],
                    os_profile=image['os_profile'], root_mode='desktop', windows=entries,
                    manifest_sha256=image['manifest_sha256'],
                    trusted_key_sha256=sha(destination / 'trusted-key.bin'),
                    authentication='owner-signed-blocks-and-volume-uuid',
                    apple_code_signatures_authenticated=False, hardware_qualified=False)
    (destination / 'contract.json').write_text(json.dumps(contract, indent=2) + '\n')
    return contract


def validate(directory: Path, expected_build: str) -> dict:
    contract = json.loads((directory / 'contract.json').read_text())
    if (contract.get('schema') != 'nabu-desktop-owner-policy-v1'
            or contract.get('build') != expected_build or contract.get('root_mode') != 'desktop'
            or len(contract.get('windows', [])) != 3
            or {w['name'] for w in contract['windows']} != {'OS', 'SystemOS', 'App'}):
        raise ValueError('desktop policy differs from the paired OS build/window set')
    loader = importlib.util.spec_from_file_location('owner_manifest',
        ROOT / 'UniXNU/kits/VerifiedRoot/tools/manifest.py')
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    key = (directory / 'trusted-key.bin').read_bytes()
    if (key != module.public_anchor(directory / 'owner-public.pem')
            or hashlib.sha256(key).hexdigest() != contract['trusted_key_sha256']):
        raise ValueError('desktop policy anchor differs from the independent public key')
    sizes, ids = set(), set()
    for window in contract['windows']:
        folder = directory / window['name']
        envelope = (folder / 'manifest.bin').read_bytes()
        if sha(folder / 'manifest.bin') != window['policy_sha256'] or len(envelope) < 544:
            raise ValueError('desktop policy envelope digest/length differs')
        magic, version, header, block, flags, size, count = struct.unpack_from('<8s4I2Q', envelope)
        identity = str(uuid.UUID(bytes=envelope[40:56])).upper()
        if (magic != b'VROOT01\0' or (version, header, block, flags) != (1, 160, 4096, 0)
                or size != window['bytes'] or size <= 0 or size % 512
                or count != (size + 4095) // 4096 or len(envelope) != 544 + count * 32
                or identity != window['volume_uuid'] or size in sizes or identity in ids
                or envelope[56:88].hex() != window['sha256']
                or hashlib.sha256(envelope[544:]).digest() != envelope[88:120]
                or hashlib.sha256(key).digest() != envelope[120:152]
                or struct.unpack_from('<Q', envelope, 152)[0] < 1):
            raise ValueError('desktop policy signed image/UUID/hash table differs')
        sizes.add(size)
        ids.add(identity)
        # Verify the envelope itself; do not trust generator booleans.
        if ((folder / 'header.bin').read_bytes() != envelope[:160]
                or (folder / 'signature.bin').read_bytes() != envelope[160:544]):
            raise ValueError('desktop policy detached signature/header differs')
        subprocess.run(['openssl', 'dgst', '-sha256', '-verify', str(directory / 'owner-public.pem'),
                        '-signature', str(folder / 'signature.bin'), str(folder / 'header.bin')],
                       check=True, capture_output=True)
    return contract


def assembly(directory: Path) -> str:
    """Emit the existing NativeVerifiedBlockPolicy ABI; no private key is embedded."""
    def incbin(path):
        path = str(path.resolve(strict=True))
        if any(c in path for c in ('"', '\\', '\n', '\r')):
            raise ValueError('policy path cannot be encoded in assembly')
        return '.incbin "' + path + '"\n'
    text = '.section __DATA_CONST,__const\n.balign 8\n.globl _verified_root_key\n_verified_root_key:\n'
    text += incbin(directory / 'trusted-key.bin')
    for index, name in enumerate(('OS', 'SystemOS', 'App')):
        text += f'.balign 8\nLpolicy{index}:\n' + incbin(directory / name / 'manifest.bin')
    text += '.balign 8\n.globl _verified_root_policies, _verified_root_policy_count\n_verified_root_policies:\n'
    for index, name in enumerate(('OS', 'SystemOS', 'App')):
        text += f'.quad Lpolicy{index}, {(directory / name / "manifest.bin").stat().st_size}\n'
    return text + '_verified_root_policy_count:\n.quad 3\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--image-build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--private-key', type=Path, required=True)
    parser.add_argument('--public-key', type=Path, required=True)
    args = parser.parse_args()
    create(args.spec, args.image_build, args.output, args.private_key, args.public_key)
    print(json.dumps(validate(args.output, json.loads(args.image_build.read_text())['build']), indent=2))
