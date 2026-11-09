#!/usr/bin/env python3
"""Pack a raw disk image into the DBRDISK1 sparse ramdisk container.

Format (DarwinBootPkg/Driver/PackedRamdisk.h): ASCII "DBRDISK1", u32 version=1,
u32 page size, u64 logical bytes, u64 extent count, then ascending
(offset, length) pairs followed by the concatenated bytes.  Omitted page ranges
are real zeroed RAM, so only nonzero pages cost space.
"""
import argparse
import struct
import sys

PAGE = 4096
MAGIC = b"DBRDISK1"


def extents(data, page=PAGE):
    out = []
    total = len(data)
    for begin in range(0, total, page):
        chunk = data[begin:begin + page]
        if not any(chunk):
            continue
        end = begin + len(chunk)
        if out and out[-1][0] + out[-1][1] == begin:
            out[-1][1] += len(chunk)
        else:
            out.append([begin, len(chunk)])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("raw")
    ap.add_argument("out")
    args = ap.parse_args()
    data = open(args.raw, "rb").read()
    if len(data) % PAGE:
        data += bytes(PAGE - len(data) % PAGE)
    spans = extents(data)
    if not spans:
        raise SystemExit("image is entirely zero")
    table = b"".join(struct.pack("<QQ", off, length) for off, length in spans)
    header = MAGIC + struct.pack("<IIQQ", 1, PAGE, len(data), len(spans))
    body = b"".join(data[off:off + length] for off, length in spans)
    blob = header + table + body
    assert len(header) + len(table) + len(body) == len(blob)
    assert len(spans) <= 65536, len(spans)
    open(args.out, "wb").write(blob)
    print("raw %d bytes (%.0f MB) -> packed %d bytes (%.0f MB), %d extents, zero %.1f%%"
          % (len(data), len(data) / 1048576, len(blob), len(blob) / 1048576,
             len(spans), 100.0 * (len(data) - len(body)) / len(data)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
