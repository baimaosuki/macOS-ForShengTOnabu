#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage a reproducible UFS integration without mutating nested checkouts."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess


def stage(root: Path, destination: Path, *, board: str) -> tuple[Path, dict]:
    if board not in ('sheng', 'nabu'):
        raise ValueError('unsupported UFS board')
    checkout = root / 'UniXNU/drivers/NativeUfs'
    target = destination / 'drivers/NativeUfs'
    target.mkdir(parents=True, exist_ok=False)
    revision = subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], text=True).strip()
    files = subprocess.check_output(['git', '-C', str(checkout), 'ls-tree', '-r', '--name-only', revision], text=True)
    for name in files.splitlines():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(subprocess.check_output(['git', '-C', str(checkout), 'show', revision + ':' + name]))
    # The ISC overlay includes SparseDisk relatively; retain its exact source.
    ram = destination / 'drivers/NativeRamBlock'
    ram.mkdir(parents=True)
    for header in (root / 'UniXNU/drivers/NativeRamBlock').glob('*.h'):
        shutil.copy2(header, ram / header.name)
    patches = []
    for patch in sorted((root / 'patches/UniXNU').glob('*.patch')):
        if 'a/drivers/NativeUfs/' not in patch.read_text():
            continue
        if patch.name.startswith(('0040-', '0041-', '0042-', '0043-')) and board != 'nabu':
            continue
        subprocess.run(['git', 'apply', '--include=drivers/NativeUfs/*', str(patch.resolve())],
                       cwd=destination, check=True, capture_output=True,
                       env={**os.environ, 'GIT_CEILING_DIRECTORIES': str(destination.resolve().parent)})
        patches.append(dict(path=str(patch.relative_to(root)), sha256=hashlib.sha256(patch.read_bytes()).hexdigest()))
    if board == 'nabu':
        for header in (root / 'platform/nabu/macos26/storage').glob('Nabu*.h'):
            if header.name != 'NabuBoard.h':
                shutil.copy2(header, target / header.name)
    return target / 'NativeUfs.cpp', dict(board=board, revision=revision, patches=patches,
        headers_sha256={str(p.relative_to(destination)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted(destination.rglob('*.h'))}, nested_checkouts_modified=False)
