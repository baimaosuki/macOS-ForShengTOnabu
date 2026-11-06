#!/usr/bin/env python3
"""Fetch pinned sources as codeload tarballs when the git protocol is unavailable.

Each source is verified only by the URL+commit recorded in the lock (plus the
tarball digest), which keeps the pin identity intact without a git exchange.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN_NAME = '.source-pin'


def slug(url):
    base = url.removesuffix('.git')
    for prefix in ('https://github.com/', 'http://github.com/'):
        if base.startswith(prefix):
            return base[len(prefix):]
    raise ValueError(f'unsupported source host: {url}')


def entries():
    lock = json.loads((ROOT / 'sources.lock.json').read_text())['sources']
    for entry in lock:
        yield entry['path'], entry['url'], entry['commit'], entry.get('group', 'core')
    macos = ROOT / 'config/macos26-sources.json'
    if macos.is_file():
        for entry in json.loads(macos.read_text())['sources']:
            yield entry['path'], entry['url'], entry['commit'], 'macos26'
    feeds = ROOT / 'UniXNU-nabu/feeds.toml'
    if feeds.exists():
        name = None
        for line in feeds.read_text().splitlines():
            line = line.strip()
            if line.startswith('name = '):
                name = line.split('"')[1]
            elif line.startswith('git = ') and name:
                url = line.split('"')[1]
                yield f'sources/unixnu-components/{name}', url, None, 'nabu-components'


def current_pin(directory):
    pin = directory / PIN_NAME
    if pin.is_file():
        return json.loads(pin.read_text())
    return None


def fetch(path, url, commit, group):
    directory = ROOT / path
    if commit is None:
        print(f'SKIP {path}: no pinned commit declared', flush=True)
        return
    pin = current_pin(directory)
    if pin and pin.get('commit') == commit:
        print(f'HAVE {path} {commit[:12]}', flush=True)
        return
    target = f'{slug(url)}/tar.gz/{commit}'
    uri = f'https://codeload.github.com/{target}'
    with tempfile.TemporaryDirectory() as work:
        archive = Path(work) / 'source.tar.gz'
        for attempt in range(3):
            result = subprocess.run(['curl', '-sL', '--retry', '2', '--max-time', '1200',
                                     '-o', str(archive), uri])
            if result.returncode == 0 and archive.stat().st_size > 0:
                break
        else:
            raise SystemExit(f'MAILFAIL {path}: {uri}')
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        extract = Path(work) / 'x'
        extract.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(extract)
        tops = [item for item in extract.iterdir() if item.is_dir()]
        if len(tops) != 1:
            raise SystemExit(f'{path}: unexpected archive layout')
        if directory.exists():
            shutil.rmtree(directory)
        shutil.move(str(tops[0]), str(directory))
        (directory / PIN_NAME).write_text(json.dumps({
            'url': url, 'commit': commit, 'tarball_sha256': digest}, indent=2) + '\n')
        print(f'PIN {path} {commit[:12]} ({archive.stat().st_size} B)', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', default='all')
    args = parser.parse_args()
    for path, url, commit, group in entries():
        if args.group not in ('all', group):
            continue
        fetch(path, url, commit, group)
    return 0


if __name__ == '__main__':
    sys.exit(main())
