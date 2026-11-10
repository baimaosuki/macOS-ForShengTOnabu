# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile against the actual build-staged NABU SMMU takeover implementation."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from nabu_storage_source import stage

class NabuSmmuIntegration(unittest.TestCase):
    def test_actual_staged_header_and_board_isolation(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        if not compiler: self.skipTest('host C++ compiler required')
        checkout = ROOT / 'UniXNU/drivers/NativeUfs'
        before = subprocess.check_output(['git','-C',checkout,'diff','--binary'])
        with tempfile.TemporaryDirectory(dir=ROOT / 'out') as temp:
            folder = Path(temp)
            source, evidence = stage(ROOT, folder / 'nabu', board='nabu')
            header = source.with_name('SmmuTakeover.h')
            patch = 'patches/UniXNU/0044-nabu-ufs-smmu-revalidate.patch'
            legacy = (ROOT / 'patches/ShengUfs/nabu-ufs-smmu-revalidate.patch').read_text()
            adapted = legacy.replace('a/SmmuTakeover.h','a/drivers/NativeUfs/SmmuTakeover.h').replace(
                'b/SmmuTakeover.h','b/drivers/NativeUfs/SmmuTakeover.h')
            self.assertEqual((ROOT / patch).read_text(), adapted)
            entry = next(p for p in evidence['patches'] if p['path'] == patch)
            self.assertEqual(entry['sha256'], hashlib.sha256((ROOT / patch).read_bytes()).hexdigest())
            self.assertIn('existingBank', header.read_text())
            self.assertIn('sameUfsSmmuPlan', header.read_text())
            self.assertIn('#include "SmmuTakeover.h"', source.read_text())
            binary = folder / 'test'
            subprocess.run([compiler,'-std=c++17','-Wall','-Wextra','-Werror','-I',str(source.parent),
                str(ROOT / 'tests/nabu_ufs_smmu.cpp'),'-o',str(binary)],check=True,capture_output=True)
            result = subprocess.check_output([binary],text=True)
            self.assertIn('photo SID300/CB1 reuse without writes', result)
            sheng, evidence = stage(ROOT, folder / 'sheng', board='sheng')
            self.assertFalse(any(p['path'] == patch for p in evidence['patches']))
            pristine = subprocess.check_output(['git','-C',checkout,'show','HEAD:SmmuTakeover.h'])
            self.assertEqual(sheng.with_name('SmmuTakeover.h').read_bytes(), pristine)
        self.assertEqual(before, subprocess.check_output(['git','-C',checkout,'diff','--binary']))

if __name__ == '__main__': unittest.main()
