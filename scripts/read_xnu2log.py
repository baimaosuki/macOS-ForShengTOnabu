#!/usr/bin/env python3
"""Decode a CRC-validated NABULOG2 journal from the nabu ESP.

The writer linearizes each RAM-ring snapshot into its on-disk slot, including
snapshots taken after the 4 MiB RAM ring wraps. The config and both slots use
the shared nabu_log_volume contract; the newest valid sequence wins.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Keep these names available to existing callers of this script.
from nabu_log_volume import (
    BLOCK, CAPACITY, SLOT_BYTES, FILE_BYTES, check_config, decode_container,
    header_crc, make_config, u32, u64,
)

SLOTS = (BLOCK, BLOCK + SLOT_BYTES)


def _legacy_slot(data: bytes, info: dict) -> dict:
    """Preserve the public parse_slot result fields."""
    return {
        "sequence": info["sequence"],
        "boot_id": info["boot_id"],
        "end": info["committed_total"],
        "first": info["ring_overwritten"],
        "length": info["payload_bytes"],
        "overwritten": info["ring_overwritten"],
        "lock_lost": info["lost_on_contention"],
        "build": bytes.fromhex(info["build_id"]).rstrip(b"\0").decode("ascii", "replace"),
        "data": data,
    }


def parse_slot(image: bytes, base: int, token: bytes) -> dict | None:
    """Validate one slot independently, retaining the historical public API.

    This helper deliberately does not validate the source container's config or
    its other slot. Use decode_image for complete-container validation.
    """
    if base < 0 or base + SLOT_BYTES > len(image):
        return None
    try:
        # Reuse the shared validator with this slot alone and the caller's token.
        container = make_config(token) + image[base:base + SLOT_BYTES] + bytes(SLOT_BYTES)
        data, info = decode_container(container)
    except ValueError:
        return None
    return _legacy_slot(data, info)


def decode_image(image: bytes, token: bytes | None = None) -> tuple[bytes, dict]:
    """Validate the full container and, if supplied, the expected allocation token."""
    data, info = decode_container(image)
    if token is not None and token != bytes.fromhex(info["allocation_token"]):
        raise ValueError("supplied token does not match the validated NABUCFG2 config")
    return data, info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("image", type=Path)
    ap.add_argument("--token", help="expected 32 hex digits; defaults to the CRC-validated config token")
    ap.add_argument("--out", type=Path, help="write the decoded text stream here")
    ap.add_argument("--info-json", type=Path, help="write validation metadata or a decode error as JSON")
    args = ap.parse_args()

    try:
        token = None
        if args.token is not None:
            token = bytes.fromhex(args.token)
            if len(token) != 16:
                raise ValueError("--token must contain exactly 32 hex digits")
        data, info = decode_image(args.image.read_bytes(), token)
    except (OSError, ValueError) as error:
        print(f"cannot decode NABULOG2: {error}", file=sys.stderr)
        if args.info_json:
            args.info_json.write_text(json.dumps(dict(decoded=False, error=str(error)), indent=2) + "\n")
        return 1

    slot = _legacy_slot(data, info)
    print(f"slot sequence : {slot['sequence']}")
    print(f"boot id       : {slot['boot_id']}")
    print(f"committed     : {slot['length']} bytes (end={slot['end']}, first={slot['first']})")
    print(f"overwritten   : {slot['overwritten']}   lock_lost: {slot['lock_lost']}")
    print(f"build         : {slot['build'] or '(unset)'}")
    print(f"valid slots   : {sum(entry['valid'] for entry in info['slots'])}")
    if args.info_json:
        args.info_json.write_text(json.dumps(dict(info, decoded=True), indent=2) + "\n")
    if args.out:
        args.out.write_bytes(data)
        print(f"wrote {args.out}")
    else:
        sys.stdout.write(data.decode("utf-8", "backslashreplace"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
