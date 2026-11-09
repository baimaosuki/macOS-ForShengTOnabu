#!/usr/bin/env python3
"""Decode the kernel journal written into XNU2LOG.BIN on the nabu ESP.

Mirrors `nb_slot_valid()` from platform/nabu/macos26/log/NabuLogFormat.h:

  slot  = 4096 header + 4 MiB payload ring
  file  = 4096 config + slot A + slot B

A slot is trusted only when magic/version/geometry/token match, the header CRC
over bytes [0,4096) with field 80 zeroed matches, end-first == length, and the
payload CRC over the committed bytes matches. The newer valid slot wins.
"""
from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path

BLOCK = 4096
CAPACITY = 4 * 1024 * 1024
SLOT_BYTES = BLOCK + CAPACITY
FILE_BYTES = BLOCK + 2 * SLOT_BYTES
SLOTS = (BLOCK, BLOCK + SLOT_BYTES)


def header_crc(block: bytes, zero_at: int) -> int:
    """crc32 over the block with a 4-byte field treated as zero."""
    buf = bytearray(block)
    struct.pack_into("<I", buf, zero_at, 0)
    return zlib.crc32(bytes(buf)) & 0xFFFFFFFF


def u32(p: bytes, at: int) -> int:
    return struct.unpack_from("<I", p, at)[0]


def u64(p: bytes, at: int) -> int:
    return struct.unpack_from("<Q", p, at)[0]


def parse_slot(image: bytes, base: int, token: bytes) -> dict | None:
    head = image[base:base + BLOCK]
    if len(head) < BLOCK or head[0:8] != b"NABULOG2":
        return None
    if u32(head, 8) != 2 or u32(head, 12) != BLOCK or u32(head, 16) != 1:
        return None
    if u32(head, 20) != CAPACITY or u32(head, 84) != 0:
        return None
    if u32(head, 80) != header_crc(head, 80):
        return None
    if head[96:112] != token:
        return None
    end, first, length = u64(head, 48), u64(head, 64), u32(head, 72)
    if first > end or end - first != length or length != min(end, CAPACITY):
        return None
    if u64(head, 88) != first:
        return None
    payload = image[base + BLOCK:base + SLOT_BYTES]
    start = (end - length) % CAPACITY
    data = payload[start:start + length]
    if start + length > CAPACITY:
        data += payload[:(start + length) - CAPACITY]
    if u32(head, 76) != (zlib.crc32(data) & 0xFFFFFFFF):
        return None
    return {
        "sequence": u64(head, 24),
        "boot_id": head[32:48].hex(),
        "end": end,
        "first": first,
        "length": length,
        "overwritten": u64(head, 88),
        "lock_lost": u64(head, 56),
        "build": head[112:144].rstrip(b"\0").decode("ascii", "replace"),
        "data": data,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("image", type=Path)
    ap.add_argument("--token", required=True, help="32 hex digits used in the boot argument")
    ap.add_argument("--out", type=Path, help="write the decoded text stream here")
    args = ap.parse_args()

    image = args.image.read_bytes()
    token = bytes.fromhex(args.token)
    if len(image) != FILE_BYTES:
        print(f"warn: size {len(image)} != {FILE_BYTES} (expected; continuing)", file=sys.stderr)

    found = [s for s in (parse_slot(image, base, token) for base in SLOTS) if s]
    if not found:
        print("no valid slot (writer never committed, or token mismatch)", file=sys.stderr)
        return 1
    slot = max(found, key=lambda s: s["sequence"])
    print(f"slot sequence : {slot['sequence']}")
    print(f"boot id       : {slot['boot_id']}")
    print(f"committed     : {slot['length']} bytes (end={slot['end']}, first={slot['first']})")
    print(f"overwritten   : {slot['overwritten']}   lock_lost: {slot['lock_lost']}")
    print(f"build         : {slot['build'] or '(unset)'}")
    print(f"valid slots   : {len(found)}")
    if args.out:
        args.out.write_bytes(slot["data"])
        print(f"wrote {args.out}")
    else:
        sys.stdout.write(slot["data"].decode("utf-8", "backslashreplace"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
