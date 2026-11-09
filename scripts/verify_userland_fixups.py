#!/usr/bin/env python3
"""Independently verify a translated userland Mach-O.

Second, independent parser: it never imports the translator, so a bug that writes
bad pointers cannot also validate them.

Format 6 (DYLD_CHAINED_PTR_64_OFFSET) has no auth bit: bit 63 is the bind flag,
the rebase target is a 36-bit offset from the image base, and bits 36..50
(high8 + reserved) must be zero.  The arm64e original is decoded per
fixup-chains.h for formats 9 and 12: auth = bit 63, bind = bit 62, next = bits
51..61 with an 8-byte stride, unauthenticated target = vm offset from the image
base with a separate high8 byte, authenticated target = 32-bit runtimeOffset.
Only formats 9 and 12 are accepted as originals; anything else stops the check
instead of being silently skipped.
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
ACCEPTED_ORIGINALS = (ARM64E_USERLAND, ARM64E_USERLAND24)

BIND_BIT_ARM64E = 1 << 62
AUTH_BIT_ARM64E = 1 << 63


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


def segment_starts(data: bytes, img: dict):
    """Yield (segment, page_size, pointer_format, page_start_list) per chained segment."""
    off, size = img['fixups']
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
        starts_list = [struct.unpack_from('<H', blob, pos + 22 + 2 * page)[0] for page in range(pages)]
        yield dict(seg=seg, page_size=page_size, format=fmt, seg_off=seg_off,
                   pages=pages, starts=starts_list)


def decode_arm64e(raw: int, fmt: int) -> dict:
    auth = bool(raw & AUTH_BIT_ARM64E)
    bind = bool(raw & BIND_BIT_ARM64E)
    nxt = (raw >> 51) & 0x7FF
    if bind:
        ordinal = raw & (0xFFFFFF if fmt == ARM64E_USERLAND24 else 0xFFFF)
        addend = 0 if auth else ((raw >> 32) & 0x7FFFF)
        if not auth and addend & 0x40000:
            addend -= 0x80000
        return dict(kind='bind', auth=auth, ordinal=ordinal, addend=addend, next=nxt, high8=None)
    if auth:
        return dict(kind='rebase', auth=True, offset=raw & 0xFFFFFFFF, next=nxt, high8=None)
    return dict(kind='rebase', auth=False, offset=raw & ((1 << 43) - 1), next=nxt,
                high8=(raw >> 43) & 0xFF)


def decode_64_offset(raw: int) -> dict:
    if raw >> 63:
        addend = (raw >> 24) & 0xFF
        if addend & 0x80:
            addend -= 0x100
        return dict(kind='bind', ordinal=raw & 0xFFFFFF, addend=addend,
                    next=(raw >> 51) & 0xFFF, reserved=(raw >> 32) & 0x7FFFF)
    return dict(kind='rebase', offset=raw & ((1 << 36) - 1), next=(raw >> 51) & 0xFFF,
                reserved=(raw >> 36) & 0x7FFF)


def walk(data: bytes, starts_entry: dict, stride: int):
    seg = starts_entry['seg']
    for page, start in enumerate(starts_entry['starts']):
        if start == 0xFFFF:
            continue
        site = page * starts_entry['page_size'] + start
        while True:
            raw, = struct.unpack_from('<Q', data, seg['fileoff'] + site)
            yield site, raw
            if stride == 8:
                nxt = (raw >> 51) & 0x7FF
            else:
                nxt = (raw >> 51) & 0xFFF
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

    report = dict(subtype=hex(img['subtype']), image_base=hex(base_of(img)),
                  pointer_formats={}, pointers=0, reserved_dirty=[], reserved_dirty_count=0,
                  segment_offsets_match=True, non_arm64_formats={})
    new_map = {}
    for entry in segment_starts(data, img):
        name = entry['seg']['name']
        report['pointer_formats'][name] = entry['format']
        if entry['format'] != PTR_64_OFFSET:
            report['non_arm64_formats'][name] = entry['format']
            continue
        if entry['seg_off'] != entry['seg']['fileoff']:
            report['segment_offsets_match'] = False
        for site, raw in walk(data, entry, 4):
            report['pointers'] += 1
            new_map[(name, site)] = raw
            decoded = decode_64_offset(raw)
            if decoded['reserved']:
                if len(report['reserved_dirty']) < 8:
                    report['reserved_dirty'].append(
                        dict(segment=name, va=hex(entry['seg']['va'] + site),
                             kind=decoded['kind'], reserved=hex(decoded['reserved'])))
                report['reserved_dirty_count'] += 1

    mismatches = []
    verified = 0
    if args.original:
        orig = args.original.read_bytes()
        oimg = image(orig)
        oentries = list(segment_starts(orig, oimg))
        oformats = {e['seg']['name']: e['format'] for e in oentries if e['format']}
        exotic = {k: v for k, v in oformats.items() if v not in ACCEPTED_ORIGINALS}
        if exotic:
            raise SystemExit(f'original uses chained fixup formats this checker cannot decode: {exotic}')
        old_map = {}
        oimg_base = base_of(oimg)
        for entry in oentries:
            if not entry['format']:
                continue
            for site, raw in walk(orig, entry, 8):
                old_map[(entry['seg']['name'], site)] = (raw, entry['format'])
        report['originals_walked'] = len(old_map)
        report['original_formats'] = oformats
        report['original_image_base'] = hex(oimg_base)
        for key, (raw, fmt) in old_map.items():
            if key not in new_map:
                mismatches.append(dict(kind='missing', segment=key[0], site=hex(key[1])))
                continue
            old = decode_arm64e(raw, fmt)
            new = decode_64_offset(new_map[key])
            if old['kind'] != new['kind']:
                mismatches.append(dict(kind='kind', segment=key[0], site=hex(key[1]),
                                       old=old['kind'], new=new['kind']))
            elif old['kind'] == 'rebase':
                if old['high8'] not in (None, 0):
                    mismatches.append(dict(kind='high8-unrepresentable', segment=key[0],
                                           site=hex(key[1]), high8=hex(old['high8'])))
                elif old['offset'] != new['offset']:
                    mismatches.append(dict(kind='offset', segment=key[0], site=hex(key[1]),
                                           old=hex(old['offset']), new=hex(new['offset'])))
                else:
                    verified += 1
            elif old['ordinal'] != new['ordinal'] or old['addend'] != new['addend']:
                mismatches.append(dict(kind='bind', segment=key[0], site=hex(key[1]),
                                       old=f"{old['ordinal']}/{old['addend']}",
                                       new=f"{new['ordinal']}/{new['addend']}"))
            else:
                verified += 1
        if len(new_map) != len(old_map):
            mismatches.append(dict(kind='site-count', new=len(new_map), old=len(old_map)))
        report['semantic_verified'] = verified
        report['mismatch_count'] = len(mismatches)
        report['mismatches'] = mismatches[:12]

    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    failed = (report['reserved_dirty_count'] or report['non_arm64_formats']
              or not report['segment_offsets_match'] or mismatches)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
