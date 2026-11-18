#!/usr/bin/env python3
"""Extract original BSD inode flags for a decompressed runtime volume plan."""
import argparse
import functools
import json
from pathlib import Path
import struct
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).with_name('zeta_pac')))
from zeta_apfs_image import Container, TYPE_INODE

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--image', type=Path, required=True)
p.add_argument('--offset', type=int, required=True)
p.add_argument('--plan', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--reader', type=Path, help='apfs-fuse metadata reader; handles sealed source volumes')
p.add_argument('--metadata-only', action='store_true')
a = p.parse_args()
plan = json.loads(a.plan.read_text())
entries = plan.get('directories', []) + plan.get('files', [])
flags = []
if a.reader:
    paths = [entry['path'] for entry in entries]
    result = subprocess.check_output([str(a.reader), str(a.image), str(a.offset)],
                                     input='\n'.join(paths) + '\n', text=True)
    for line, expected in zip(result.splitlines(), paths):
        value, sep, actual = line.partition('\t')
        if not sep or actual != expected:
            raise ValueError('native inode metadata order/path mismatch')
        original = int(value, 16)
        flags.append(dict(path=actual, flags=original & ~0x20, original_flags=original))
    if len(flags) != len(paths):
        raise ValueError('incomplete native metadata read')
    if a.metadata_only:
        plan = dict(files=[], directories=[], symlinks=[])
    plan['bsd_flags'] = flags
    a.output.write_text(json.dumps(plan, indent=2) + '\n')
    print(f'{a.output.name}: extracted {len(flags)} BSD flags; restricted={sum(bool(f["flags"] & 0x80000) for f in flags)}')
    raise SystemExit(0)
with a.image.open('rb') as f:
    container = Container(f, a.offset)
    container.checked = functools.lru_cache(maxsize=8192)(container.checked)
    container.omap_lookup = functools.lru_cache(maxsize=32768)(container.omap_lookup)
    container.node = functools.lru_cache(maxsize=8192)(container.node)
    volumes = list(container.volumes())
    if len(volumes) != 1:
        raise ValueError('require one selected volume')
    volume = volumes[0]
    volume.records = functools.lru_cache(maxsize=32768)(volume.records)
    for entry in entries:
        ino = volume.lookup(entry['path'])
        records = volume.records(ino, TYPE_INODE)
        if len(records) != 1:
            raise ValueError(f'{entry["path"]}: ambiguous inode')
        native_flags = struct.unpack_from('<I', records[0][1], 0x44)[0]
        flags.append(dict(path=entry['path'], flags=native_flags & ~0x20,
                          original_flags=native_flags, original_inode=ino))
if a.metadata_only:
    plan = dict(files=[], directories=[], symlinks=[])
plan['bsd_flags'] = flags
plan['original_volume'] = dict(name=volume.name, role=volume.role)
a.output.write_text(json.dumps(plan, indent=2) + '\n')
print(f'{a.output.name}: extracted {len(flags)} BSD flags; restricted={sum(bool(f["flags"] & 0x80000) for f in flags)}')
