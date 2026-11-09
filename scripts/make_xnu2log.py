#!/usr/bin/env python3
"""Build a valid XNU2LOG.BIN image for the nabu kernel journal writer.

On-medium contract (platform/nabu/macos26/log/NabuLogFormat.h):

  file  = config block (4096) + slot A (4096 header + 4 MiB) + slot B (same)
        = 8,400,896 bytes, i.e. exactly 2051 clusters of 4096 bytes

  config = "NABUCFG2", version 2, header 4096, capacity 4 MiB,
           slot bytes 4,198,400, file bytes 8,400,896, zero,
           token[16], block bytes 4096, zero, crc32(block with field 56 zeroed),
           zero padding to 4096

The kernel writer refuses the volume unless the file is one contiguous chain
(nb_no_aliases) and the config carries the same token as the boot argument
`nabu-log-token=<32 hex digits>`. Slots are left zeroed: the writer commits
them.
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import zlib
from pathlib import Path

BLOCK = 4096
CAPACITY = 4 * 1024 * 1024
SLOT_BYTES = BLOCK + CAPACITY
FILE_BYTES = BLOCK + 2 * SLOT_BYTES
CONFIG_MAGIC = b"NABUCFG2"
TOKEN_OFFSET = 32
CRC_OFFSET = 56


def config_block(token: bytes) -> bytes:
    if len(token) != 16:
        raise SystemExit("token must be 16 bytes")
    block = bytearray(BLOCK)
    block[0:8] = CONFIG_MAGIC
    struct.pack_into("<I", block, 8, 2)
    struct.pack_into("<I", block, 12, BLOCK)
    struct.pack_into("<I", block, 16, CAPACITY)
    struct.pack_into("<I", block, 20, SLOT_BYTES)
    struct.pack_into("<I", block, 24, FILE_BYTES)
    struct.pack_into("<I", block, 28, 0)
    block[TOKEN_OFFSET:TOKEN_OFFSET + 16] = token
    struct.pack_into("<I", block, 48, BLOCK)
    struct.pack_into("<I", block, 52, 0)
    struct.pack_into("<I", block, CRC_OFFSET, 0)
    crc = zlib.crc32(bytes(block)) & 0xFFFFFFFF
    struct.pack_into("<I", block, CRC_OFFSET, crc)
    return bytes(block)


def verify(image: bytes, token: bytes) -> None:
    """Mirror nb_config_valid() from the header, field by field."""
    p = image
    assert p[0:8] == CONFIG_MAGIC, "config magic"
    assert struct.unpack_from("<I", p, 8)[0] == 2, "config version"
    assert struct.unpack_from("<I", p, 12)[0] == BLOCK, "header bytes"
    assert struct.unpack_from("<I", p, 16)[0] == CAPACITY, "capacity"
    assert struct.unpack_from("<I", p, 20)[0] == SLOT_BYTES, "slot bytes"
    assert struct.unpack_from("<I", p, 24)[0] == FILE_BYTES, "file bytes"
    assert struct.unpack_from("<I", p, 28)[0] == 0, "reserved 28"
    assert p[TOKEN_OFFSET:TOKEN_OFFSET + 16] == token, "token"
    assert struct.unpack_from("<I", p, 48)[0] == BLOCK, "block bytes"
    assert struct.unpack_from("<I", p, 52)[0] == 0, "reserved 52"
    stored = struct.unpack_from("<I", p, CRC_OFFSET)[0]
    zeroed = bytearray(p[:BLOCK])
    struct.pack_into("<I", zeroed, CRC_OFFSET, 0)
    assert stored == (zlib.crc32(bytes(zeroed)) & 0xFFFFFFFF), "config crc"
    assert not any(p[60:BLOCK]), "config tail must be zero"
    assert len(p) == FILE_BYTES, f"size {len(p)} != {FILE_BYTES}"
    assert FILE_BYTES % BLOCK == 0 and FILE_BYTES // BLOCK == 2051


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", nargs="?", default="XNU2LOG.BIN", type=Path)
    parser.add_argument("--token", help="32 hex digits; default: derived from the build tag")
    args = parser.parse_args()

    if args.token:
        token = bytes.fromhex(args.token)
        if len(token) != 16:
            raise SystemExit("--token must be exactly 32 hex digits")
    else:
        token = hashlib.sha256(b"nabu-xnu2log-v1").digest()[:16]

    image = config_block(token) + bytes(FILE_BYTES - BLOCK)
    verify(image, token)
    args.out.write_bytes(image)

    print(f"wrote {args.out} ({len(image)} bytes, {len(image) // BLOCK} clusters)")
    print(f"token hex : {token.hex()}")
    print(f"boot arg  : nabu-log-token={token.hex()}")
    print(f"md5       : {hashlib.md5(image).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
