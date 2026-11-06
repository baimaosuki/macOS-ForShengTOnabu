#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Xiaomi Pad 5 (nabu / SM8150) ESP NABULOG2 dual-slot log manager."""
from __future__ import annotations

import json
from pathlib import Path

from nabu_log_volume import (
    BLOCK,
    CAPACITY,
    CONTRACT,
    FILE_BYTES,
    NAME,
    SLOT_BYTES,
    check_config,
    crc,
    decode_container,
    encode_slot,
    header_crc,
    make_config,
    u32,
    u64,
    validate_contract,
)

ROOT = Path(__file__).resolve().parents[1]


def get_nabulog_contract() -> dict:
    """Return and validate the fixed NABULOG2 contract."""
    contract = json.loads((ROOT / 'config/nabulog.json').read_text())
    validate_contract(contract)
    return contract


__all__ = [
    'BLOCK',
    'CAPACITY',
    'CONTRACT',
    'FILE_BYTES',
    'NAME',
    'SLOT_BYTES',
    'check_config',
    'crc',
    'decode_container',
    'encode_slot',
    'get_nabulog_contract',
    'header_crc',
    'make_config',
    'u32',
    'u64',
    'validate_contract',
]
