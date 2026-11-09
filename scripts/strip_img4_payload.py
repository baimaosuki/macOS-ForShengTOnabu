#!/usr/bin/env python3
"""Strip an IMG4 wrapper off an Apple ramdisk and check it is a plain APFS container.

The IMG4 file is a 4-byte tag, a 4-byte length, then IM4P/IM4M/IM4R TLV records
whose payload contains the raw disk image.  Rather than trusting the TLV lengths
this locates the APFS container superblock and derives the image extent from
nx_block_size * nx_block_count, which is what the kernel will use anyway.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

NX_MAGIC = b'NXSB'


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('img4')
    ap.add_argument('out')
    ap.add_argument('--report')
    args = ap.parse_args()

    blob = Path(args.img4).read_bytes()
    magic = blob.find(NX_MAGIC)
    if magic < 0:
        raise SystemExit('no NXSB superblock in the IMG4 payload')
    start = magic - 0x20
    if start < 0:
        raise SystemExit('NXSB sits too close to the start of the file to carry a block header')
    block_size, block_count = struct.unpack_from('<IQ', blob, magic + 4)
    logical = block_size * block_count
    if start + logical > len(blob):
        raise SystemExit(f'container extends past the file: {start}+{logical} > {len(blob)}')
    image = blob[start:start + logical]
    if image[0x20:0x24] != NX_MAGIC:
        raise SystemExit('extracted bytes do not start with an APFS container')
    Path(args.out).write_bytes(image)
    report = dict(img4_bytes=len(blob), payload_start=start, block_size=block_size,
                  block_count=block_count, logical=logical,
                  md5=hashlib.md5(image).hexdigest(),
                  sha256=hashlib.sha256(image).hexdigest(),
                  trailing_bytes=len(blob) - start - logical)
    print(json.dumps(report, indent=2))
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
