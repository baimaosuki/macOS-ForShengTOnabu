#!/usr/bin/env python3
"""Independently verify that a translated userland Mach-O carries only arm64
(non-arm64e) chained fixups.

This is deliberately a *second* parser: it does not import the translator, so a
bug that writes bad pointers cannot also validate them.  The translated image is
walked as DYLD_CHAINED_PTR_64_OFFSET (format 6, 4-byte stride) and every site is
compared against the same site in the arm64e original, decoded per
fixup-chains.h: auth=bit63, bind=bit62, next=bits51..61 with an 8-byte stride,
unauthenticated target = vm offset from the image base, authenticated target =
32-bit runtimeOffset.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

LC_SEGMENT_64 = 0x19
LC_DYLD_CHAINED_FIXUPS = 0x80000034

PTR_64_OFFSET = 6
ARM64E_USERLAND = 9
ARM64E_USERLAND24 = 12

BIND_BIT = 1 << 62
AUTH_BIT = 1 << 63


def image(data: bytes) -> dict:
    magic, cpu, subtype, kind, count, size = struct.unpack_from('<6I', data)
    if magic != 0xfeedfacf or cpu != 0x100000c:
        raise SystemExit('not a native arm64 Mach-O')
    out = dict(subtype=subtype & 0xFFFFFFFF, segments=[], fixups=None)
    pos = 32
    for _ in range(count):
        cmd, length = struct.unpack_from('<II', data, pos)
        if cmd == LC_SEGMENT_64:
            name, va, span, off, backed = struct.unpack_from('<16s4Q', data, pos + 8)
            out['segments'].append(dict(name=name.rstrip(b'\0').decode(), va=va, size=span,
                                        fileoff=off, filesize=backed))
        elif cmd == LC_DYLD_CHAINED_FIXUPS:
            out['fixups'] = struct.unpack_from('<II', data, pos + 8)
        pos += length
    return out


def base_of(img: dict) -> int:
    for seg in img['segments']:
        if seg['name'] == '__TEXT':
            return seg['va']
    return min(seg['va'] for seg in img['segments'])


def decode_arm64e(raw: int) -> dict:
    auth = bool(raw & AUTH_BIT)
    bind = bool(raw & BIND_BIT)
    nxt = (raw >> 51) & 0x7FF
    if bind:
        ordinal = raw & 0xFFFFFF
        addend = 0 if auth else ((raw >> 32) & 0x7FFFF)
        if not auth and addend & 0x40000:
            addend -= 0x80000
        return dict(kind='bind', auth=auth, ordinal=ordinal, addend=addend, next=nxt)
    offset = raw & (0xFFFFFFFF if auth else ((1 << 43) - 1))
    return dict(kind='rebase', auth=auth, offset=offset, next=nxt)


def decode_64_offset(raw: int) -> dict:
    bind = bool(raw >> 63)
    nxt = (raw >> 51) & 0xFFF
    if bind:
        addend = (raw >> 24) & 0xFF
        if addend & 0x80:
            addend -= 0x100
        return dict(kind='bind', ordinal=raw & 0xFFFFFF, addend=addend, next=nxt)
    return dict(kind='rebase', offset=raw & ((1 << 36) - 1), next=nxt)


def walk(data: bytes, img: dict, command, expect_format: int, stride: int,
         stride_scale: int = 1):
    """Yield (segment, site, raw) for every chain member."""
    off, size = command
    blob = data[off:off + size]
    version, starts = struct.unpack_from('<II', blob, 0)
    if version != 0:
        raise SystemExit(f'unsupported fixups version {version}')
    count, = struct.unpack_from('<I', blob, starts)
    for index, seg in enumerate(img['segments'][:count]):
        relative, = struct.unpack_from('<I', blob, starts + 4 + index * 4)
        if not relative:
            continue
        pos = starts + relative
        span, page_size, fmt, seg_off, max_valid, pages = struct.unpack_from('<IHHQIH', blob, pos)
        yield ('format', seg, fmt)
        if fmt != expect_format:
            continue
        if seg_off != seg['fileoff']:
            yield ('offset-mismatch', seg, seg_off)
        for page in range(pages):
            start, = struct.unpack_from('<H', blob, pos + 22 + 2 * page)
            if start == 0xFFFF:
                continue
            site = page * page_size + start
            while True:
                raw, = struct.unpack_from('<Q', data, seg['fileoff'] + site)
                yield ('pointer', seg, site, raw)
                if expect_format == PTR_64_OFFSET:
                    nxt = (raw >> 51) & 0xFFF
                else:
                    nxt = (raw >> 51) & 0x7FF
                if not nxt:
                    break
                site += nxt * stride


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--translated', type=Path, required=True)
    ap.add_argument('--original', type=Path)
    ap.add_argument('--report', type=Path)
    args = ap.parse_args()

    data = args.translated.read_bytes()
    img = image(data)
    if img['fixups'] is None:
        raise SystemExit('no LC_DYLD_CHAINED_FIXUPS: nothing to verify')

    formats = {}
    pointers = 0
    auth_left = 0
    offsets_ok = True
    for item in walk(data, img, img['fixups'], PTR_64_OFFSET, 4):
        if item[0] == 'format':
            formats[item[1]['name']] = item[2]
        elif item[0] == 'offset-mismatch':
            offsets_ok = False
        else:
            pointers += 1
            if item[3] & AUTH_BIT:
                auth_left += 1

    bad_formats = {k: v for k, v in formats.items() if v not in (0, PTR_64_OFFSET)}
    report = dict(subtype=hex(img['subtype']), image_base=hex(base_of(img)),
                  pointer_formats=formats, pointers=pointers,
                  auth_bits_left=auth_left, segment_offsets_match=True and offsets_ok,
                  non_arm64_formats=bad_formats)

    mismatches = []
    verified = 0
    if args.original:
        orig = args.original.read_bytes()
        oimg = image(orig)
        new_map = {}
        for item in walk(data, img, img['fixups'], PTR_64_OFFSET, 4):
            if item[0] == 'pointer':
                new_map[(item[1]['name'], item[2])] = item[3]
        old_sites = 0
        for item in walk(orig, oimg, oimg['fixups'], 12, 8):
            if item[0] != 'pointer':
                continue
            old_sites += 1
            key = (item[1]['name'], item[2])
            if key not in new_map:
                mismatches.append(('missing', item[1]['name'], hex(item[2])))
                continue
            old = decode_arm64e(item[3])
            new = decode_64_offset(new_map[key])
            if old['kind'] != new['kind']:
                mismatches.append(('kind', key[0], hex(key[1]), old['kind'], new['kind']))
            elif old['kind'] == 'rebase':
                if old['offset'] != new['offset']:
                    mismatches.append(('offset', key[0], hex(key[1]),
                                       hex(old['offset']), hex(new['offset'])))
                else:
                    verified += 1
            else:
                if old['ordinal'] != new['ordinal'] or old['addend'] != new['addend']:
                    mismatches.append(('bind', key[0], hex(key[1]),
                                       f"{old['ordinal']}/{old['addend']}",
                                       f"{new['ordinal']}/{new['addend']}"))
                else:
                    verified += 1
        if len(new_map) != old_sites:
            mismatches.append(('site-count', len(new_map), old_sites))
        report['originals_walked'] = old_sites
        report['semantic_verified'] = verified
        report['mismatch_count'] = len(mismatches)
        report['mismatches'] = mismatches[:12]

    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    if auth_left or bad_formats or mismatches:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
