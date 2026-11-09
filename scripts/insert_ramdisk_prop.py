#!/usr/bin/env python3
"""Insert exactly one property (`RAMDisk` = 16 zero bytes) into /chosen/memory-map.

Minimal-diff rule: take the working device tree byte-for-byte, add the single
node DarwinBoot looks for, and assert that nothing else changed. Regenerating
the whole tree from native_devtree.py is explicitly not what this does.

Format as measured on the real ADT (no guesswork):

  node      := u32 properties, u32 children, property*, node*
  property  := char name[32], u32 length, value padded to 4-byte alignment

Usage: insert_ramdisk_prop.py <in.adt> <out.adt>
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

NODE_HEADER = 8
PROP_HEADER = 36
PAD = lambda n: (n + 3) & ~3  # noqa: E731 - keeps the arithmetic visible
PATH_SLOT = 16


def parse(data: bytes) -> tuple[list[dict], int]:
    records: list[dict] = []

    def prop_name(at: int) -> str:
        return data[at:at + 32].split(b"\0", 1)[0].decode("ascii", "replace")

    def peek_child_name(at: int) -> str:
        props = struct.unpack_from("<I", data, at)[0]
        cursor = at + NODE_HEADER
        for _ in range(props):
            name = prop_name(cursor)
            length = struct.unpack_from("<I", data, cursor + 32)[0]
            if name == "name":
                start = cursor + PROP_HEADER
                return data[start:start + length].split(b"\0", 1)[0].decode("ascii", "replace")
            cursor += PROP_HEADER + PAD(length)
        return ""

    def node(at: int, path: tuple[str, ...]) -> int:
        start = at
        props, children = struct.unpack_from("<II", data, at)
        at += NODE_HEADER
        names: list[str] = []
        for _ in range(props):
            if at + PROP_HEADER > len(data):
                raise SystemExit(f"truncated property header at {at:#x} in {path}")
            name = prop_name(at)
            length = struct.unpack_from("<I", data, at + 32)[0]
            at += PROP_HEADER + PAD(length)
            names.append(name)
        records.append({"path": path, "start": start, "props": props,
                        "children": children, "names": names, "body_end": at})
        for _ in range(children):
            child = peek_child_name(at)
            at = node(at, path + (child,))
        return at

    end = node(0, ())
    return records, end


def main() -> int:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "kernel.macho.adt")
    dst = Path(sys.argv[2] if len(sys.argv) > 2 else "kernel.macho.adt.ramdisk")
    data = src.read_bytes()

    records, end = parse(data)
    if end != len(data):
        raise SystemExit(f"tree does not consume the file: parsed {end}, size {len(data)}")

    target = next((r for r in records if r["path"] == ("chosen", "memory-map")), None)
    if target is None:
        raise SystemExit("/chosen/memory-map not found")
    if "RAMDisk" in target["names"]:
        raise SystemExit("RAMDisk already present")
    print(f"memory-map at {target['start']:#x}: {target['props']} props "
          f"{target['names']}, {target['children']} children")

    insert_at = target["body_end"]
    record = b"RAMDisk".ljust(32, b"\0") + struct.pack("<I", PATH_SLOT) + bytes(PATH_SLOT)
    patched = bytearray(data[:insert_at] + record + data[insert_at:])
    struct.pack_into("<I", patched, target["start"], target["props"] + 1)
    out = bytes(patched)

    after, after_end = parse(out)
    if after_end != len(out):
        raise SystemExit("patched tree does not parse cleanly")
    if len(after) != len(records):
        raise SystemExit(f"node count changed: {len(records)} -> {len(after)}")
    lookup = {r["path"]: r for r in after}
    mm = lookup[("chosen", "memory-map")]
    if mm["names"] != target["names"] + ["RAMDisk"]:
        raise SystemExit(f"unexpected memory-map props: {mm['names']}")
    if mm["children"] != target["children"]:
        raise SystemExit("child count changed")
    for rec in records:
        if rec["path"] == ("chosen", "memory-map"):
            continue
        other = lookup.get(rec["path"])
        if other is None or other["names"] != rec["names"] or other["children"] != rec["children"]:
            raise SystemExit(f"collateral change at {rec['path']}")

    dst.write_bytes(out)
    print(f"wrote {dst}: {len(data)} -> {len(out)} bytes (+{len(out) - len(data)})")
    print(f"/chosen/memory-map props {target['names']} -> {mm['names']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
