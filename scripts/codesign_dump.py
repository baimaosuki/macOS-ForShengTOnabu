#!/usr/bin/env python3
"""Dump the embedded code signature of a Mach-O without macOS tooling.

Reads LC_CODE_SIGNATURE, walks the SuperBlob, and reports for every CodeDirectory:
identifier, team identifier, flags (with the ad-hoc / platform bits decoded),
hash type, cdhash, and the number of special slots. This is the evidence needed
to tell "no signature on disk" apart from "signature present but not trusted".
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

LC_CODE_SIGNATURE = 0x1d
CSMAGIC_EMBEDDED_SIGNATURE = 0xfade0cc0
CSMAGIC_CODEDIRECTORY = 0xfade0c02

CS_ADHOC = 0x00000002
CS_HARD = 0x00000100
CS_RESTRICT = 0x00000800
CS_ENFORCEMENT = 0x00001000
CS_RUNTIME = 0x00010000
CS_LINKER_SIGNED = 0x20000000
CS_PLATFORM_BINARY = 0x04000000

FLAG_NAMES = {
    0x00000001: 'CS_VALID', 0x00000002: 'CS_ADHOC', 0x00000004: 'CS_GET_TASK_ALLOW',
    0x00000008: 'CS_INSTALLER', 0x00000100: 'CS_HARD', 0x00000200: 'CS_KILL',
    0x00000400: 'CS_CHECK_EXPIRATION', 0x00000800: 'CS_RESTRICT',
    0x00001000: 'CS_ENFORCEMENT', 0x00002000: 'CS_REQUIRE_LV', 0x00004000: 'CS_ENTITLEMENTS_VALIDATED',
    0x00010000: 'CS_RUNTIME', 0x00020000: 'CS_LINKER_SIGNED',
    0x04000000: 'CS_PLATFORM_BINARY',
}

HASH_NAMES = {1: ('sha1', 20), 2: ('sha256', 32), 3: ('sha256', 20), 4: ('sha384', 48)}


def decode_flags(flags: int) -> list[str]:
    names = [name for bit, name in sorted(FLAG_NAMES.items()) if flags & bit]
    unknown = flags & ~sum(FLAG_NAMES)
    if unknown:
        names.append(hex(unknown))
    return names


def blobs(data: bytes):
    magic, length, count = struct.unpack_from('>III', data, 0)
    if magic != CSMAGIC_EMBEDDED_SIGNATURE:
        raise SystemExit(f'not an embedded signature superblob: {magic:#x}')
    for index in range(count):
        kind, offset = struct.unpack_from('>II', data, 12 + index * 8)
        yield kind, data[offset:]


def parse_codedirectory(blob: bytes) -> dict:
    magic, length, version, flags, hash_offset, ident_offset, n_special, n_code, code_limit = \
        struct.unpack_from('>IIIIIIIII', blob, 0)
    if magic != CSMAGIC_CODEDIRECTORY:
        raise SystemExit(f'not a CodeDirectory: {magic:#x}')
    hash_size, hash_type, platform, page_size, spare2 = struct.unpack_from('>BBBBI', blob, 36)
    ident = blob[ident_offset:blob.index(b'\0', ident_offset)].decode('utf-8', 'replace')
    team = None
    if version >= 0x20200:
        team_offset, = struct.unpack_from('>I', blob, 48)
        if team_offset:
            team = blob[team_offset:blob.index(b'\0', team_offset)].decode('utf-8', 'replace')
    algo, truncated = HASH_NAMES.get(hash_type, ('unknown', hash_size))
    digest = hashlib.new(algo, blob[:length]).digest()[:truncated]
    return dict(version=hex(version), flags=flags, flags_decoded=decode_flags(flags),
                adhoc=bool(flags & CS_ADHOC), platform_flag=bool(flags & CS_PLATFORM_BINARY),
                platform_field=platform, identifier=ident, team_identifier=team,
                hash_type=hash_type, hash_name=algo, n_special_slots=n_special,
                n_code_slots=n_code, code_limit=code_limit, cdhash=digest.hex())


def dump(path: Path) -> dict:
    data = path.read_bytes()
    magic, cpu, subtype, kind, ncmds, sizeofcmds = struct.unpack_from('<6I', data, 0)
    if magic != 0xfeedfacf:
        raise SystemExit(f'{path}: not a 64-bit Mach-O')
    pos, sig = 32, None
    for _ in range(ncmds):
        cmd, length = struct.unpack_from('<II', data, pos)
        if cmd == LC_CODE_SIGNATURE:
            sig = struct.unpack_from('<II', data, pos + 8)
        pos += length
    out = dict(path=str(path), size=len(data), cputype=hex(cpu), subtype=hex(subtype & 0xFFFFFFFF),
               has_lc_code_signature=sig is not None, code_directories=[])
    if sig is None:
        out['verdict'] = 'unsigned: no LC_CODE_SIGNATURE'
        return out
    offset, size = sig
    blob = data[offset:offset + size]
    kind = struct.unpack_from('>I', blob, 0)[0]
    out['signature_kind'] = {CSMAGIC_EMBEDDED_SIGNATURE: 'embedded superblob',
                             CSMAGIC_CODEDIRECTORY: 'bare CodeDirectory',
                             0xfade0c01: 'adhoc-linker'}.get(kind, hex(kind))
    out['signature_bytes'] = size
    for blob_type, payload in blobs(blob):
        if struct.unpack_from('>I', payload, 0)[0] == CSMAGIC_CODEDIRECTORY:
            entry = parse_codedirectory(payload)
            entry['blob_type'] = hex(blob_type)
            out['code_directories'].append(entry)
    out['verdict'] = ('ad-hoc only: no platform identity' if out['code_directories']
                      and all(c['adhoc'] for c in out['code_directories'])
                      else 'see code_directories')
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('paths', nargs='+', type=Path)
    ap.add_argument('--json', type=Path)
    args = ap.parse_args()
    results = [dump(p) for p in args.paths]
    text = json.dumps(results, indent=2)
    if args.json:
        args.json.write_text(text + '\n')
    print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())