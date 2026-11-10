#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Assemble the macOS 27.3 read-only root image for nabu.

The driver parses a SHNGROOT v1 manifest in the first 4096-byte block of the
root partition (UniXNU/drivers/NativeUfs/RootLayout.h) and publishes one block
device per window. This script writes exactly that contract for the three
windows a desktop boot needs, and refuses anything the kernel would refuse:

  * window offsets are 4096-aligned and start at or after 4096,
  * window sizes are whole 512-byte sectors,
  * windows do not overlap after 4096 rounding,
  * every window fits inside imageBytes,
  * each window carries the SHA-256 of the bytes that were copied.

The 25F71 recovery writer (prepare_macos26_ufs_root.py) is deliberately not
reused: it binds a different build, a different window set and a paired
BaseSystem, and its evidence keys are not this contract.

  python3 scripts/prepare_macos27_root.py spec.json out/
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nabu_disk_units import UFS_BLOCK_BYTES, Bytes, Window, check_table, within  # noqa: E402

MANIFEST_MAGIC = b'SHNGROOT'
MANIFEST_VERSION = 1
MANIFEST_BLOCK_BYTES = UFS_BLOCK_BYTES
ENTRY_BYTES = 128
ENTRY_LIMIT = 4
CHUNK = 1 << 20


class RootImageError(ValueError):
    """The requested root image cannot be expressed as a kernel manifest."""


def _sources(spec: dict) -> list[tuple[str, Path, int]]:
    windows = spec.get('windows')
    if not isinstance(windows, list) or not 2 <= len(windows) <= ENTRY_LIMIT:
        raise RootImageError('spec needs 2..%d windows, got %r' % (ENTRY_LIMIT, windows))
    resolved: list[tuple[str, Path, int]] = []
    for item in windows:
        name = item.get('name')
        source = item.get('source')
        offset = item.get('offset_bytes')
        if not isinstance(name, str) or not name or len(name) > 31:
            raise RootImageError('window name must be 1..31 bytes of ASCII: %r' % (name,))
        if any(ord(char) < 0x20 or ord(char) > 0x7E for char in name):
            raise RootImageError('window name is not printable ASCII: %r' % name)
        if not isinstance(source, str):
            raise RootImageError('%s: source path missing' % name)
        if type(offset) is not int or offset % UFS_BLOCK_BYTES or offset < UFS_BLOCK_BYTES:
            raise RootImageError('%s: offset_bytes must be a 4096-aligned multiple >= 4096' % name)
        path = Path(source)
        if not path.is_file():
            raise RootImageError('%s: source %s is not a file' % (name, path))
        resolved.append((name, path, offset))
    names = [name for name, _, _ in resolved]
    if len(set(names)) != len(names):
        raise RootImageError('window names must be distinct: %r' % names)
    return resolved


def plan(spec: dict) -> tuple[list[tuple[str, Path, int, int]], int]:
    """Validate every window and return (name, path, offset, size) plus imageBytes."""
    entries: list[tuple[str, Path, int, int]] = []
    for name, path, offset in _sources(spec):
        size = path.stat().st_size
        if not size or size % 512:
            raise RootImageError('%s: %d bytes is not a whole number of 512-byte sectors'
                                 % (name, size))
        entries.append((name, path, offset, size))
    windows = []
    for name, _, offset, size in entries:
        rounded = (size + UFS_BLOCK_BYTES - 1) // UFS_BLOCK_BYTES * UFS_BLOCK_BYTES
        windows.append(Window(name, Bytes(offset), Bytes(rounded)))
    check_table(windows)                                   # duplicate/overlap, in bytes
    image_bytes = max(window.end.value for window in windows)
    within(image_bytes, *windows)
    expected = spec.get('image_bytes')
    if expected is not None and expected != image_bytes:
        raise RootImageError('image_bytes %r differs from the laid-out %d'
                             % (expected, image_bytes))
    return entries, image_bytes


