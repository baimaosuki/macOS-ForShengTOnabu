#!/usr/bin/env python3
"""Inventory the exact cache files selected by zeta; never rewrite the input."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).with_name('zeta_pac')))
from zeta_dyld_cache import Cache
from nabu_zeta_pac import ABI, PIN


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    cache = Cache(args.cache.resolve())
    entries = []
    for item in cache.files:
        with item.path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        entries.append(dict(name=item.path.name, bytes=item.path.stat().st_size, sha256=digest))
    # Order must match Cache.files, including the main cache and every subcache.
    report = dict(abi=ABI, upstream_commit=PIN, files=entries)
    with args.output.open('x') as stream:
        stream.write(json.dumps(report, indent=2) + '\n')
    print(f'Inventoried {len(entries)} cache files; input unchanged')


if __name__ == '__main__':
    main()
