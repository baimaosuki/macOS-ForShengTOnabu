#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Host tests for the read-only UFS image contract (GPT, windows, probes)."""
from __future__ import annotations

import hashlib
import json
import struct
import sys
import tempfile
import uuid
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from nabu_disk_units import UFS_BLOCK_BYTES, UnitMismatch  # noqa: E402
from nabu_ufs_image import (GPT_ENTRY_BYTES, GPT_ENTRY_COUNT, GPT_SIGNATURE,  # noqa: E402
                            ImageError, compare, parse_gpt, probe_offsets, readcheck,
                            windows_for)

SECTOR = 512
TOTAL_SECTORS = 0x8000                     # 16 MiB image

TYPE_OS = '7C3457EF-0000-11AA-AA11-00306543ECAC'
TYPE_SYSTEMOS = '7C3457EF-0000-11AA-AA11-00306543ECAD'
TYPE_APP = '7C3457EF-0000-11AA-AA11-00306543ECAE'


def _entry(index: int, type_hex: str, first: int, last: int, name: str) -> bytes:
    body = bytearray(GPT_ENTRY_BYTES)
    raw = uuid.UUID(type_hex).bytes_le
    body[0:16] = raw
    body[16:32] = uuid.UUID(int=0x1000 + index).bytes_le
    struct.pack_into('<QQQ', body, 32, first, last, 0)
    encoded = name.encode('utf-16-le')[:70]
    body[56:56 + len(encoded)] = encoded
    return bytes(body)


def build_image(parts: list[tuple[str, str, int, int]], *, total: int = TOTAL_SECTORS,
                break_header_crc: bool = False, break_entry_crc: bool = False) -> bytes:
    image = bytearray(total * SECTOR)
    alt = total - 1
    image[446 + 4] = 0xEE
    struct.pack_into('<I', image, 446 + 8, 1)
    struct.pack_into('<I', image, 446 + 12, total - 1)
    image[510:512] = b'\x55\xaa'
    table = bytearray(GPT_ENTRY_COUNT * GPT_ENTRY_BYTES)
    for index, (type_hex, name, first, last) in enumerate(parts):
        table[index * GPT_ENTRY_BYTES:(index + 1) * GPT_ENTRY_BYTES] = _entry(index, type_hex, first, last, name)
    entries_crc = zlib.crc32(bytes(table)) & 0xffffffff
    if break_entry_crc:
        entries_crc ^= 0x1
    image[2 * SECTOR:2 * SECTOR + len(table)] = table

    def header(my_lba: int, alt_lba: int, entries_lba: int) -> bytes:
        head = bytearray(SECTOR)
        head[0:8] = GPT_SIGNATURE
        struct.pack_into('<I', head, 8, 0x00010000)
        struct.pack_into('<I', head, 12, 92)
        struct.pack_into('<QQ', head, 24, my_lba, alt_lba)
        struct.pack_into('<QQ', head, 40, 34, total - 34)
        head[56:72] = uuid.UUID(int=7).bytes_le
        struct.pack_into('<Q', head, 72, entries_lba)
        struct.pack_into('<III', head, 80, GPT_ENTRY_COUNT, GPT_ENTRY_BYTES, entries_crc)
        crc = zlib.crc32(bytes(head[:92])) & 0xffffffff
        if break_header_crc and my_lba == 1:
            crc ^= 0x1
        struct.pack_into('<I', head, 16, crc)
        return bytes(head)

    image[1 * SECTOR:2 * SECTOR] = header(1, alt, 2)
    backup_entries = alt - 32
    image[backup_entries * SECTOR:backup_entries * SECTOR + len(table)] = table
    image[alt * SECTOR:(alt + 1) * SECTOR] = header(alt, 1, backup_entries)
    for _, name, first, last in parts:
        seed = hashlib.sha256(name.encode()).digest()
        for sector in range(first, last + 1):
            block = bytes((seed[(sector + i) % len(seed)] ^ 0x5A) for i in range(SECTOR))
            image[sector * SECTOR:(sector + 1) * SECTOR] = block
    return bytes(image)


def _image(parts=None, **kwargs) -> bytes:
    default = [(TYPE_OS, 'OS', 0x40, 0x23F),
               (TYPE_SYSTEMOS, 'SystemOS', 0x240, 0x43F),
               (TYPE_APP, 'App', 0x440, 0x63F)]
    return build_image(parts or default, **kwargs)


def test_parse_gpt_reads_names_and_ranges() -> None:
    parts = parse_gpt(_image())
    assert [p.name for p in parts] == ['OS', 'SystemOS', 'App'], parts
    assert parts[0].first_lba == 0x40 and parts[0].last_lba == 0x23F
    assert parts[0].type_guid == TYPE_OS


