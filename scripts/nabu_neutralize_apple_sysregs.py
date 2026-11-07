#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Neutralise Apple-private system registers in a linked nabu XNU kernel.

Kryo 485 (Snapdragon 860) implements none of Apple's implementation-defined
performance-monitor registers: every MRS/MSR to the S3_*_C15_* space raises an
undefined-instruction exception (ESR EC=0x0, FAR=0) and takes the kernel down
inside cpc_* / kpc_* / awl_* before the first kext is even reached.

Scanning the linked image is the only reliable gate here: the register accesses
are spread over cpc_arm64.c, kpc_arm64.c and awl.c, and each carries literal
register names, so no single source-level macro covers them all. Rewrite them
after the link:

    MRS Xd, S3_x_C15_cY_Z  ->  MOV Xd, #0     (the feature simply reads as absent)
    MSR S3_x_C15_cY_Z, Xn  ->  NOP           (nothing to program, nothing to arm)

Architected system registers (S3_3_C9_* PMU, S3_0/1/2/3_C0_* cache ID space) are
left untouched: those are ISA-defined and do exist on this core.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM

LC_SEGMENT_64 = 0x19
MACH_HEADER_64 = 0xFEEDFACF
SCAN_SEGMENTS = ("__TEXT_EXEC", "__TEXT_BOOT_EXEC", "__TEXT")
APPLE_SYSREG_MARK = "c15"

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
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from("<QQQQ", data, off + 24)
            segments[name] = (vmaddr, vmsize, fileoff, filesize)
        off += size
    return segments


def mov_imm_zero(insn) -> int:
    """MOV Xd/Wd, #0 -> MOVZ with a zero immediate."""
    reg = insn.operands[0].reg
    name = insn.reg_name(reg)
    if name.startswith("w"):
        return 0x52800000 | reg
    return 0xD2800000 | reg


def patch(data: bytearray, keep_going: bool) -> list[tuple[int, str, str, int]]:
    segments = load_segments(data)
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md.skipdata = True
    md.detail = True   # capstone 5 needs detail for operands
    changes: list[tuple[int, str, str, int]] = []
    for seg in SCAN_SEGMENTS:
        if seg not in segments:
            continue
        vmaddr, _, fileoff, filesize = segments[seg]
        blob = bytes(data[fileoff:fileoff + filesize])
        for insn in md.disasm(blob, vmaddr):
            if insn.mnemonic not in ("mrs", "msr"):
                continue
            if APPLE_SYSREG_MARK not in insn.op_str.lower():
                continue
            if not insn.operands:
                continue
            if insn.mnemonic == "mrs":
                word = mov_imm_zero(insn)
            else:
                word = NOP
            at = fileoff + (insn.address - vmaddr)
            if struct.unpack_from("<I", data, at)[0] == word:
                continue
            struct.pack_into("<I", data, at, word)
            changes.append((insn.address, insn.mnemonic, insn.op_str, word))
            if not keep_going:
                pass
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kernel", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    data = bytearray(args.kernel.read_bytes())
    changes = patch(data, keep_going=True)
    out = args.output or args.kernel
    out.write_bytes(bytes(data))
    if not args.quiet:
        print(f"{args.kernel}: neutralised {len(changes)} Apple-private sysreg access(es)")
        for addr, mnemonic, ops, word in changes:
            print(f"  0x{addr:x}  {mnemonic:4s} {ops:24s} -> 0x{word:08x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
