#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Find FEAT_SHA3 / FEAT_SHA512 / FEAT_SM4 instructions in a kernel collection.

Kryo 485 (Snapdragon 860 = ARMv8.2-A) implements none of: SHA3 (eor3/bcax/rax1/xar),
SHA512 (sha512h/sha512h2/sha512su0/sha512su1) or SM4 (sm4e/sm4ekey2). Apple's arm64e
kernel collection is built with all of them enabled, so a translated kext that still
carries one dies with ESR EC=0x0 the moment that path runs.

All of these live in the Advanced SIMD "three same, crypto" space: bits[31:24] == 0xCE.
Scan per fileset entry so the report names the bundle that needs fixing.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

OPCODES = [
    ("eor3",     0xFFE08000, 0xCE000000),
    ("bcax",     0xFFE08000, 0xCE200000),
    ("rax1",     0xFFE0FC00, 0xCE608C00),
    ("xar",      0xFFE00000, 0xCE800000),
    ("sha512h",  0xFFE08000, 0xCE608000),
    ("sha512h2", 0xFFE08000, 0xCE608400),
    ("sha512su0",0xFFE0FC00, 0xCEC08000),
    ("sha512su1",0xFFE0FC00, 0xCEC08800),
    ("sm4e",     0xFFFFFC00, 0xCEC08400),
    ("sm4ekey2", 0xFFE08000, 0xCEC08800),
    ("OTHER_CE", 0xFF000000, 0xCE000000),
]


def decode(word: int) -> str | None:
    if word & 0xFF000000 != 0xCE000000:
        return None
    for name, mask, base in OPCODES:
        if word & mask == base:
            return name
    return "OTHER_CE"


LC_SEGMENT_64 = 0x19


def nested_code_ranges(macho: bytes, header_off: int) -> list[tuple[int, int]]:
    """File ranges of the code segments of one entry's nested Mach-O.

    The fileset metadata omits fileoffs, but each entry starts with its own
    MH_KEXT_BUNDLE header whose LC_SEGMENT_64 commands carry absolute offsets.
    """
    ncmds, = struct.unpack_from("<I", macho, header_off + 16)
    off = header_off + 32
    ranges = []
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", macho, off)
        if cmd == LC_SEGMENT_64:
            name = macho[off + 8:off + 24].rstrip(b"\0").decode("ascii", "replace")
            if name in ("__TEXT_EXEC", "__TEXT", "__TEXT_BOOT_EXEC"):
                _, vmsize, fileoff, filesize = struct.unpack_from("<QQQQ", macho, off + 24)
                ranges.append((fileoff, min(filesize, vmsize)))
        off += size
    return ranges


def scan_collection(macho: bytes, fileset: dict) -> dict:
    findings: dict[str, dict[str, int]] = {}
    total = 0
    for entry in fileset["entries"]:
        entry_id = entry["entry_id"]
        counts: dict[str, int] = {}
        for start, size in nested_code_ranges(macho, entry["fileoff"]):
            for pos in range(start, start + size - 3, 4):
                name = decode(struct.unpack_from("<I", macho, pos)[0])
                if name:
                    counts[name] = counts.get(name, 0) + 1
                    total += 1
        if counts:
            findings[entry_id] = counts
    return {"entries": findings, "total": total}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("macho", type=Path)
    parser.add_argument("--fileset", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    report = scan_collection(args.macho.read_bytes(), json.loads(args.fileset.read_text()))
    if args.output:
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if not args.quiet:
        print(f"{args.macho.name}: {report['total']} instruction(s) in {len(report['entries'])} bundle(s)")
        for entry_id, counts in sorted(report["entries"].items(), key=lambda kv: -sum(kv[1].values())):
            shown = " ".join(f"{name}={n}" for name, n in sorted(counts.items()))
            print(f"  {sum(counts.values()):6d}  {entry_id}  {shown}")
    return 1 if report["total"] else 0


if __name__ == "__main__":
    sys.exit(main())
