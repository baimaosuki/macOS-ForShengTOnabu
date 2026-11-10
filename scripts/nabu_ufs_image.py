#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only UFS image contract: GPT parsing, root windows and read probes.

The device publishes 4096-byte logical blocks (NabuBoard.h); a disk image is a
512-byte-sector stream.  Everything here converts through nabu_disk_units so a
window can never be an untyped number, and every conversion either lands on a
block boundary or is refused.

  python3 scripts/nabu_ufs_image.py layout out/root.img
  python3 scripts/nabu_ufs_image.py probes out/root.img --config config/macos27.json
  python3 scripts/nabu_ufs_image.py check  out/root.img --dumps dumps/ --config config/macos27.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nabu_disk_units import (IMAGE_SECTOR_BYTES, UFS_BLOCK_BYTES, Bytes, Lba, UnitMismatch,  # noqa: E402
                             Window, check_table, window_bytes, within)

GPT_SIGNATURE = b'EFI PART'
GPT_ENTRY_BYTES = 128
GPT_ENTRY_COUNT = 128
MAX_GPT_SECTORS = 4096          # a 512-byte-sector entry array of 128 entries is 32 sectors


class ImageError(ValueError):
    """The image or its GPT violates the contract."""


@dataclass(frozen=True)
class Partition:
    index: int
    type_guid: str
    unique_guid: str
    first_lba: int
    last_lba: int
    name: str
    sector_bytes: int = IMAGE_SECTOR_BYTES

    @property
    def offset(self) -> Bytes:
        return Bytes(self.first_lba * self.sector_bytes)

    @property
    def length(self) -> Bytes:
        return Bytes((self.last_lba - self.first_lba + 1) * self.sector_bytes)

    def window(self) -> Window:
        return Window(self.name, self.offset, self.length)


def _guid(raw: bytes) -> str:
    if len(raw) != 16:
        raise ImageError('GUID is not 16 bytes')
    a, b, c = struct.unpack_from('<IHH', raw, 0)
    d = raw[8:10]
    e = raw[10:16]
    return ('%08X-%04X-%04X-%s-%s' % (a, b, c, d.hex().upper(), e.hex().upper()))


def _decode_name(raw: bytes) -> str:
    text = raw.decode('utf-16-le', 'replace').split('\x00', 1)[0]
    return text.strip() or '-'


def parse_gpt(data: bytes, *, sector_bytes: int = IMAGE_SECTOR_BYTES,
              require_backup: bool = True) -> list[Partition]:
    """Parse and CRC-check the primary (and, by default, the backup) GPT."""
    if sector_bytes not in (512, 4096):
        raise ImageError('unsupported sector size %d' % sector_bytes)
    if len(data) < sector_bytes * 4:
        raise ImageError('image shorter than a protective MBR plus one header')
    if data[510:512] != b'\x55\xaa':
        raise ImageError('no protective MBR signature')
    header = data[sector_bytes:sector_bytes * 2]
    if header[0:8] != GPT_SIGNATURE:
        raise ImageError('primary GPT signature missing at LBA 1')
    header_size = struct.unpack_from('<I', header, 12)[0]
    if not 92 <= header_size <= sector_bytes:
        raise ImageError('primary header size %d out of range' % header_size)
    stored_crc = struct.unpack_from('<I', header, 16)[0]
    probe = bytearray(header[:header_size])
    probe[16:20] = b'\x00' * 4
    if zlib.crc32(bytes(probe)) & 0xffffffff != stored_crc:
        raise ImageError('primary header CRC mismatch')
    my_lba, alt_lba = struct.unpack_from('<QQ', header, 24)
    first_usable, last_usable = struct.unpack_from('<QQ', header, 40)
    entries_lba = struct.unpack_from('<Q', header, 72)[0]
    entry_count, entry_bytes, entries_crc = struct.unpack_from('<III', header, 80)
    if my_lba != 1:
        raise ImageError('primary header claims LBA %d' % my_lba)
    if entry_bytes != GPT_ENTRY_BYTES:
        raise ImageError('entry size %d, expected %d' % (entry_bytes, GPT_ENTRY_BYTES))
    if entry_count > MAX_GPT_SECTORS * sector_bytes // entry_bytes:
        raise ImageError('entry count %d too large' % entry_count)
    table_bytes = entry_count * entry_bytes
    start = entries_lba * sector_bytes
    table = data[start:start + table_bytes]
    if len(table) != table_bytes:
        raise ImageError('entry array truncated')
    if zlib.crc32(table) & 0xffffffff != entries_crc:
        raise ImageError('entry array CRC mismatch')
    partitions: list[Partition] = []
    for index in range(entry_count):
        entry = table[index * entry_bytes:(index + 1) * entry_bytes]
        if entry[:16] == b'\x00' * 16:
            continue
        first_lba, last_lba = struct.unpack_from('<QQ', entry, 32)
        if first_lba < first_usable or last_lba > last_usable or last_lba < first_lba:
            raise ImageError('entry %d spans %d..%d outside usable %d..%d'
                             % (index, first_lba, last_lba, first_usable, last_usable))
        partitions.append(Partition(index, _guid(entry[0:16]), _guid(entry[16:32]),
                                    first_lba, last_lba, _decode_name(entry[56:128])))
    if require_backup:
        backup_at = alt_lba * sector_bytes
        if backup_at + sector_bytes > len(data):
            raise ImageError('backup GPT outside the image')
        if data[backup_at:backup_at + 8] != GPT_SIGNATURE:
            raise ImageError('backup GPT signature missing at LBA %d' % alt_lba)
        bsize = struct.unpack_from('<I', data, backup_at + 12)[0]
        bcrc = struct.unpack_from('<I', data, backup_at + 16)[0]
        bprobe = bytearray(data[backup_at:backup_at + bsize])
        bprobe[16:20] = b'\x00' * 4
        if zlib.crc32(bytes(bprobe)) & 0xffffffff != bcrc:
            raise ImageError('backup header CRC mismatch')
        bentries = struct.unpack_from('<Q', data, backup_at + 72)[0]
        btable = data[bentries * sector_bytes:bentries * sector_bytes + table_bytes]
        if zlib.crc32(btable) & 0xffffffff != struct.unpack_from('<I', data, backup_at + 88)[0]:
            raise ImageError('backup entry array CRC mismatch')
    return partitions


