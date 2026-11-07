#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Neutralise Apple-private system registers in a linked nabu XNU kernel.

Kryo 485 (Snapdragon 860) implements none of Apple's implementation-defined
performance-monitor registers: every MRS/MSR into the C15 system-register space
raises an undefined-instruction exception (ESR EC=0x0, FAR=0) and takes the
kernel down inside cpc_* / kpc_* / awl_* before the first kext is reached.

Scanning the linked image is the only reliable gate: the accesses are spread over
cpc_arm64.c, kpc_arm64.c and awl.c and each carries literal register names, so no
single source-level macro covers them all. Rewrite them after the link:

    MRS Xd, <C15 sysreg>  ->  MOV Xd, #0     (the feature reads as absent)
    MSR <C15 sysreg>, Xn  ->  NOP           (nothing to program, nothing to arm)

AArch64 system-register moves are fixed encodings, so this needs no disassembler:

    MRS Xt, <sysreg>      bits[31:20] = 1101 0101 0011   (0xD53)
    MSR <sysreg>, Xt      bits[31:20] = 1101 0101 0001   (0xD51)
    <sysreg>              op0 bits[20:19], op1 [18:16], CRn [15:12], CRm [11:8], op2 [7:5]

CRn == 15 is the implementation-defined space; architected registers (PMU
S3_3_C9_*, cache IDs S3_x_C0_*) are never touched because their CRn is not 15.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

LC_SEGMENT_64 = 0x19
MACH_HEADER_64 = 0xFEEDFACF
SCAN_SEGMENTS = ("__TEXT_EXEC", "__TEXT_BOOT_EXEC", "__TEXT")

MRS_MASK, MRS_BASE = 0xFFF00000, 0xD5300000
MSR_MASK, MSR_BASE = 0xFFF00000, 0xD5100000
NOP = 0xD503201F


def load_segments(data: bytes) -> dict[str, tuple[int, int, int, int]]:
    magic = struct.unpack_from("<I", data, 0)[0]
    if magic != MACH_HEADER_64:
        raise SystemExit(f"not a thin 64-bit Mach-O (magic {magic:#x})")
    ncmds, = struct.unpack_from("<I", data, 16)
    off = 32
    segments: dict[str, tuple[int, int, int, int]] = {}
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", data, off)
        if cmd == LC_SEGMENT_64:
            name = data[off + 8:off + 24].rstrip(b"\0").decode("ascii", "replace")
            segments[name] = struct.unpack_from("<QQQQ", data, off + 24)
        off += size
    return segments


def sysreg_moves(word: int) -> tuple[str, int] | None:
    """Return ('mrs'|'msr', Rt) for a C15 system-register move, else None."""
    if word & MRS_MASK == MRS_BASE:
        mnemonic = "mrs"
    elif word & MSR_MASK == MSR_BASE:
        mnemonic = "msr"
    else:
        return None
    if (word >> 12) & 0xF != 15:          # CRn must be 15 (implementation defined)
        return None
    return mnemonic, word & 0x1F


def patch(data: bytearray) -> list[tuple[int, str, int, int]]:
    segments = load_segments(data)
    changes: list[tuple[int, str, int, int]] = []
    for seg in SCAN_SEGMENTS:
        if seg not in segments:
            continue
        vmaddr, _, fileoff, filesize = segments[seg]
        for pos in range(0, filesize - 3, 4):
            word, = struct.unpack_from("<I", data, fileoff + pos)
            decoded = sysreg_moves(word)
            if decoded is None:
                continue
            mnemonic, rt = decoded
            replacement = (0xD2800000 | rt) if mnemonic == "mrs" else NOP
            address = vmaddr + pos
            struct.pack_into("<I", data, fileoff + pos, replacement)
            changes.append((address, mnemonic, word, replacement))
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kernel", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    data = bytearray(args.kernel.read_bytes())
    changes = patch(data)
    out = args.output or args.kernel
    out.write_bytes(bytes(data))
    if not args.quiet:
        print(f"{args.kernel}: neutralised {len(changes)} C15 sysreg access(es)")
        for address, mnemonic, word, replacement in changes:
            print(f"  0x{address:x}  {mnemonic}  0x{word:08x} -> 0x{replacement:08x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
