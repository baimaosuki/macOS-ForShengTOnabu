#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""unittest entry points for the nabu read-only UFS contract.

`python3 -m unittest discover -s tests` is what CI runs, so the contract has to
be visible to it as test cases rather than as standalone scripts.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
STORAGE = ROOT / 'platform' / 'nabu' / 'macos26' / 'storage'
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / 'tests'))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tests' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cases(module) -> list:
    return [value for name, value in sorted(vars(module).items())
            if name.startswith('test_') and callable(value)]


class NabuUfsHostContract(unittest.TestCase):
    def test_disk_unit_contract(self) -> None:
        for case in _cases(_load('test_nabu_disk_units')):
            with self.subTest(case=case.__name__):
                case()

    def test_image_contract(self) -> None:
        for case in _cases(_load('test_nabu_ufs_image')):
            with self.subTest(case=case.__name__):
                case()


class NabuUfsRootImageContract(unittest.TestCase):
    def test_root_image_contract(self) -> None:
        for case in _cases(_load('test_nabu_ufs_root')):
            with self.subTest(case=case.__name__):
                case()


class NabuUfsNativeContract(unittest.TestCase):
    def _compile_and_run(self, source: str, standard: str = 'c++17') -> str:
        compiler = shutil.which('g++') or shutil.which('clang++')
        if compiler is None:
            self.skipTest('no host C++ compiler')
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'test'
            build = subprocess.run(
                [compiler, f'-std={standard}', '-Wall', '-Wextra', '-Wno-unused-function',
                 '-I', str(STORAGE), str(ROOT / 'tests' / source), '-o', str(binary)],
                capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            return run.stdout

    def test_read_window_alignment(self) -> None:
        out = self._compile_and_run('nabu_ufs_alignment.cpp')
        self.assertIn('window-contract checks passed', out)

    def test_root_window_gate(self) -> None:
        out = self._compile_and_run('nabu_ufs_windowplan.cpp')
        self.assertIn('root-window gate checks passed', out)


class Macos27Components(unittest.TestCase):
    def test_component_resolver(self) -> None:
        for case in _cases(_load('test_macos27_components')):
            with self.subTest(case=case.__name__):
                case()


class NabuUfsBootConfig(unittest.TestCase):
    def test_loader_rejects_the_recovery_build(self) -> None:
        import json
        from nabu_boot_config import ConfigIncomplete, load
        import tempfile as tf
        payload = json.loads((ROOT / 'config' / 'macos27.json').read_text())
        payload['build'] = '25F71'
        with tf.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'legacy.json'
            path.write_text(json.dumps(payload))
            with self.assertRaises(ValueError):
                load(path)

    def test_loader_serves_the_273_skeleton(self) -> None:
        from nabu_boot_config import load
        resolved = load(ROOT / 'config' / 'macos27.json', allow_incomplete=True)
        self.assertEqual(resolved['build'], '26C5035r')
        self.assertTrue(resolved['incomplete'])


if __name__ == '__main__':
    unittest.main()
