#!/usr/bin/env python3
"""Unpack a DBRDISK1 sparse ramdisk container back into a raw disk image."""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys

MAGIC = b'DBRDISK1'


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('packed')
    ap.add_argument('out')
    ap.add_argument('--report')
    args = ap.parse_args()

    blob = open(args.packed, 'rb').read()
    if blob[:8] != MAGIC:
        raise SystemExit('not a DBRDISK1 container')
    version, page, logical, count = struct.unpack_from('<IIQQ', blob, 8)
    if version != 1 or page not in (4096, 16384):
        raise SystemExit(f'unsupported container version/page {version}/{page}')
    table_at = 8 + 24
    spans = [struct.unpack_from('<QQ', blob, table_at + 16 * i) for i in range(count)]
    body_at = table_at + 16 * count
    image = bytearray(logical)
    cursor = body_at
    previous_end = 0
    for offset, length in spans:
        if offset < previous_end or offset + length > logical or length % page:
            raise SystemExit('extent table is not ascending and bounded')
        image[offset:offset + length] = blob[cursor:cursor + length]
        cursor += length
        previous_end = offset + length
    if cursor != len(blob):
        raise SystemExit('container carries trailing bytes after the last extent')
    with open(args.out, 'wb') as stream:
        stream.write(image)
    digest = hashlib.md5(image).hexdigest()
    print('logical %d bytes, %d extents, %d packed -> md5 %s'
          % (logical, count, len(blob), digest))
    if args.report:
        import json
        with open(args.report, 'w') as stream:
            json.dump(dict(logical=logical, extents=count, packed_bytes=len(blob),
                           page=page, md5=digest), stream, indent=2)
    return 0


if __name__ == '__main__':
    sys.exit(main())