def windows_for(partitions: list[Partition], names: tuple[str, ...],
                *, disk_bytes: int) -> dict[str, Window]:
    """Map partition names onto root windows and prove they are device-readable."""
    by_name = {p.name: p for p in partitions}
    missing = [name for name in names if name not in by_name]
    if missing:
        raise ImageError('image lacks partition(s) %s; it has %s'
                         % (', '.join(missing), ', '.join(sorted(by_name)) or 'none'))
    windows: dict[str, Window] = {}
    for name in names:
        window = by_name[name].window()
        try:
            window.block_count()
        except UnitMismatch as exc:
            raise ImageError('%s cannot be read in %d-byte blocks: %s'
                             % (name, UFS_BLOCK_BYTES, exc)) from exc
        windows[name] = window
    check_table(windows.values())
    within(disk_bytes, *windows.values())
    return windows


def probe_offsets(window: Window, count: int = 4, *, seed: str = 'nabu') -> list[tuple[int, int]]:
    """(offset inside the window, length) probes: first, last block, a 4 KiB
    boundary crossing and deterministic pseudo-random interior blocks."""
    if count < 0:
        raise ValueError('probe count must not be negative')
    block = UFS_BLOCK_BYTES
    if window.length.value < block * 2:
        raise ImageError('%s is shorter than two blocks' % window.name)
    probes = [(0, block), (window.length.value - block, block),
              (block - 8, 16)]
    span = window.length.value // block
    digest = hashlib.sha256(seed.encode() + window.name.encode()).digest()
    for step in range(count):
        pick = int.from_bytes(digest[step * 4:step * 4 + 4], 'little') % max(span - 1, 1)
        probes.append((pick * block, block))
    seen, unique = set(), []
    for offset, length in probes:
        if offset + length > window.length.value:
            raise ImageError('%s: probe %d+%d runs past the window' % (window.name, offset, length))
        if offset in seen:
            continue
        seen.add(offset)
        unique.append((offset, length))
    return unique


