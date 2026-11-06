#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate and emit the Xiaomi Pad 5 (nabu / SM8150) GOP scanout & IOMobileFramebuffer translation contract."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NABU_GOP_HEADER = ROOT / 'platform' / 'nabu' / 'macos26' / 'display' / 'nabu_gop_scanout.h'


def nabu_gop_contract(*, orientation: str = 'landscape') -> dict:
    """Return verified GOP framebuffer & scanout geometry for Xiaomi Pad 5 (nabu / SM8150)."""
    if orientation not in ('landscape', 'portrait'):
        raise ValueError(f'unsupported Nabu GOP orientation: {orientation}')

    header_text = NABU_GOP_HEADER.read_text(encoding='utf-8')
    for token in (
        'NABU_GOP_PHYS_BASE        0x9c000000ULL',
        'NABU_GOP_CARVEOUT_SIZE    0x01000000ULL',
        'NABU_GOP_PANEL_WIDTH      1600U',
        'NABU_GOP_PANEL_HEIGHT     2560U',
        'NABU_GOP_PANEL_STRIDE     6400U',
        'nabuGopScanoutFrameRotated',
    ):
        if token not in header_text:
            raise ValueError(f'missing Nabu GOP scanout token in nabu_gop_scanout.h: {token}')

    phys_base = 0x9C000000
    carveout_size = 0x01000000
    panel_width = 1600
    panel_height = 2560
    panel_stride = 6400
    active_bytes = panel_stride * panel_height

    if active_bytes > carveout_size:
        raise ValueError('active GOP scanout exceeds 16 MiB ABL carveout')
    if not (0x80000000 <= phys_base < phys_base + carveout_size <= 0xB9900000):
        raise ValueError('Nabu GOP carveout must reside in DRAM Bank 0 below the TrustZone hole')

    desktop_width = 2560 if orientation == 'landscape' else 1600
    desktop_height = 1600 if orientation == 'landscape' else 2560
    rotation_degrees = 90 if orientation == 'landscape' else 0

    return {
        'board': 'nabu',
        'soc': 'SM8150',
        'component': 'NativeGOPDisplay',
        'bundle_id': 'org.crossdarwin.nabu.NativeGOPDisplay',
        'framebuffer_phys_base': hex(phys_base),
        'framebuffer_carveout_bytes': carveout_size,
        'framebuffer_active_bytes': active_bytes,
        'panel_width': panel_width,
        'panel_height': panel_height,
        'panel_stride': panel_stride,
        'pixel_format': 'BGRA8888',
        'desktop_orientation': orientation,
        'desktop_width': desktop_width,
        'desktop_height': desktop_height,
        'rotation_degrees': rotation_degrees,
        'outside_xnu_dram_window': True,
        'xnu_dram_base': hex(0xC0000000),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--orientation', choices=('landscape', 'portrait'), default='landscape')
    parser.add_argument('--output', type=Path, help='Optional JSON contract output path')
    args = parser.parse_args()

    contract = nabu_gop_contract(orientation=args.orientation)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(contract, indent=2) + '\n')
    else:
        print(json.dumps(contract, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
