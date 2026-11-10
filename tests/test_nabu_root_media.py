# SPDX-License-Identifier: GPL-3.0-or-later
"""Test the actual NABU-staged media scanner and its production property adapter."""
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


class NabuRootMediaIntegration(unittest.TestCase):
    def test_actual_staged_gate_and_adapter(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            self.skipTest('host C++ compiler required')
        checkout = ROOT / 'UniXNU/drivers/NativeUfs'
        before = subprocess.check_output(['git', '-C', checkout, 'diff', '--binary'])
        with tempfile.TemporaryDirectory(dir=ROOT / 'out') as temp:
            folder = Path(temp)
            source, evidence = stage(ROOT, folder / 'nabu', board='nabu')
            header = source.with_name('NabuRootMedia.h')
            self.assertEqual(header.read_bytes(), (ROOT / 'platform/nabu/macos26/storage/NabuRootMedia.h').read_bytes())
            key = str(header.relative_to(folder / 'nabu'))
            self.assertEqual(evidence['headers_sha256'][key], hashlib.sha256(header.read_bytes()).hexdigest())
            patch = 'patches/UniXNU/0046-nabu-root-media-registry.patch'
            self.assertIn(patch, [entry['path'] for entry in evidence['patches']])
            text = source.read_text()
            self.assertIn('#include "NabuRootMedia.h"', text)
            begin = text.index('struct NabuRootMediaRegistry {')
            end = text.index('static bool mediaWithUuidExists', begin)
            adapter = text[begin:end]
            (folder / 'NabuRootMediaAdapterForTest.inc').write_text(adapter)
            media_end = text.index('// Used only for objects that were never started/published.', end)
            implementation = text[begin:media_end]
            self.assertNotIn('setProperty', implementation)
            self.assertNotIn('getMatchingServices', implementation)
            self.assertNotIn('kIOServiceMatchedState', adapter)
            self.assertIn('window, gIOServicePlane, kIORegistryIterateRecursively', implementation)
            self.assertIn('metaCast ("AppleAPFSVolume")', adapter)
            self.assertEqual(text.count('gRootDiagnostic.windowBytes = device->windowSize ();'), 2)
            self.assertNotIn('stampVolumeUuid', text)
            for name in ('nabu_root_media', 'nabu_root_media_adapter'):
                binary = folder / name
                subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                    '-I', str(source.parent), '-I', str(folder),
                    str(ROOT / 'tests' / (name + '.cpp')), '-o', str(binary)],
                    check=True, capture_output=True)
                subprocess.run([binary], check=True, capture_output=True)
            sheng, sheng_evidence = stage(ROOT, folder / 'sheng', board='sheng')
            self.assertNotIn(patch, [entry['path'] for entry in sheng_evidence['patches']])
            self.assertFalse(sheng.with_name('NabuRootMedia.h').exists())
            self.assertNotIn('NabuRootMedia', sheng.read_text())
        self.assertEqual(before, subprocess.check_output(['git', '-C', checkout, 'diff', '--binary']))


if __name__ == '__main__':
    unittest.main()
