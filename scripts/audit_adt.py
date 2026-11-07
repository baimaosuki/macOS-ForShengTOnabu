#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Static audit of a generated Apple device tree against its in-kernel consumers.

Every assertion below mirrors a hard-coded expectation in the kernel or driver
sources: pe_identify_machine (device_type/ranges/state/timebase/bus/clock),
ArmCpu::mapGic (intc compatible length, reg length, redistributor stride) and
ArmDTPlatform (required children, CHRP NVRAM proxy). Run it on every generated
.adt before flashing; a single failing byte is a boot-time fault.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path


def parse_adt(data: bytes, at: int = 0, path: str = "") -> tuple[dict, int]:
    """Decode the Apple ADT node format: u32 prop count, u32 child count,
    then (32-byte key, u32 length, value padded to four bytes) per property."""
    n_props, n_children = struct.unpack_from("<II", data, at)
    at += 8
    props: dict[str, bytes] = {}
    for _ in range(n_props):
        key, size = struct.unpack_from("<32sI", data, at)
        key = key.split(b"\0", 1)[0].decode("ascii")
        at += 36
        props[key] = data[at:at + size]
        at += (size + 3) & ~3
    name = props.get("name", b"").rstrip(b"\0").decode("ascii")
    cur = f"{path}/{name}" if path != "/" else f"/{name}"
    nodes: dict[str, dict[str, bytes]] = {cur: props}
    for _ in range(n_children):
        child, at = parse_adt(data, at, cur)
        nodes.update(child)
    return nodes, at


def audit(adt: Path) -> int:
    raw = adt.read_bytes()
    tree, end = parse_adt(raw)
    checks: list[tuple[str, bool, str]] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        checks.append((label, bool(ok), detail))

    check("ADT length self-consistent", end == len(raw), f"{end} vs {len(raw)}")
    arm_io = tree.get("/device-tree/arm-io", {})
    check("/arm-io device_type == arm-io",
          arm_io.get("device_type") == b"arm-io\0", repr(arm_io.get("device_type")))
    ranges = arm_io.get("ranges", b"")
    check("/arm-io ranges is 24 bytes", len(ranges) == 24, str(len(ranges)))
    check("/arm-io ranges[1] non-zero",
          len(ranges) == 24 and struct.unpack_from("<3Q", ranges)[1] != 0,
          hex(struct.unpack_from("<3Q", ranges)[1]) if len(ranges) == 24 else "-")
    cpu0 = tree.get("/device-tree/cpus/cpu@0", {})
    check("cpu@0 state == running", cpu0.get("state") == b"running\0", repr(cpu0.get("state")))
    tb = cpu0.get("timebase-frequency", b"")
    check("cpu@0 timebase-frequency non-zero",
          len(tb) in (4, 8) and int.from_bytes(tb, "little") > 0,
          repr(tb))
    for field in ("bus-frequency", "clock-frequency"):
        value = cpu0.get(field, b"")
        check(f"cpu@0 {field} non-zero",
              len(value) in (4, 8) and int.from_bytes(value, "little") > 0, repr(value))
    intc = tree.get("/device-tree/arm-io/intc", {})
    check("intc compatible == arm,gic-v3 (11 bytes)",
          intc.get("compatible") == b"arm,gic-v3\0" and len(intc.get("compatible", b"")) == 11,
          repr(intc.get("compatible")))
    check("intc reg is 32 bytes", len(intc.get("reg", b"")) == 32, str(len(intc.get("reg", b""))))
    stride = intc.get("redistributor-stride", b"")
    check("intc redistributor-stride == 0x20000",
          len(stride) == 8 and struct.unpack("<Q", stride)[0] == 0x20000, repr(stride))
    for required in ("/device-tree/chosen", "/device-tree/cpus", "/device-tree/arm-io",
                     "/device-tree/defaults", "/device-tree/options"):
        check(f"node {required}", required in tree)
    chosen = tree.get("/device-tree/chosen", {})
    check("chosen nvram-proxy-data is 8192 bytes",
          len(chosen.get("nvram-proxy-data", b"")) == 8192,
          str(len(chosen.get("nvram-proxy-data", b""))))
    check("chosen dram-base/dram-size present",
          len(chosen.get("dram-base", b"")) == 8 and len(chosen.get("dram-size", b"")) == 8)
    check("root compatible names the platform",
          b"xiaomi,nabu" in tree.get("/device-tree", {}).get("compatible", b""))

    width = max(len(label) for label, _, _ in checks)
    failures = 0
    for label, ok, detail in checks:
        failures += 0 if ok else 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {label.ljust(width)} {detail}".rstrip())
    print(f"  nodes={len(tree)} bytes={len(raw)}")
    print("  RESULT:", "ALL PASS" if not failures else f"{failures} FAILURE(S)")
    return 0 if not failures else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("adt", type=Path, nargs="?", default=Path("out/nabu/esp/Payloads/kernel.macho.adt"))
    sys.exit(audit(parser.parse_args().adt))


if __name__ == "__main__":
    main()
