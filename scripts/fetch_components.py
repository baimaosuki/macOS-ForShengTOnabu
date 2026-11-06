#!/usr/bin/env python3
"""Fetch the UniXNU component repositories at the pins recorded in feeds.toml."""
import json
import shutil
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FEEDS = ROOT / 'UniXNU-nabu/feeds.toml'
TARGET = ROOT / 'sources/unixnu-components'


def git(directory, *args, capture=False):
    return subprocess.run(['git', '-C', str(directory), *args], check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def entries():
    data = tomllib.loads(FEEDS.read_text())
    for source in data.get('source', []):
        name = source.get('name')
        url = source.get('git')
        commit = source.get('commit')
        if name and url and commit:
            yield name, url, commit


def fetch(name, url, commit):
    directory = TARGET / name
    if (directory / '.git').is_dir():
        head = subprocess.run(['git', '-C', str(directory), 'rev-parse', 'HEAD'],
                              text=True, capture_output=True)
        if head.returncode == 0 and head.stdout.strip() == commit:
            print(f'HAVE {name} {commit[:12]}', flush=True)
            return
    if directory.exists():
        shutil.rmtree(directory)
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.mkdir()
    git(directory, 'init', '-q')
    git(directory, 'remote', 'add', 'origin', url)
    git(directory, 'fetch', '--depth=1', 'origin', commit)
    git(directory, 'checkout', '-q', '--detach', 'FETCH_HEAD')
    print(f'PIN {name} {commit[:12]}', flush=True)


def main():
    TARGET.mkdir(parents=True, exist_ok=True)
    for name, url, commit in entries():
        fetch(name, url, commit)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
