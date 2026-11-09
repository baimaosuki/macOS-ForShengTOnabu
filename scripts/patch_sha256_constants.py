#!/usr/bin/env python3
"""Minimal, asserted repair of a kernel image that only has the two corrupted
SHA-256 K tables.

`prelink_fileset_kernel.py`'s provenance rewrite is still incomplete: it stops at
`br xN` and therefore misses jump-table targets, so it silently drops real calls
(`AppleMatch::_matchUnpack` -> `_kfree_data` / `_IOMallocTypeImpl`) and the kernel
jumps into a zeroed hole (`instr=0`). Until that scanner is finished, the safe
candidate is the last image that reaches dyld with exactly the two confirmed
constant repairs applied.

Every step is asserted: the old bytes must match, only the listed words may
change, and the resulting hash is printed.

Usage:
    patch_sha256_constants.py --base IMAGE --output IMAGE [--json OUT]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys

# The two K[31] words that the 4-byte branch scan turned into branches. Offsets are
# file offsets in the *input* image and are asserted against `old` before writing.
REPAIRS = (
    dict(offset=0x500654C, old=0x169B283C, new=0x14292967,
         what='SHA-256 K[31] in placed kext text (first copy)'),
    dict(offset=0x500D48C, old=0x169B1414, new=0x14292967,
         what='SHA-256 K[31] in placed kext text (second copy)'),
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--json')
    parser.add_argument('--expect-missing', action='store_true',
                        help='report a no-op instead of failing when the base image'
                             ' already carries the repaired values')
    args = parser.parse_args()

    data = bytearray(open(args.base, 'rb').read())
    before = bytes(data)
    applied, skipped = [], []
    for repair in REPAIRS:
        offset = repair['offset']
        found = struct.unpack_from('<I', data, offset)[0]
        if found == repair['old']:
            struct.pack_into('<I', data, offset, repair['new'])
            applied.append(dict(repair, found='%#010x' % found))
        elif found == repair['new']:
            skipped.append(dict(repair, found='%#010x' % found))
        else:
            raise SystemExit('offset %#x holds %#010x, expected %#010x (%s)'
                             % (offset, found, repair['old'], repair['what']))

    changed = {offset for offset in range(0, len(before) - 3, 4)
               if before[offset:offset + 4] != bytes(data[offset:offset + 4])}
    allowed = {repair['offset'] for repair in applied}
    unexpected = sorted(changed - allowed)
    if unexpected:
        raise SystemExit('candidate changed %d unrecorded word(s), first at %#x'
                         % (len(unexpected), unexpected[0]))
    if not applied and not args.expect_missing:
        raise SystemExit('nothing to repair: base image already carries the new values')

    open(args.output, 'wb').write(bytes(data))
    report = dict(base=args.base, base_md5=hashlib.md5(before).hexdigest(),
                  base_bytes=len(before), output=args.output,
                  output_md5=hashlib.md5(bytes(data)).hexdigest(),
                  output_sha256=hashlib.sha256(bytes(data)).hexdigest(),
                  output_bytes=len(data),
                  applied=[dict(offset='%#x' % item['offset'], old=item['found'],
                                new='%#010x' % item['new'], what=item['what'])
                           for item in applied],
                  already=[dict(offset='%#x' % item['offset'], value=item['found'])
                           for item in skipped],
                  changed_words=len(changed))
    print(json.dumps(report, indent=2))
    if args.json:
        open(args.json, 'w').write(json.dumps(report, indent=2) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
