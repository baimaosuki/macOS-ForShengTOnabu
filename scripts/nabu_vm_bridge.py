#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""16 KiB XNU VM <-> 4 KiB SM8150 SMMUv2/UFS DMA page granule bridge for Xiaomi Pad 5 (nabu)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

XNU_PAGE_SIZE = 0x4000
SMMU_PAGE_SIZE = 0x1000
SUBPAGES_PER_XNU_PAGE = XNU_PAGE_SIZE // SMMU_PAGE_SIZE

BANK0_START = 0x80000000
BANK0_SAFE_END = 0xB9900000
TZ_HOLE_START = 0xB9900000
TZ_HOLE_END = 0xC0000000
XNU_DRAM_START = 0xC0000000
XNU_DRAM_END = 0x200000000


def split_16k_page_to_4k_smmu(
    xnu_phys: int,
    xnu_iova: int | None = None,
    *,
    stream_id: int = 0x300,
) -> list[dict]:
    """Translate a 16 KiB XNU physical page into four 4 KiB SM8150 SMMUv2 subpage descriptors."""
    if xnu_iova is None:
        xnu_iova = xnu_phys
    if xnu_phys & (XNU_PAGE_SIZE - 1) or xnu_iova & (XNU_PAGE_SIZE - 1):
        raise ValueError(f'XNU page address must be 16 KiB aligned: phys={hex(xnu_phys)}, iova={hex(xnu_iova)}')
    if TZ_HOLE_START <= xnu_phys < TZ_HOLE_END or TZ_HOLE_START < xnu_phys + XNU_PAGE_SIZE <= TZ_HOLE_END:
        raise ValueError(f'DMA page overlaps SM8150 TrustZone hole: {hex(xnu_phys)}')
    if not (XNU_DRAM_START <= xnu_phys <= XNU_DRAM_END - XNU_PAGE_SIZE):
        raise ValueError(f'DMA page outside XNU-managed SM8150 DRAM window: {hex(xnu_phys)}')

    return [
        {
            'subpage_index': idx,
            'stream_id': stream_id,
            'phys': xnu_phys + idx * SMMU_PAGE_SIZE,
            'iova': xnu_iova + idx * SMMU_PAGE_SIZE,
            'bytes': SMMU_PAGE_SIZE,
        }
        for idx in range(SUBPAGES_PER_XNU_PAGE)
    ]


def nabu_vm_bridge_contract() -> dict:
    return {
        'board': 'nabu',
        'soc': 'SM8150',
        'xnu_page_size': XNU_PAGE_SIZE,
        'smmu_page_size': SMMU_PAGE_SIZE,
        'subpages_per_xnu_page': SUBPAGES_PER_XNU_PAGE,
        'smmu_base': hex(0x15000000),
        'ufs_stream_id': hex(0x300),
        'trustzone_hole': [hex(TZ_HOLE_START), hex(TZ_HOLE_END)],
        'xnu_dram_window': [hex(XNU_DRAM_START), hex(XNU_DRAM_END)],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Optional JSON output path')
    args = parser.parse_args()
    contract = nabu_vm_bridge_contract()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(contract, indent=2) + '\n')
    else:
        print(json.dumps(contract, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
