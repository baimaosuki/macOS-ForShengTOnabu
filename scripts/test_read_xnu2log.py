#!/usr/bin/env python3
"""Verify the reader against the writer's linearized NABULOG2 snapshot contract."""
from __future__ import annotations

import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

import read_xnu2log as R
from nabu_log_volume import encode_slot, make_config

TOKEN = bytes(range(16))
BOOT = bytes(range(16, 32))
BUILD = b"test-bd".ljust(32, b"\0")


def slot(payload: bytes, sequence: int = 3, *, first: int = 0, token: bytes = TOKEN) -> bytes:
    return encode_slot(token, sequence, BOOT, payload, first=first, build_id=BUILD)


def container(a: bytes, b: bytes | None = None) -> bytes:
    return make_config(TOKEN) + a + (b if b is not None else bytes(R.SLOT_BYTES))


class ReadXnu2Log(unittest.TestCase):
    def cli(self, image: bytes, *arguments: str) -> tuple[subprocess.CompletedProcess, bytes | None, dict]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output, metadata = root / "XNU2LOG.BIN", root / "console.bin", root / "info.json"
            source.write_bytes(image)
            result = subprocess.run(
                [sys.executable, str(Path(R.__file__)), str(source), "--out", str(output),
                 "--info-json", str(metadata), *arguments], capture_output=True, text=True,
            )
            return result, output.read_bytes() if output.exists() else None, json.loads(metadata.read_text())

    def test_small_linear_and_legacy_parse_slot_fields(self):
        payload = b"nabu-stage-2 IOKit entry\n"
        image = container(slot(payload))
        parsed = R.parse_slot(image, R.SLOTS[0], TOKEN)
        self.assertEqual(parsed, dict(sequence=3, boot_id=BOOT.hex(), end=len(payload), first=0,
                                      length=len(payload), overwritten=0, lock_lost=0,
                                      build="test-bd", data=payload))
        self.assertIsNone(R.parse_slot(image, R.SLOTS[0], b"\xff" * 16))
        self.assertIsNone(R.parse_slot(image[:-1], R.SLOTS[1], TOKEN))
        result, data, info = self.cli(image, "--token", TOKEN.hex())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data, payload)
        self.assertTrue(info["decoded"])
        self.assertTrue(info["config_checksum_valid"])

    def test_actual_wrapped_ram_stream_is_stored_linear_and_token_is_automatic(self):
        # Produce more than one complete RAM ring, then snapshot its newest bytes
        # in the same oldest-to-newest order as nabu_capture_copy(first+at,...).
        length = R.CAPACITY + 1000
        stream = (bytes(range(251)) * ((length + 250) // 251))[:length]
        payload = stream[-R.CAPACITY:]
        image = container(slot(payload, 9, first=length - R.CAPACITY))
        parsed = R.parse_slot(image, R.SLOTS[0], TOKEN)
        self.assertEqual(parsed["data"], payload)
        self.assertEqual(parsed["first"], 1000)
        result, data, info = self.cli(image)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data, payload)
        self.assertEqual(info["committed_total"], length)
        self.assertEqual(info["ring_order"], "already_linearised")
        self.assertEqual(info["allocation_token"], TOKEN.hex())

    def test_corrupt_newer_payload_falls_back_to_older_slot(self):
        newer = bytearray(slot(b"newer", 10))
        newer[R.BLOCK] ^= 1
        result, data, info = self.cli(container(slot(b"older"), bytes(newer)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data, b"older")
        self.assertEqual(info["sequence"], 3)
        self.assertEqual(info["selected_slot"], 0)
        self.assertFalse(info["slots"][1]["valid"])
        self.assertIn("payload CRC", info["slots"][1]["error"])

    def test_config_corruption_is_rejected_even_with_valid_slot_and_explicit_token(self):
        image = bytearray(container(slot(b"valid slot")))
        image[32] ^= 1
        result, data, info = self.cli(bytes(image), "--token", TOKEN.hex())
        self.assertEqual(result.returncode, 1)
        self.assertIsNone(data)
        self.assertFalse(info["decoded"])
        self.assertIn("NABUCFG2", info["error"])

    def test_expected_token_mismatch_is_rejected(self):
        result, data, info = self.cli(container(slot(b"valid")), "--token", (b"\xff" * 16).hex())
        self.assertEqual(result.returncode, 1)
        self.assertIsNone(data)
        self.assertIn("supplied token", info["error"])

    def test_slot_token_must_match_config(self):
        result, data, info = self.cli(container(slot(b"wrong token", token=b"\xff" * 16)))
        self.assertEqual(result.returncode, 1)
        self.assertIsNone(data)
        self.assertIn("token/reserved", info["error"])

    def test_duplicate_valid_sequences_are_ambiguous(self):
        result, data, info = self.cli(container(slot(b"first", 8), slot(b"second", 8)))
        self.assertEqual(result.returncode, 1)
        self.assertIsNone(data)
        self.assertIn("duplicate slot sequences", info["error"])

    def test_container_size_is_strict(self):
        image = container(slot(b"valid"))
        for malformed in (image[:-1], image + b"\0"):
            with self.subTest(bytes=len(malformed)):
                result, data, info = self.cli(malformed)
                self.assertEqual(result.returncode, 1)
                self.assertIsNone(data)
                self.assertIn("exactly", info["error"])

    def test_reserved_header_fields_are_checked_even_with_valid_crc(self):
        malformed = bytearray(slot(b"valid payload"))
        malformed[144] = 1
        struct.pack_into("<I", malformed, 80, R.header_crc(malformed[:R.BLOCK], 80))
        image = container(bytes(malformed))
        self.assertIsNone(R.parse_slot(image, R.SLOTS[0], TOKEN))
        with self.assertRaisesRegex(ValueError, "token/reserved"):
            R.decode_image(image)


if __name__ == "__main__":
    unittest.main()
