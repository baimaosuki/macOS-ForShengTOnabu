#!/usr/bin/env python3
"""Extract/decrypt the selected 26C5035r inputs and decode original raw disks.

Uses the existing IPSW identity/pin checks. AEA decoding needs macOS; inputs
may be a local IPSW or the OS profile's Apple URL. Nothing is flashed/mounted
read-write and an existing output is never replaced.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
from unixnu import cli, ipsw, userdata
from unixnu.config import Context
from nabu_root27 import WINDOWS, save, sha


def apfs_offset(path):
    with path.open('rb') as stream:
        first = stream.read(4096)
        if first[32:36] == b'NXSB':
            return 0
        for sector in (512, 4096):
            stream.seek(sector)
            header = bytearray(stream.read(sector))
            if header[:8] != b'EFI PART':
                continue
            length, expected = struct.unpack_from('<II', header, 12)
            if not 92 <= length <= sector:
                raise ValueError('Invalid original GPT header size')
            header[16:20] = bytes(4)
            if zlib.crc32(header[:length]) & 0xffffffff != expected:
                raise ValueError('Original GPT checksum mismatch')
            table_lba, count, width, crc = struct.unpack_from('<QIII', header, 72)
            if not 1 <= count <= 4096 or width < 128 or width * count > 16 << 20:
                raise ValueError('Invalid original GPT table')
            stream.seek(table_lba * sector)
            table = stream.read(count * width)
            if zlib.crc32(table) & 0xffffffff != crc:
                raise ValueError('Original GPT entries checksum mismatch')
            offsets = []
            for at in range(0, len(table), width):
                kind = uuid.UUID(bytes_le=table[at:at + 16])
                if str(kind).upper() == '7C3457EF-0000-11AA-AA11-00306543ECAC':
                    offset = struct.unpack_from('<Q', table, at + 32)[0] * sector
                    stream.seek(offset + 32)
                    if stream.read(4) != b'NXSB':
                        raise ValueError('Original APFS partition lacks NXSB')
                    offsets.append(offset)
            if len(offsets) != 1:
                raise ValueError('Require exactly one original APFS container')
            return offsets[0]
    raise ValueError('No raw APFS container or supported GPT')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ipsw', help='local IPSW or URL; omit for the pinned Apple URL')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    if out.exists():
        raise FileExistsError('Use a new input inventory output directory')
    command = ['ipsw', 'info', '-D', str(ROOT / 'devices/nabu'), '--os', 'macos-27']
    if args.ipsw:
        command += ['--ipsw', args.ipsw]
    ctx = Context(cli.parser().parse_args(command))
    source = ipsw.Ipsw(ctx)
    out.mkdir(parents=True)
    result = dict(build='26C5035r', windows={}, trust_caches=[])
    for window in ctx.board['userdata']['windows']:
        name = window['name']
        image = source.disk_image(window['component'])
        expected = ctx.board['root'][name.lower() + '_image_sha256']
        if sha(image) != expected:
            raise ValueError(f'{name}: original decrypted image differs from profile')
        raw = out / (name + '.original.raw')
        size = userdata.image_size(image)
        with raw.open('xb') as target:
            for data in userdata.disk_blocks(image, size):
                target.write(data)
        if raw.stat().st_size != size:
            raise ValueError('Short original raw disk extraction')
        result['windows'][name] = dict(image=str(image), image_sha256=expected,
                                       raw=str(raw), raw_sha256=sha(raw), bytes=size,
                                       apfs_offset=apfs_offset(raw),
                                       volume_uuid=ctx.board['root'][name.lower() + '_volume_uuid'])
    if tuple(result['windows']) != WINDOWS:
        raise ValueError('This recipe requires all three 27.3 windows')
    for component in ctx.board['payload']['runtime']['trust_components']:
        path = source.extract(component)
        result['trust_caches'].append(str(path))
    result['kernelcache'] = str(ipsw.kernelcache(ctx, source))
    save(out / 'inputs.json', result)
    print('Original27.3 inputs decoded and inventoried; no PAC conversion or device writes performed')


if __name__ == '__main__':
    main()
