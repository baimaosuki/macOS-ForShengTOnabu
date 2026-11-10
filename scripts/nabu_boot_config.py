#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Load and validate the nabu macOS 27.3 boot configuration.

The 25F71 recovery configuration (config/macos26.json, build_macos26_storage.py,
prepare_macos26_ufs_root.py) binds a recovery root by UUID, digest and size.
This loader refuses that contract outright: the 27.3 boot path is a different
medium layout, a different build and a different mount set, so a 25F71 value
can never be carried across by accident.

  python3 scripts/nabu_boot_config.py config/macos27.json
  python3 scripts/nabu_boot_config.py config/macos27.json --allow-incomplete
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nabu_disk_units import (IMAGE_SECTOR_BYTES, UFS_BLOCK_BYTES, Lba, UnitMismatch,  # noqa: E402
                             check_table, window_at, within)

REQUIRED_BUILD = '26C5035r'
REQUIRED_VERSION = '27.3'
WINDOW_NAMES = ('OS', 'SystemOS', 'App')


class ConfigIncomplete(ValueError):
    """A field the boot path needs is still null."""


def _int_at(data: dict, path: str, *, minimum: int = 0) -> int:
    node = data
    for key in path.split('.'):
        if not isinstance(node, dict) or key not in node:
            raise ConfigIncomplete('%s is missing' % path)
        node = node[key]
    if node is None:
        raise ConfigIncomplete('%s is not set' % path)
    if type(node) is not int or node < minimum:
        raise ValueError('%s must be an int >= %d, got %r' % (path, minimum, node))
    return node


def _bool_at(data: dict, path: str) -> bool:
    node = data
    for key in path.split('.'):
        if not isinstance(node, dict) or key not in node:
            raise ConfigIncomplete('%s is missing' % path)
        node = node[key]
    if not isinstance(node, bool):
        raise ConfigIncomplete('%s is not set' % path)
    return node


def _str_at(data: dict, path: str) -> str:
    node = data
    for key in path.split('.'):
        if not isinstance(node, dict) or key not in node:
            raise ConfigIncomplete('%s is missing' % path)
        node = node[key]
    if not isinstance(node, str) or not node:
        raise ConfigIncomplete('%s is not set' % path)
    return node


def load(path: Path, *, allow_incomplete: bool = False) -> dict:
    data = json.loads(path.read_text())
    if data.get('schema') != 'nabu-boot-config-v1':
        raise ValueError('%s: unexpected schema %r' % (path, data.get('schema')))
    build = data.get('build')
    if build != REQUIRED_BUILD:
        raise ValueError('%s: build %r, this loader only serves %s'
                         % (path, build, REQUIRED_BUILD))
    if build in data.get('refused_builds', []):
        raise ValueError('%s: %s is the recovery-only contract' % (path, build))
    if data.get('version') != REQUIRED_VERSION:
        raise ValueError('%s: version %r, expected %s' % (path, data.get('version'), REQUIRED_VERSION))
    medium = data.get('medium') or {}
    if medium.get('block_bytes') != UFS_BLOCK_BYTES:
        raise ValueError('%s: medium.block_bytes %r differs from NabuBoard.h (%d)'
                         % (path, medium.get('block_bytes'), UFS_BLOCK_BYTES))
    if medium.get('sector_bytes') != IMAGE_SECTOR_BYTES:
        raise ValueError('%s: medium.sector_bytes %r differs from the GPT unit (%d)'
                         % (path, medium.get('sector_bytes'), IMAGE_SECTOR_BYTES))
    if (data.get('data') or {}).get('mode') != 'ram':
        raise ValueError('%s: data.mode must be "ram"; UFS writes are out of scope'
                         % path)
    if (data.get('root') or {}).get('system') != 'read_only':
        raise ValueError('%s: root.system must be "read_only"' % path)

    resolved: dict = {'build': build, 'version': REQUIRED_VERSION,
                      'os_profile': data.get('os_profile'), 'windows': {}, 'incomplete': []}
    for name in WINDOW_NAMES:
        try:
            start = _int_at(data, 'windows.%s.start_block' % name)
            blocks = _int_at(data, 'windows.%s.blocks' % name, minimum=1)
            window = window_at(name, Lba(start, UFS_BLOCK_BYTES), blocks)
            window.require_aligned()
        except ConfigIncomplete as exc:
            if not allow_incomplete:
                raise
            resolved['incomplete'].append(str(exc))
            continue
        resolved['windows'][name] = window
    if resolved['windows']:
        check_table(resolved['windows'].values())
        try:
            disk_bytes = _int_at(data, 'medium.disk_bytes', minimum=1)
            within(disk_bytes, *resolved['windows'].values())
            resolved['disk_bytes'] = disk_bytes
        except ConfigIncomplete as exc:
            if not allow_incomplete:
                raise
            resolved['incomplete'].append(str(exc))
    for probe in ('root.mount_by_uuid', 'root.data_firmlinks',
                  'data.account.auto_login', 'data.account.skip_buddy',
                  'data.account.apple_setup_done'):
        try:
            resolved[probe] = _bool_at(data, probe)
        except ConfigIncomplete as exc:
            if not allow_incomplete:
                raise
            resolved['incomplete'].append(str(exc))
    try:
        resolved['launchd_source'] = _str_at(data, 'root.launchd_source')
    except ConfigIncomplete as exc:
        if not allow_incomplete:
            raise
        resolved['incomplete'].append(str(exc))
    if not data.get('trust_cache'):
        if not allow_incomplete:
            raise ConfigIncomplete('trust_cache is empty; the 27.3 root needs its trust cache set')
        resolved['incomplete'].append('trust_cache is empty')
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('config', type=Path)
    parser.add_argument('--allow-incomplete', action='store_true',
                        help='report null fields instead of failing on them')
    args = parser.parse_args()
    try:
        resolved = load(args.config, allow_incomplete=args.allow_incomplete)
    except ConfigIncomplete as exc:
        print('incomplete: %s' % exc)
        return 2
    except (ValueError, UnitMismatch) as exc:
        print('refused: %s' % exc)
        return 1
    print('%s %s (%s)' % (resolved['os_profile'], resolved['version'], resolved['build']))
    for name, window in sorted(resolved['windows'].items(), key=lambda kv: kv[1].offset.value):
        print('  %-9s block %#x +%#x  byte %s +%s  (%#x sectors)'
              % (name, window.blocks(), window.block_count(), window.offset,
                 window.length, window.sector_count()))
    if resolved.get('disk_bytes'):
        print('  medium   %#x bytes (%d blocks)' % (resolved['disk_bytes'],
                                                    resolved['disk_bytes'] // UFS_BLOCK_BYTES))
    for name in resolved['incomplete']:
        print('  unset:   %s' % name)
    return 0 if not resolved['incomplete'] else 2


if __name__ == '__main__':
    sys.exit(main())
