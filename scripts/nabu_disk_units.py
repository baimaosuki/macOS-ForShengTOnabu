#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""One unit vocabulary for the nabu root disk.

The board publishes 4096-byte logical blocks (platform/nabu/macos26/storage/
NabuBoard.h), while a disk image is addressed in 512-byte sectors. Adding a
block index to a byte offset, or treating "LBA" as one unit, silently lands a
window 8x away from where it was meant to be.  So nothing here is a bare int:

  * Bytes        - the only thing that may be added or compared to another Bytes
  * Lba          - an index plus the unit it is counted in
  * Window       - a byte offset and length, named, with explicit conversions

Conversions refuse instead of truncating: a window that is not sector aligned,
a length that is not a whole number of blocks, or two windows that overlap all
raise with the window name in the message.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

# platform/nabu/macos26/storage/NabuBoard.h: "logical block 4096 bytes"
UFS_BLOCK_BYTES = 4096
# A raw disk image (GPT + partitions) is addressed in 512-byte sectors.
IMAGE_SECTOR_BYTES = 512
KNOWN_UNITS = (UFS_BLOCK_BYTES, IMAGE_SECTOR_BYTES)


class UnitMismatch(ValueError):
    """Two values counted in different units were combined."""


@dataclass(frozen=True, order=True)
class Bytes:
    value: int

    def __post_init__(self) -> None:
        if type(self.value) is not int or self.value < 0:
            raise ValueError('byte count must be a non-negative int, got %r' % (self.value,))

    def __add__(self, other: 'Bytes') -> 'Bytes':
        if not isinstance(other, Bytes):
            raise UnitMismatch('cannot add %r to Bytes' % (other,))
        return Bytes(self.value + other.value)

    def __sub__(self, other: 'Bytes') -> 'Bytes':
        if not isinstance(other, Bytes):
            raise UnitMismatch('cannot subtract %r from Bytes' % (other,))
        return Bytes(self.value - other.value)

    def __str__(self) -> str:
        return '%#x' % self.value


@dataclass(frozen=True)
class Lba:
    index: int
    unit: int

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise ValueError('block index must be a non-negative int, got %r' % (self.index,))
        if self.unit not in KNOWN_UNITS:
            raise ValueError('unknown unit %r; expected one of %r' % (self.unit, KNOWN_UNITS))

    def to_bytes(self) -> Bytes:
        return Bytes(self.index * self.unit)

    def rebase(self, unit: int) -> 'Lba':
        """Same position expressed in another unit; refuses if it is not exact."""
        if unit not in KNOWN_UNITS:
            raise ValueError('unknown unit %r' % (unit,))
        total = self.to_bytes().value
        if total % unit:
            raise UnitMismatch('%#x bytes is not a whole %d-byte block' % (total, unit))
        return Lba(total // unit, unit)

    def __add__(self, other: object) -> 'Lba':
        raise UnitMismatch('a block index is a position: convert it to Bytes first')


@dataclass(frozen=True)
class Window:
    name: str
    offset: Bytes
    length: Bytes

    def __post_init__(self) -> None:
        if not self.name or not isinstance(self.name, str):
            raise ValueError('window needs a name')
        if not isinstance(self.offset, Bytes) or not isinstance(self.length, Bytes):
            raise UnitMismatch('%s: offset and length must be Bytes' % self.name)
        if self.length.value <= 0:
            raise ValueError('%s: length must be positive' % self.name)

    @property
    def end(self) -> Bytes:
        return Bytes(self.offset.value + self.length.value)

    def sectors(self) -> int:
        if self.offset.value % IMAGE_SECTOR_BYTES:
            raise UnitMismatch('%s: offset %s is not %d-byte aligned'
                               % (self.name, self.offset, IMAGE_SECTOR_BYTES))
        return self.offset.value // IMAGE_SECTOR_BYTES

    def sector_count(self) -> int:
        if self.length.value % IMAGE_SECTOR_BYTES:
            raise UnitMismatch('%s: length %s is not a multiple of %d'
                               % (self.name, self.length, IMAGE_SECTOR_BYTES))
        return self.length.value // IMAGE_SECTOR_BYTES

    def blocks(self) -> int:
        if self.offset.value % UFS_BLOCK_BYTES:
            raise UnitMismatch('%s: offset %s is not %d-byte aligned'
                               % (self.name, self.offset, UFS_BLOCK_BYTES))
        return self.offset.value // UFS_BLOCK_BYTES

    def block_count(self) -> int:
        if self.length.value % UFS_BLOCK_BYTES:
            raise UnitMismatch('%s: length %s is not a multiple of %d'
                               % (self.name, self.length, UFS_BLOCK_BYTES))
        return self.length.value // UFS_BLOCK_BYTES

    def require_aligned(self) -> 'Window':
        if self.offset.value % IMAGE_SECTOR_BYTES or self.length.value % IMAGE_SECTOR_BYTES:
            raise UnitMismatch('%s: %s+%s is not sector aligned' % (self.name, self.offset, self.length))
        return self


def window_at(name: str, start: Lba, length_blocks: int) -> Window:
    """A window given in the controller's own unit, converted once to bytes."""
    if type(length_blocks) is not int or length_blocks <= 0:
        raise ValueError('%s: block count must be a positive int' % name)
    return Window(name, start.to_bytes(), Bytes(length_blocks * start.unit))


def window_bytes(name: str, offset_bytes: int, length_bytes: int) -> Window:
    """A window already given in bytes, e.g. read from a GPT entry (LBA * 512)."""
    return Window(name, Bytes(offset_bytes), Bytes(length_bytes))


def check_table(windows: Iterable[Window]) -> None:
    """Names unique, offsets increasing, no overlap, nothing before zero."""
    table = sorted(windows, key=lambda w: w.offset.value)
    seen: set[str] = set()
    previous: Window | None = None
    for window in table:
        if window.name in seen:
            raise ValueError('duplicate window name %r' % window.name)
        seen.add(window.name)
        if previous is not None:
            if window.offset.value < previous.end.value:
                raise ValueError('%s at %s overlaps %s ending at %s'
                                 % (window.name, window.offset, previous.name, previous.end))
            if window.offset.value == previous.offset.value:
                raise ValueError('%s and %s share an offset' % (window.name, previous.name))
        previous = window


def within(disk_bytes: int, *windows: Window) -> None:
    """Every window must end inside the medium."""
    if type(disk_bytes) is not int or disk_bytes <= 0:
        raise ValueError('medium size must be a positive int')
    for window in windows:
        if window.end.value > disk_bytes:
            raise ValueError('%s ends at %s, past the %#x-byte medium'
                             % (window.name, window.end, disk_bytes))