def test_windows_are_block_aligned_and_ordered() -> None:
    data = _image()
    windows = windows_for(parse_gpt(data), ('OS', 'SystemOS', 'App'), disk_bytes=len(data))
    assert windows['OS'].blocks() == 0x40 // 8
    assert windows['OS'].block_count() == (0x240 - 0x40) // 8
    assert windows['App'].sector_count() == 0x200


def test_misaligned_partition_is_refused() -> None:
    data = _image([(TYPE_OS, 'OS', 0x41, 0x23F)])
    try:
        windows_for(parse_gpt(data), ('OS',), disk_bytes=len(data))
    except ImageError as exc:
        assert '4096' in str(exc), exc
        return
    raise AssertionError('a partition starting at sector 0x41 was accepted as a window')


def test_missing_partition_is_refused() -> None:
    data = _image([(TYPE_OS, 'OS', 0x40, 0x23F)])
    try:
        windows_for(parse_gpt(data), ('OS', 'SystemOS'), disk_bytes=len(data))
    except ImageError as exc:
        assert 'lacks partition' in str(exc), exc
        return
    raise AssertionError('a missing window name was accepted')


def test_header_crc_corruption_is_refused() -> None:
    try:
        parse_gpt(_image(break_header_crc=True))
    except ImageError as exc:
        assert 'header CRC' in str(exc), exc
        return
    raise AssertionError('a corrupt primary header passed')


def test_entry_crc_corruption_is_refused() -> None:
    try:
        parse_gpt(_image(break_entry_crc=True))
    except ImageError as exc:
        assert 'entry array CRC' in str(exc), exc
        return
    raise AssertionError('a corrupt entry array passed')


def test_backup_gpt_must_exist() -> None:
    good = bytearray(_image())
    tail = (TOTAL_SECTORS - 1) * SECTOR
    good[tail:tail + 8] = b'\x00' * 8
    try:
        parse_gpt(bytes(good))
    except ImageError as exc:
        assert 'backup' in str(exc), exc
        return
    raise AssertionError('a missing backup GPT passed')


def test_probes_cover_first_last_and_cross_block() -> None:
    data = _image()
    windows = windows_for(parse_gpt(data), ('OS',), disk_bytes=len(data))
    probes = probe_offsets(windows['OS'], 4)
    offsets = [offset for offset, _ in probes]
    assert offsets[0] == 0
    assert offsets[1] == windows['OS'].length.value - UFS_BLOCK_BYTES
    assert offsets[2] == UFS_BLOCK_BYTES - 8
    assert len(offsets) == len(set(offsets))
    for offset, length in probes:
        assert offset + length <= windows['OS'].length.value


def test_readcheck_matches_identity_dump() -> None:
    data = _image()
    windows = windows_for(parse_gpt(data), ('OS', 'SystemOS', 'App'), disk_bytes=len(data))
    with tempfile.TemporaryDirectory() as tmp:
        image = Path(tmp) / 'root.img'
        image.write_bytes(data)
        report = readcheck(image, windows, count=2)
        dumps = Path(tmp) / 'dumps'
        dumps.mkdir()
        for name, window in report['windows'].items():
            for probe in window['probes']:
                start = probe['absolute']
                (dumps / ('%s-%x-%x.bin' % (name, start, probe['length']))).write_bytes(
                    data[start:start + probe['length']])
        result = compare(report, dumps)
        assert result['passed'], result
        assert all(row['status'] == 'match' for row in result['results'])


def test_readcheck_detects_a_corrupted_dump() -> None:
    data = _image()
    windows = windows_for(parse_gpt(data), ('OS',), disk_bytes=len(data))
    with tempfile.TemporaryDirectory() as tmp:
        image = Path(tmp) / 'root.img'
        image.write_bytes(data)
        report = readcheck(image, windows, count=1)
        dumps = Path(tmp) / 'dumps'
        dumps.mkdir()
        first = report['windows']['OS']['probes'][0]
        start = first['absolute']
        corrupted = bytearray(data[start:start + first['length']])
        corrupted[0] ^= 0xFF
        (dumps / ('OS-%x-%x.bin' % (start, first['length']))).write_bytes(bytes(corrupted))
        result = compare(report, dumps)
        assert not result['passed'], result
        assert result['results'][0]['status'] == 'mismatch'


def test_missing_dump_is_reported_not_ignored() -> None:
    data = _image()
    windows = windows_for(parse_gpt(data), ('OS',), disk_bytes=len(data))
    with tempfile.TemporaryDirectory() as tmp:
        image = Path(tmp) / 'root.img'
        image.write_bytes(data)
        report = readcheck(image, windows, count=1)
        dumps = Path(tmp) / 'dumps'
        dumps.mkdir()
        result = compare(report, dumps)
        assert not result['passed']
        assert result['results'][0]['status'] == 'missing'


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
    print('%d/%d image-contract cases passed' % (len(tests) - failures, len(tests)))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
