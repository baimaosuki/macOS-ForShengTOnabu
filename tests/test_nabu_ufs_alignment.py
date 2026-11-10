#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Host runner for tests/nabu_ufs_alignment.cpp (the read window contract)."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tests' / 'nabu_ufs_alignment.cpp'
INCLUDE = ROOT / 'platform' / 'nabu' / 'macos26' / 'storage'


def test_window_contract() -> None:
    compiler = shutil.which('g++') or shutil.which('clang++')
    if compiler is None:
        raise AssertionError('no host C++ compiler; the window contract cannot be checked here')
    with tempfile.TemporaryDirectory() as tmp:
        binary = Path(tmp) / 'alignment'
        build = subprocess.run(
            [compiler, '-std=c++17', '-Wall', '-Wextra', '-Wno-unused-function',
             '-I', str(INCLUDE), str(SOURCE), '-o', str(binary)],
            capture_output=True, text=True)
        if build.returncode != 0:
            raise AssertionError('build failed:\n%s%s' % (build.stdout, build.stderr))
        run = subprocess.run([str(binary)], capture_output=True, text=True)
        assert run.returncode == 0, run.stdout + run.stderr
        assert 'window-contract checks passed' in run.stdout, run.stdout
        print('    ' + run.stdout.strip().replace('\n', '\n    '))


def main() -> int:
    try:
        test_window_contract()
    except AssertionError as exc:
        print('  FAIL %s' % exc)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
