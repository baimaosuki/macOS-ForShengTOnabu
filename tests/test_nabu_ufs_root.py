#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Host tests for the macOS 27.3 root image writer.

The reader below is written from the kernel parser's field offsets
(UniXNU/drivers/NativeUfs/RootLayout.h) and not from the writer's constants, so
a writer that drifts from the kernel contract fails here.
"""
from __future__ import annotations

import hashlib
import json
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from prepare_macos27_root import RootImageError, prepare  # noqa: E402


def read_manifest(blob: bytes) -> dict:
    """Independent reader: SHNGROOT v1 as the kernel parses it."""
    assert blob[:8] == b'SHNGROOT', blob[:8]
    version, count, image_bytes = struct.unpack_from('<IIQ', blob, 8)
    assert version == 1 and 1 <= count <= 4, (version, count)
    assert hashlib.sha256(blob[:4064]).digest() == blob[4064:4096]
    windows = []
    for index in range(count):
        base = 24 + 128 * index
        name = blob[base:base + 32].split(b'\x00', 1)[0].decode('ascii')
        offset, size, sector, index_field = struct.unpack_from('<QQII', blob, base + 32)
        assert sector == 512 and index_field == 1, (sector, index_field)
        offset, size = struct.unpack_from('<QQ', blob, base + 32)[0:2]
        windows.append({'name': name, 'offset': offset, 'size': size,
                        'sha256': blob[base + 56:base + 88].hex()})
    return {'count': count, 'image_bytes': image_bytes, 'windows': windows}


def _spec(tmp: Path, sizes: tuple[int, int, int] = (0x1000, 0x800, 0x600),
          names: tuple[str, str, str] = ('OS', 'SystemOS', 'App')) -> Path:
    offsets = (0x1000, 0x2000, 0x3000)
    windows = []
    for name, size, offset in zip(names, sizes, offsets):
        source = tmp / ('%s.bin' % name)
        source.write_bytes(bytes((i * 7 + 1) & 0xFF for i in range(size)))
        windows.append({'name': name, 'source': str(source), 'offset_bytes': offset})
    spec = {'os_profile': 'macos-27.3-26C5035r', 'build': '26C5035r', 'windows': windows}
    path = tmp / 'spec.json'
    path.write_text(json.dumps(spec))
    return path


def test_manifest_matches_the_kernel_parser() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        record = prepare(_spec(tmp), tmp / 'out')
        parsed = read_manifest((tmp / 'out' / 'manifest.bin').read_bytes())
        assert parsed['count'] == 3, parsed
        assert [w['name'] for w in parsed['windows']] == ['OS', 'SystemOS', 'App']
        assert parsed['windows'][0]['offset'] == 0x1000
        assert parsed['windows'][0]['size'] == 0x1000
        assert parsed['image_bytes'] == record['image_bytes']
        for window, written in zip(parsed['windows'], record['windows']):
            assert window['sha256'] == written['sha256'], (window, written)


def test_windows_are_copied_at_their_offsets() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        prepare(_spec(tmp), tmp / 'out')
        image = (tmp / 'out' / 'root_image.bin').read_bytes()
        record = json.loads((tmp / 'out' / 'build.json').read_text())
        for window in record['windows']:
            start = window['offset_bytes']
            chunk = image[start:start + window['size_bytes']]
            assert hashlib.sha256(chunk).hexdigest() == window['sha256'], window['name']


def test_unaligned_offset_is_refused() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        spec = json.loads(_spec(tmp).read_text())
        spec['windows'][0]['offset_bytes'] = 0x1800
        path = tmp / 'bad.json'
        path.write_text(json.dumps(spec))
        try:
            prepare(path, tmp / 'out')
        except RootImageError as exc:
            assert '4096-aligned' in str(exc), exc
            return
        raise AssertionError('a misaligned window offset was accepted')


def test_partial_sector_source_is_refused() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        spec = json.loads(_spec(tmp).read_text())
        source = tmp / 'odd.bin'
        source.write_bytes(b'\x00' * 0x801)
        spec['windows'][1]['source'] = str(source)
        path = tmp / 'odd.json'
        path.write_text(json.dumps(spec))
        try:
            prepare(path, tmp / 'out')
        except RootImageError as exc:
            assert '512-byte sectors' in str(exc), exc
            return
        raise AssertionError('a partial-sector window was accepted')


def test_overlap_is_refused() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        spec = json.loads(_spec(tmp).read_text())
        spec['windows'][1]['offset_bytes'] = 0x1000
        path = tmp / 'overlap.json'
        path.write_text(json.dumps(spec))
        try:
            prepare(path, tmp / 'out')
        except (RootImageError, ValueError) as exc:
            assert 'overlap' in str(exc) or 'share' in str(exc) or 'behind' in str(exc), exc
            return
        raise AssertionError('overlapping windows were accepted')


def test_two_windows_is_allowed_but_a_recovery_pair_is_not_the_goal() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        spec = json.loads(_spec(tmp).read_text())
        spec['windows'] = spec['windows'][:2]
        path = tmp / 'pair.json'
        path.write_text(json.dumps(spec))
        record = prepare(path, tmp / 'out')
        assert len(record['windows']) == 2


def test_duplicate_names_are_refused() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        spec = json.loads(_spec(tmp).read_text())
        spec['windows'][2]['name'] = 'OS'
        path = tmp / 'dup.json'
        path.write_text(json.dumps(spec))
        try:
            prepare(path, tmp / 'out')
        except RootImageError as exc:
            assert 'distinct' in str(exc), exc
            return
        raise AssertionError('a duplicate window name was accepted')


def test_image_bytes_mismatch_is_refused() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        spec = json.loads(_spec(tmp).read_text())
        spec['image_bytes'] = 0x9999
        path = tmp / 'bytes.json'
        path.write_text(json.dumps(spec))
        try:
            prepare(path, tmp / 'out')
        except RootImageError as exc:
            assert 'image_bytes' in str(exc), exc
            return
        raise AssertionError('a wrong image_bytes claim was accepted')


def test_four_window_limit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        spec = json.loads(_spec(tmp).read_text())
        extra = tmp / 'Extra.bin'
        extra.write_bytes(b'\x00' * 0x200)
        spec['windows'].append({'name': 'Extra', 'source': str(extra),
                                'offset_bytes': 0x4000})
        path = tmp / 'four.json'
        path.write_text(json.dumps(spec))
        assert len(prepare(path, tmp / 'out')['windows']) == 4
        spec['windows'].append({'name': 'Fifth', 'source': str(extra),
                                'offset_bytes': 0x5000})
        path.write_text(json.dumps(spec))
        try:
            prepare(path, tmp / 'out2')
        except RootImageError as exc:
            assert '2..4 windows' in str(exc), exc
            return
        raise AssertionError('a fifth window was accepted')


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith('test_')]
    failures = 0
    for test in tests:
        try:
            test()
            print('  %-56s ok' % test.__name__)
        except Exception as exc:                               # noqa: BLE001
            failures += 1
            print('  %-56s FAIL %s' % (test.__name__, exc))
    print('%d/%d root image cases passed' % (len(tests) - failures, len(tests)))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
