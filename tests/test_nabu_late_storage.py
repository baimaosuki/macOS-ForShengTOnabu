# SPDX-License-Identifier: GPL-3.0-or-later
import ast
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from nabu_storage_source import stage
from nabu_storage_contract import validate_module, audit_kernel, MODULE_SYMBOL, KERNEL_SYMBOLS


class LateStorage(unittest.TestCase):
    def test_actual_staged_lifecycle_and_module_adapter(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            self.skipTest('host C++ compiler required')
        checkout = ROOT / 'UniXNU/drivers/NativeUfs'
        before = subprocess.check_output(['git', '-C', checkout, 'diff', '--binary'])
        with tempfile.TemporaryDirectory(dir=ROOT / 'out') as temp:
            folder = Path(temp)
            source, evidence = stage(ROOT, folder / 'stage', board='nabu')
            headers = ROOT / 'platform/nabu/macos26/storage'
            for name in ('NabuStorageModule.h', 'NabuStorageLifecycle.h'):
                self.assertEqual(source.with_name(name).read_bytes(), (headers / name).read_bytes())
            fake = folder / 'libkern/c++'
            fake.mkdir(parents=True)
            (fake / 'OSSymbol.h').write_text('// API defined by fixture\n')
            for name in ('nabu_storage_lifecycle', 'nabu_storage_module'):
                binary = folder / name
                result = subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                    '-I', str(folder), '-I', str(source.parent),
                    str(ROOT / 'tests' / (name + '.cpp')), '-o', str(binary)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                subprocess.run([binary], check=True, capture_output=True)
            self.assertFalse(evidence['nested_checkouts_modified'])
        self.assertEqual(before, subprocess.check_output(['git', '-C', checkout, 'diff', '--binary']))

    def test_bsd_patch_applies_and_keeps_order(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'out') as temp:
            folder = Path(temp)
            source = folder / 'bsd/kern/bsd_init.c'
            source.parent.mkdir(parents=True)
            # Exact existing checkpoint preimage is used by the real build.
            original = (ROOT / 'sources/macos26/xnu/bsd/kern/bsd_init.c').read_text()
            original = original.replace('bsd_init_kprintf("calling vfsinit\\n");', 'printf("NABU-CHK vfsinit\\n");')
            source.write_text(original)
            patch = ROOT / 'platform/nabu/macos26/desktop/late-storage-start.patch'
            subprocess.run(['git', 'apply', str(patch)], cwd=folder, check=True, capture_output=True,
                env={'PATH': __import__('os').environ['PATH'], 'GIT_CEILING_DIRECTORIES': str(folder.parent)})
            text = source.read_text()
            self.assertEqual(text.count('nabu_source_storage_start();'), 1)
            self.assertLess(text.index('vfsinit();'), text.index('nabu_source_storage_start();'))
            self.assertLess(text.index('nabu_source_storage_start();'), text.index('\n\tbsd_autoconf();'))

    def test_both_real_info_generators_disable_automatic_storage(self):
        # Execute the actual Info.plist construction AST, so an accidental
        # restoration in either independent packaging path is caught.
        for filename, function in [('build_macos26_storage.py', 'build'),
                                   ('link_nabu_desktop_storage.py', 'compiled_evidence')]:
            tree = ast.parse((ROOT / 'scripts' / filename).read_text())
            node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function)
            begin = next(i for i, n in enumerate(node.body)
                if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'info' for t in n.targets))
            statements = [node.body[begin]]
            if filename.startswith('build_'):
                statements.append(node.body[begin + 1]) # transport == ufs adjustment
            env = dict(transport='ufs', root_mode='desktop', board='nabu',
                bundle='org.blu-sharky.NativeUfs', component='NativeUfs', BUNDLE='virtio',
                PROTOCOL_CHARACTERISTICS={})
            exec(compile(ast.fix_missing_locations(ast.Module(body=statements, type_ignores=[])), filename, 'exec'), env)
            self.assertEqual(env['info']['IOKitPersonalities'], {})
        build = (ROOT / 'scripts/build_macos26_storage.py').read_text()
        self.assertIn('#include "NabuStorageModule.h"', build)
        self.assertIn('nabu_register_storage_start(nabu_storage_module_start)', build)
        kernel = (ROOT / 'scripts/build_macos26_xnu.py').read_text()
        self.assertIn("PATCHES.append(desktop / 'late-storage-start.patch')", kernel)
        self.assertIn('audit_kernel(destination.read_bytes(), actual)', kernel)

    def test_prelink_rejects_mixed_kernel_and_driver(self):
        evidence = dict(late_storage_start=True, automatic_storage_personalities=False)
        info = {'IOKitPersonalities': {}}
        own = {MODULE_SYMBOL: 1}
        native = {name: i + 1 for i, name in enumerate(KERNEL_SYMBOLS)}
        imports = {'_nabu_register_storage_start': native['_nabu_register_storage_start']}
        validate_module(evidence, info, own, native, imports)
        for bad_evidence, bad_info, bad_own, bad_native in [
            ({}, info, own, native),
            (evidence | {'late_storage_start': False}, info, own, native),
            (evidence, {'IOKitPersonalities': {'old': {'IOClass': 'NativeRamBlock'}}}, own, native),
            (evidence, info, {}, native), (evidence, info, own, {})]:
            with self.assertRaises(ValueError):
                validate_module(bad_evidence, bad_info, bad_own, bad_native, imports)
        for wrong in ({}, {'_nabu_register_storage_start': 0xdeadbeef}):
            with self.assertRaises(ValueError): validate_module(evidence, info, own, native, wrong)

    def test_compiled_call_audit(self):
        # A small real ARM64 Mach-O encoding exercises order/branch checks.
        data = bytearray(0x300)
        struct.pack_into('<8I', data, 0, 0xfeedfacf, 0x100000c, 0, 2, 1, 72, 0, 0)
        struct.pack_into('<II16s4Q4I', data, 32, 0x19, 72, b'__TEXT_EXEC', 0x1000,
                         len(data), 0, len(data), 5, 5, 0, 0)
        syms = {'_bsd_init': 0x1100, '_vfsinit': 0x1180,
            '_nabu_source_storage_start': 0x11a0, '_nabu_register_storage_start': 0x11e0,
            '_OSKextLoadKextWithIdentifier': 0x1200, '_end': 0x1250}
        def branch(at, target):
            struct.pack_into('<I', data, at - 0x1000, 0x94000000 | (((target - at) // 4) & 0x3ffffff))
        branch(0x1100, syms['_vfsinit']); branch(0x1104, syms['_nabu_source_storage_start'])
        branch(0x11a0, syms['_OSKextLoadKextWithIdentifier'])
        self.assertTrue(audit_kernel(data, syms)['compiled_calls_verified'])
        branch(0x1100, syms['_nabu_source_storage_start']); branch(0x1104, syms['_vfsinit'])
        with self.assertRaises(ValueError): audit_kernel(data, syms)


if __name__ == '__main__':
    unittest.main()