def readcheck(image: Path, windows: dict[str, Window], *, count: int = 4) -> dict:
    """Expected bytes for every probe, keyed by window and offset; this is the
    reference the device dump is compared against."""
    data = image.read_bytes()
    report: dict = {'image_bytes': len(data), 'windows': {}}
    for name, window in sorted(windows.items(), key=lambda kv: kv[1].offset.value):
        entries = []
        for offset, length in probe_offsets(window, count):
            absolute = window.offset.value + offset
            chunk = data[absolute:absolute + length]
            if len(chunk) != length:
                raise ImageError('%s probe at %#x is past the image' % (name, absolute))
            entries.append({'offset': offset, 'length': length,
                            'absolute': absolute,
                            'sha256': hashlib.sha256(chunk).hexdigest(),
                            'head': chunk[:16].hex()})
        report['windows'][name] = {
            'offset': window.offset.value, 'length': window.length.value,
            'physical_block': window.blocks(), 'blocks': window.block_count(),
            'first_lba_512': window.sectors(), 'probes': entries}
    return report


def compare(report: dict, dumps: Path) -> dict:
    """Compare device-read dumps (one file per probe hash prefix) with the report."""
    results = []
    for name, window in report['windows'].items():
        for probe in window['probes']:
            wanted = probe['sha256']
            candidates = sorted(dumps.glob('%s-%x-*.bin' % (name, probe['absolute'])))
            if not candidates:
                results.append({'window': name, 'absolute': probe['absolute'],
                                'status': 'missing'})
                continue
            got = hashlib.sha256(candidates[-1].read_bytes()).hexdigest()
            results.append({'window': name, 'absolute': probe['absolute'],
                            'status': 'match' if got == wanted else 'mismatch',
                            'expected': wanted, 'got': got})
    return {'results': results,
            'passed': bool(results) and all(r['status'] == 'match' for r in results)}


def _load_config(path: Path) -> tuple[list[str], int | None]:
    data = json.loads(path.read_text())
    names = tuple(name for name, body in (data.get('windows') or {}).items() if body)
    disk_bytes = (data.get('medium') or {}).get('disk_bytes')
    return list(names), disk_bytes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('layout', 'probes', 'check', 'fill'))
    parser.add_argument('image', type=Path)
    parser.add_argument('--config', type=Path, default=Path('config/macos27.json'))
    parser.add_argument('--names', default='OS,SystemOS,App')
    parser.add_argument('--count', type=int, default=4)
    parser.add_argument('--dumps', type=Path)
    parser.add_argument('--json', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    data = args.image.read_bytes()
    try:
        partitions = parse_gpt(data)
        names = args.names.split(',')
        windows = windows_for(partitions, tuple(names), disk_bytes=len(data))
    except (ImageError, ValueError) as exc:
        print('refused: %s' % exc)
        return 1
    if args.action == 'layout':
        for part in partitions:
            print('  %2d %-20s %s  LBA %#x..%#x  block %#x +%#x  %s'
                  % (part.index, part.name, part.type_guid, part.first_lba, part.last_lba,
                     part.offset.value // UFS_BLOCK_BYTES, part.length.value // UFS_BLOCK_BYTES,
                     part.unique_guid))
        for name, window in sorted(windows.items(), key=lambda kv: kv[1].offset.value):
            print('  window %-9s block %#x +%#x (%#x blocks, %#x sectors)'
                  % (name, window.blocks(), window.block_count(), window.block_count(),
                     window.sector_count()))
        return 0
    report = readcheck(args.image, windows, count=args.count)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + '\n')
    if args.action == 'fill':
        if not args.out:
            print('fill needs --out PATH')
            return 2
        config = json.loads(args.config.read_text())
        for name, window in windows.items():
            node = config.setdefault('windows', {}).setdefault(name, {})
            node['start_block'] = window.blocks()
            node['blocks'] = window.block_count()
        config.setdefault('medium', {})['disk_bytes'] = len(data)
        args.out.write_text(json.dumps(config, indent=2) + '\n')
        print('filled %s from %s: %s' % (args.out, args.image,
              ', '.join('%s block %#x +%#x' % (n, w.blocks(), w.block_count())
                        for n, w in sorted(windows.items(), key=lambda kv: kv[1].offset.value))))
        return 0
    if args.action == 'probes':
        for name, window in report['windows'].items():
            print('  %-9s %d probe(s)' % (name, len(window['probes'])))
            for probe in window['probes']:
                print('    abs %#010x  len %#06x  %s'
                      % (probe['absolute'], probe['length'], probe['sha256'][:16]))
        if args.json:
            print('  report: %s' % args.json)
        return 0
    if not args.dumps:
        print('check needs --dumps DIR')
        return 2
    result = compare(report, args.dumps)
    for row in result['results']:
        print('  %-9s abs %#010x  %s' % (row['window'], row['absolute'], row['status']))
    print('readcheck: %s' % ('passed' if result['passed'] else 'failed'))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
