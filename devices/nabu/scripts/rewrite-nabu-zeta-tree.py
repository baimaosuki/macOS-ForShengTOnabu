#!/usr/bin/env python3
"""Rewrite arm64e programs of one read-only macOS APFS volume into a new tree."""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import stat
import struct
from nabu_zeta_pac import ABI, PIN, load_trust, rewrite, require


def candidate(head):
    if len(head) < 32:
        return False
    if head[:4] == b'\xcf\xfa\xed\xfe':
        _, cpu, subtype, kind = struct.unpack_from('<4I', head)
        return cpu == 0x100000c and subtype & 0xffffff == 2 and kind in (2, 6, 7, 8)
    magic, count = struct.unpack_from('>II', head)
    if magic not in (0xcafebabe, 0xcafebabf):
        return False
    width = 32 if magic == 0xcafebabf else 20
    require(count <= 64 and 8 + width * count <= len(head), 'invalid universal header during inventory')
    return any(struct.unpack_from('>II', head, 8 + width * i)[0] == 0x100000c
               and struct.unpack_from('>II', head, 8 + width * i)[1] & 0xffffff == 2
               for i in range(count))


def inventory(root):
    files, errors = {}, []
    def error(err):
        errors.append(str(err))
    for parent, dirs, names in os.walk(root, followlinks=False, onerror=error):
        for name in names:
            path = Path(parent, name)
            try:
                status = path.lstat()
                if not stat.S_ISREG(status.st_mode) or status.st_size < 32:
                    continue
                with path.open('rb') as f:
                    header = f.read(8 + 64 * 32)
                if candidate(header):
                    key = (status.st_dev, status.st_ino)
                    files.setdefault(key, []).append('/' + path.relative_to(root).as_posix())
            except (OSError, ValueError) as err:
                errors.append(f'{path}: {err}')
    return sorted((sorted(paths) for paths in files.values()), key=lambda p: p[0]), errors


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--file-list', type=Path, required=True)
    p.add_argument('--apple-trust-cache', type=Path, action='append', required=True)
    p.add_argument('--jobs', type=int, default=2)
    a = p.parse_args(argv)
    require(not a.output.exists() and not a.report.exists(), 'output already exists')
    trusted = load_trust(a.apple_trust_cache)
    paths, errors = inventory(a.root)
    a.output.mkdir(parents=True)
    def one(names):
        source = a.root / names[0].lstrip('/')
        try:
            status = source.stat()
            data, report = rewrite(source.read_bytes(), trusted)
            dest = a.output / names[0].lstrip('/')
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open('xb') as f:
                f.write(data)
            report.update(paths=names, output=str(dest), uid=status.st_uid, gid=status.st_gid,
                          mode=f'{stat.S_IMODE(status.st_mode):o}')
            return report
        except (OSError, ValueError, KeyError, IndexError, struct.error) as err:
            return dict(paths=names, error=f'{type(err).__name__}: {err}')
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.jobs) as pool:
        for i, item in enumerate(pool.map(one, paths), 1):
            results.append(item)
            if i % 100 == 0 or i == len(paths):
                print(f'{a.root.name}: {i}/{len(paths)} rewritten; failures={sum("error" in r for r in results)}', flush=True)
    failures = [r for r in results if 'error' in r]
    manifest = [dict(source=r['output'], path=path, uid=r['uid'], gid=r['gid'], mode=r['mode'])
                for r in results if 'error' not in r for path in r['paths']]
    report = dict(schema='nabu-zeta-tree-v1', abi=ABI, upstream_commit=PIN, root=str(a.root),
                  candidates=len(paths), complete=not errors and not failures, inventory_errors=errors,
                  files=results, failures=len(failures), hardware_tested=False)
    a.file_list.write_text(json.dumps(manifest, indent=2) + '\n')
    a.report.write_text(json.dumps(report, indent=2) + '\n')
    return 0 if report['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