def build_manifest(entries: list[tuple[str, Path, int, int]], image_bytes: int,
                   digests: dict[str, str]) -> bytes:
    header = bytearray(MANIFEST_BLOCK_BYTES)
    header[:8] = MANIFEST_MAGIC
    struct.pack_into('<IIQ', header, 8, MANIFEST_VERSION, len(entries), image_bytes)
    for index, (name, _path, offset, size) in enumerate(entries):
        struct.pack_into('<32sQQII32s40s', header, 24 + index * ENTRY_BYTES,
                         name.encode('ascii'), offset, size, 512, 1,
                         bytes.fromhex(digests[name]), bytes(40))
    header[4064:] = hashlib.sha256(header[:4064]).digest()
    return bytes(header)


def assemble(entries: list[tuple[str, Path, int, int]], image_bytes: int,
             destination: Path) -> dict[str, str]:
    """Copy each window to its offset and hash the bytes actually written."""
    image = destination / 'root_image.bin'
    digests: dict[str, str] = {}
    with image.open('wb') as out:
        cursor = 0
        for name, path, offset, size in entries:
            if offset < cursor:
                raise RootImageError('%s: offset %#x is behind the last window' % (name, offset))
            out.write(b'\x00' * (offset - cursor))
            cursor = offset
            digest = hashlib.sha256()
            copied = 0
            with path.open('rb') as source:
                while True:
                    chunk = source.read(CHUNK)
                    if not chunk:
                        break
                    digest.update(chunk)
                    out.write(chunk)
                    copied += len(chunk)
            if copied != size:
                raise RootImageError('%s: copied %d of %d bytes' % (name, copied, size))
            cursor += size
            pad = (-size) % UFS_BLOCK_BYTES
            out.write(b'\x00' * pad)
            cursor += pad
            digests[name] = digest.hexdigest()
        if cursor != image_bytes:
            raise RootImageError('laid out %#x bytes, expected %#x' % (cursor, image_bytes))
    return digests


def prepare(spec_path: Path, destination: Path, *, assemble_image: bool = True) -> dict:
    spec = json.loads(spec_path.read_text())
    entries, image_bytes = plan(spec)
    destination.mkdir(parents=True, exist_ok=True)
    if assemble_image:
        digests = assemble(entries, image_bytes, destination)
    else:
        digests = {name: hashlib.sha256(path.read_bytes()).hexdigest()
                   for name, path, _, _ in entries}
    manifest = build_manifest(entries, image_bytes, digests)
    (destination / 'manifest.bin').write_bytes(manifest)
    record = {
        'schema': 'nabu-root-image-v1',
        'os_profile': spec.get('os_profile'),
        'build': spec.get('build'),
        'image_bytes': image_bytes,
        'manifest_sha256': hashlib.sha256(manifest).hexdigest(),
        'manifest_integrity_is_not_authentication': True,
        'windows': [
            {'name': name, 'offset_bytes': offset, 'size_bytes': size,
             'device_blocks': (size + UFS_BLOCK_BYTES - 1) // UFS_BLOCK_BYTES,
             'sha256': digests[name], 'source': path.name}
            for name, path, offset, size in entries
        ],
        'assemble_image': assemble_image,
    }
    (destination / 'build.json').write_text(json.dumps(record, indent=2) + '\n')
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('spec', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--no-image', action='store_true',
                        help='hash the sources but do not write root_image.bin')
    args = parser.parse_args()
    try:
        record = prepare(args.spec, args.destination, assemble_image=not args.no_image)
    except (RootImageError, ValueError) as exc:
        print('refused: %s' % exc)
        return 1
    print('root image %#x bytes, %d windows' % (record['image_bytes'], len(record['windows'])))
    for window in record['windows']:
        print('  %-9s offset %#010x size %#010x blocks %#06x sha %s'
              % (window['name'], window['offset_bytes'], window['size_bytes'],
                 window['device_blocks'], window['sha256'][:16]))
    print('  manifest %s' % record['manifest_sha256'][:16])
    return 0


if __name__ == '__main__':
    sys.exit(main())
