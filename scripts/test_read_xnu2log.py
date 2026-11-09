#!/usr/bin/env python3
"""Self-test for read_xnu2log.py: synthesize committed slots and decode them.

Covers the three shapes that matter:
  A. small linear stream   (end < capacity)
  B. ring-wrapped stream   (end - first == capacity, start != 0)
  C. corrupted payload CRC (must be rejected)
"""
from __future__ import annotations

import struct
import zlib

import read_xnu2log as R

TOKEN = bytes(range(16))
CAP = R.CAPACITY


def header(sequence: int, end: int, length: int, payload_crc: int, token: bytes) -> bytes:
    first = end - length
    block = bytearray(R.BLOCK)
    block[0:8] = b"NABULOG2"
    struct.pack_into("<I", block, 8, 2)
    struct.pack_into("<I", block, 12, R.BLOCK)
    struct.pack_into("<I", block, 16, 1)
    struct.pack_into("<I", block, 20, CAP)
    struct.pack_into("<Q", block, 24, sequence)
    block[32:48] = bytes(range(16, 32))
    struct.pack_into("<Q", block, 48, end)
    struct.pack_into("<Q", block, 56, 0)
    struct.pack_into("<Q", block, 64, first)
    struct.pack_into("<I", block, 72, length)
    struct.pack_into("<I", block, 76, payload_crc)
    struct.pack_into("<I", block, 80, 0)
    struct.pack_into("<Q", block, 88, first)
    block[96:112] = token
    block[112:112 + 7] = b"test-bd"
    struct.pack_into("<I", block, 80, R.header_crc(bytes(block), 80))
    return bytes(block)


def ring(payload: bytes, start: int) -> bytes:
    """Place payload into a ring at `start`, wrapping as needed."""
    buf = bytearray(CAP)
    head = payload[:CAP - start]
    buf[start:start + len(head)] = head
    buf[:len(payload) - len(head)] = payload[len(head):]
    return bytes(buf)


def build(slot_index: int, payload: bytes, sequence: int, start: int, corrupt: bool = False) -> bytes:
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    if corrupt:
        crc ^= 0xDEADBEEF
    image = bytearray(R.FILE_BYTES)
    base = R.SLOTS[slot_index]
    image[base:base + R.BLOCK] = header(sequence, start + len(payload), len(payload), crc, TOKEN)
    image[base + R.BLOCK:base + R.SLOT_BYTES] = ring(payload, start)
    return bytes(image)


def run(image: bytes, label: str) -> dict | None:
    got = [s for s in (R.parse_slot(image, b, TOKEN) for b in R.SLOTS) if s]
    best = max(got, key=lambda s: s["sequence"]) if got else None
    print(f"  {label}: slots={len(got)} seq={best['sequence'] if best else None} "
          f"len={best['length'] if best else None}")
    return best


def main() -> int:
    failures = 0

    # A: linear, small
    text_a = b"sheng-stage-2 IOKit entry\ntrust_cache_init: skipped\n"
    img = build(0, text_a, 3, 0)
    got = run(img, "A linear")
    ok = got is not None and got["data"] == text_a
    print(f"    payload match = {ok}")
    failures += 0 if ok else 1

    # B: wrapped ring, full capacity, start 1000
    body = bytes((i * 7) & 0xFF for i in range(CAP))
    img = build(1, body, 9, 1000)
    got = run(img, "B wrapped")
    ok = got is not None and got["data"] == body and got["first"] == 1000
    print(f"    payload match = {ok} first={got['first'] if got else None}")
    failures += 0 if ok else 1

    # C: corrupted payload must be rejected, and the valid other slot must win
    img = bytearray(build(0, text_a, 3, 0))
    bad = build(1, b"corrupt", 10, 0, corrupt=True)
    img[R.SLOTS[1]:R.SLOTS[1] + R.FILE_BYTES - R.SLOTS[1]] = bad[R.SLOTS[1]:R.FILE_BYTES]
    got = run(bytes(img), "C corrupted newer slot")
    ok = got is not None and got["sequence"] == 3 and got["data"] == text_a
    print(f"    fell back to older valid slot = {ok}")
    failures += 0 if ok else 1

    # D: token mismatch must reject everything
    wrong = [s for s in (R.parse_slot(build(0, text_a, 3, 0), b, b"\xff" * 16) for b in R.SLOTS) if s]
    print(f"  D wrong token: slots={len(wrong)}")
    failures += 0 if not wrong else 1

    print("FAILURES:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
