#!/usr/bin/env python3
"""Decode an Apple UniversalMac kernelcache into a flat Mach-O kernel collection.

The IPSW ships one kernelcache.release.<soc> per SoC.  Each file is an IMG4/IM4P
container whose ``krnl`` payload is LZFSE compressed.  This tool parses the DER
header, decompresses the payload and records the segment table plus the sha256 of
the decoded image, which is what UniXNU pins as ``kernelcache_sha256``.

Usage:
    decode_kernelcache.py IPSW --soc mac13g --output out/macos26-assets
    decode_kernelcache.py kernelcache.release.mac13g --output out/macos26-assets
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

MH_MAGIC_64 = 0xFEEDFACF
LC_SEGMENT_64 = 0x19
CPU_NAMES = {0x0100000C: "arm64", 0x0200000C: "arm64e"}


def der_children(buf: bytes, off: int, end: int) -> list[tuple[int, int, int]]:
    out = []
    while off < end:
        tag = buf[off]
        off += 1
        length = buf[off]
        off += 1
        if length & 0x80:
            count = length & 0x7F
            length = int.from_bytes(buf[off:off + count], "big")
            off += count
        out.append((tag, off, length))
        off += length
    return out


def im4p_payload(blob: bytes) -> bytes:
    (tag, off, size), = der_children(blob, 0, len(blob))[:1]
    if tag != 0x30:
        raise ValueError("not a DER sequence")
    children = der_children(blob, off, off + size)
    if len(children) < 2 or blob[children[0][1]:children[0][1] + 4] != b"IM4P":
        raise ValueError("missing IM4P marker")
    payloads = [c for c in children if c[0] == 0x04 and c[2] > 4096]
    if not payloads:
        raise ValueError("no payload octet string")
    _, poff, plen = payloads[0]
    return blob[poff:poff + plen]


def decompress(payload: bytes) -> tuple[bytes, str]:
    if payload[:4] in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"):
        return payload, "none"
    try:
        import lzfse  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("payload is compressed; install the 'lzfse' wheel first") from exc
    return lzfse.decompress(payload), "lzfse"


def segment_table(data: bytes) -> list[dict[str, object]]:
    magic, cputype, cpusubtype, filetype, ncmds, _, _, _ = struct.unpack_from("<IiiIIIII", data, 0)
    if magic != MH_MAGIC_64:
        raise ValueError(f"unexpected Mach-O magic 0x{magic:08x}")
    segments = []
    off = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", data, off)
        if cmd == LC_SEGMENT_64:
            name, vmaddr, vmsize, fileoff, filesize = struct.unpack_from("<16sQQQQ", data, off + 8)
            segments.append({
                "name": name.rstrip(b"\0").decode(),
                "vmaddr": vmaddr,
                "vmsize": vmsize,
                "fileoff": fileoff,
                "filesize": filesize,
            })
        off += size
    return [{
        "cputype": CPU_NAMES.get(cputype, hex(cputype)),
        "cpusubtype": cpusubtype & 0xFFFFFFFF,
        "filetype": filetype,
        "ncmds": ncmds,
        "segments": segments,
    }]


def extract_from_ipsw(ipsw: Path, soc: str, workdir: Path) -> Path:
    entry = f"kernelcache.release.{soc}"
    subprocess.run(["7z", "x", "-y", f"-o{workdir}", str(ipsw), entry],
                   check=True, stdout=subprocess.DEVNULL)
    target = workdir / entry
    if not target.exists():
        raise SystemExit(f"{entry} not present in {ipsw}")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="IPSW or an extracted kernelcache.release.* file")
    parser.add_argument("--soc", default="mac13g", help="SoC tag when the source is an IPSW")
    parser.add_argument("--output", type=Path, required=True, help="directory for the decoded image")
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        src = args.source
        if src.suffix == ".ipsw":
            src = extract_from_ipsw(src, args.soc, Path(tmp))
        blob = src.read_bytes()

    payload = im4p_payload(blob)
    data, method = decompress(payload)
    table = segment_table(data)[0]
    digest = hashlib.sha256(data).hexdigest()

    out = args.output / f"kc.{args.soc}.macho"
    out.write_bytes(data)
    report = {
        "source": str(src),
        "soc": args.soc,
        "im4p_kind": blob[blob.find(b"krnl"):blob.find(b"krnl") + 4].decode(),
        "compression": method,
        "im4p_payload_bytes": len(payload),
        "decoded_bytes": len(data),
        "decoded_sha256": digest,
        **table,
    }
    (args.output / f"kc.{args.soc}.json").write_text(json.dumps(report, indent=2) + "\n")

    print(f"  {out}  {len(data)/1e6:.1f} MB  sha256 {digest}")
    print(f"  cputype {table['cputype']} filetype {table['filetype']} segments {len(table['segments'])}")
    for seg in table["segments"]:
        print(f"    {seg['name']:<18} vm 0x{seg['vmaddr']:x} size 0x{seg['vmsize']:x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
