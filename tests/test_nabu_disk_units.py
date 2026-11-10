#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit contract tests for the nabu root disk window table."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from nabu_disk_units import (IMAGE_SECTOR_BYTES, UFS_BLOCK_BYTES, Bytes, Lba,  # noqa: E402
                             UnitMismatch, Window, check_table, window_at, window_bytes,
                             within)


def test_block_index_converts_once() -> None:
    assert Lba(0x12345, UFS_BLOCK_BYTES).to_bytes().value == 0x12345 * 4096
    assert Lba(0x12345, UFS_BLOCK_BYTES).rebase(IMAGE_SECTOR_BYTES).index == 0x12345 * 8


def test_block_index_is_not_a_byte_count() -> None:
    try:
        Lba(1, UFS_BLOCK_BYTES) + Bytes(4096)
    except UnitMismatch:
        return
    raise AssertionError('a block index combined with bytes without conversion')


def test_sector_index_rebases_to_blocks_only_when_whole() -> None:
    assert Lba(8, IMAGE_SECTOR_BYTES).rebase(UFS_BLOCK_BYTES).index == 1
    try:
        Lba(9, IMAGE_SECTOR_BYTES).rebase(UFS_BLOCK_BYTES)
    except UnitMismatch:
        return
    raise AssertionError('a partial block was truncated instead of refused')


def test_window_at_uses_the_controllers_unit() -> None:
    window = window_at('OS', Lba(0x4000, UFS_BLOCK_BYTES), 0x2000)
    assert window.offset.value == 0x4000 * 4096
    assert window.length.value == 0x2000 * 4096
    assert window.blocks() == 0x4000 and window.block_count() == 0x2000
    assert window.sectors() == 0x4000 * 8 and window.sector_count() == 0x2000 * 8


def test_sector_unaligned_window_is_refused() -> None:
    window = window_bytes('App', 0x1001, 1024)
    try:
        window.sectors()
    except UnitMismatch:
        return
    raise AssertionError('sector-unaligned offset %s passed' % window.offset)


def test_block_alignment_is_checked_separately() -> None:
    window = window_bytes('App', 0x1800, 0x1800)
    assert window.sectors() == 0xC and window.sector_count() == 0xC
    try:
        window.block_count()
    except UnitMismatch:
        return
    raise AssertionError('sector-aligned but block-unaligned length passed as blocks')


def test_overlap_is_refused() -> None:
    table = [window_bytes('OS', 0x1000, 0x2000), window_bytes('App', 0x2000, 0x1000)]
    try:
        check_table(table)
    except ValueError as exc:
        assert 'overlaps' in str(exc), exc
        return
    raise AssertionError('overlapping windows were accepted')


def test_adjacent_windows_pass() -> None:
    table = [window_bytes('OS', 0x1000, 0x2000), window_bytes('SystemOS', 0x3000, 0x1000)]
    check_table(table)
    assert [w.name for w in sorted(table, key=lambda w: w.offset.value)] == ['OS', 'SystemOS']


def test_medium_bound_is_enforced() -> None:
    within(0x3000, window_bytes('OS', 0x1000, 0x2000))          # end == medium is legal
    try:
        within(0x2000, window_bytes('OS', 0x1000, 0x2000))
    except ValueError as exc:
        assert 'past the' in str(exc), exc
        return
    raise AssertionError('window past the medium was accepted')


def test_duplicate_name_is_refused() -> None:
    try:
        check_table([window_bytes('OS', 0x1000, 0x1000), window_bytes('OS', 0x2000, 0x1000)])
    except ValueError as exc:
        assert 'duplicate' in str(exc), exc
        return
    raise AssertionError('duplicate window name accepted')


def test_window_needs_bytes() -> None:
    try:
        Window('OS', 0x1000, Bytes(0x1000))  # type: ignore[arg-type]
    except UnitMismatch:
        return
    raise AssertionError('a bare int offset was accepted as Bytes')


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith('test_')]
    failures = 0
    for test in tests:
        try:
            test()
            print('  %-52s ok' % test.__name__)
        except Exception as exc:                              # noqa: BLE001
            failures += 1
            print('  %-52s FAIL %s' % (test.__name__, exc))
    print('%d/%d unit-contract cases passed' % (len(tests) - failures, len(tests)))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
