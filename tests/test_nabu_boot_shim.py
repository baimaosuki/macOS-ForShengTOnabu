#!/usr/bin/env python3
"""Exercise the boot adapter's filesystem checks using a Linux host binary."""
import errno
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import importlib.util
import hashlib
import json
import plistlib

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'platform/nabu/macos26/bootshim/apfs_boot_util.c'


def test_shipping_builder_records_exact_script_and_probe_sources(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('shim_builder_fixture', ROOT / 'scripts/build_nabu_boot_shim.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    cdhash = '12' * 20
    raw = bytearray(64)
    import struct
    struct.pack_into('<III', raw, 0, 0xfeedfacf, 0x100000c, 0)

    def run(*args):
        if args[0] == 'xcrun':
            assert '-target' in args and 'arm64-apple-macos26.5' in args
            Path(args[args.index('-o') + 1]).write_bytes(raw)
        output = ''
        if '--verbose=4' in args:
            output = f'CDHash={cdhash}\n'
        if '--entitlements' in args and ':-' in args:
            output = (SOURCE.parent / 'entitlements.plist').read_text()
        return subprocess.CompletedProcess(args, 0, stdout=output, stderr='')

    monkeypatch.setattr(builder.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(builder, 'run', run)
    monkeypatch.setattr(builder.verify_page_hashes, 'verify', lambda _: dict(
        signed=True, ok=True, code_slots=1, cdhash=cdhash))
    result = builder.build(tmp_path / 'build')
    assert result['probe_policy'] == builder.probe_policy()
    assert result['probe_policy']['powerd_after_discovery_seconds'] == [0, 60, 120]
    assert result['probe_policy']['version'] == 7
    assert result['probe_policy']['default'] == 'raw-only'
    assert result['probe_policy']['general_registers'] == 'x0-x28-raw'
    assert result['probe_policy']['optional_targets'] == ['lsd', 'coreservicesd', 'launchservicesd', 'powerd']
    assert result['build_script_sha256'] == hashlib.sha256(Path(builder.__file__).read_bytes()).hexdigest()
    assert result['raw_stacks_header_sha256'] == hashlib.sha256((SOURCE.parent / 'raw_stacks.h').read_bytes()).hexdigest()
    assert result['session_probe_script_sha256'] == hashlib.sha256((SOURCE.parent / 'session_probe.sh').read_bytes()).hexdigest()
    assert json.loads((tmp_path / 'build/build.json').read_text()) == result
    entitlements = plistlib.loads((SOURCE.parent / 'entitlements.plist').read_bytes())
    assert entitlements['com.apple.system-task-ports.read'] and entitlements['com.apple.system-task-ports.read.safe']


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux host logic tests')
class BootShim(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('cc')
        if compiler is None:
            raise unittest.SkipTest('a C compiler is required')
        cls.build = tempfile.TemporaryDirectory(prefix='nabu-boot-shim-')
        cls.binary = Path(cls.build.name) / 'apfs_boot_util'
        subprocess.run([compiler, '-std=c11', '-D_GNU_SOURCE',
                        '-DNABU_BOOT_SHIM_HOST_TEST', '-Wall', '-Wextra', '-Werror',
                        str(SOURCE), '-o', str(cls.binary)], check=True,
                       capture_output=True, text=True)
        # This binary receives a deterministic read-only statfs response. It
        # exercises the same branch even on hosts without read-only mounts.
        fixture = Path(cls.build.name) / 'readonly_statfs.c'
        fixture.write_text('''#include <string.h>
#include <sys/vfs.h>
#include <sys/statvfs.h>
int statfs(const char *path, struct statfs *result) {
    (void)path;
    memset(result, 0, sizeof(*result));
    result->f_flags = ST_RDONLY;
    return 0;
}
''')
        cls.readonly_binary = Path(cls.build.name) / 'apfs_boot_util_readonly'
        subprocess.run([compiler, '-std=c11', '-D_GNU_SOURCE',
                        '-DNABU_BOOT_SHIM_HOST_TEST', '-Wall', '-Wextra', '-Werror',
                        str(SOURCE), str(fixture), '-o', str(cls.readonly_binary)],
                       check=True, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='nabu-boot-layout-')
        self.addCleanup(self.work.cleanup)
        self.data = Path(self.work.name) / 'Data'
        self.data.mkdir()
        self.vm_target = self.data / 'vm'
        self.vm_target.mkdir()
        self.vm = Path(self.work.name) / 'VM'
        self.vm.symlink_to(self.vm_target, target_is_directory=True)

    def invoke(self, *phase, data=None, vm=None, binary=None):
        env = dict(os.environ, NABU_BOOT_TEST_DATA=str(data or self.data),
                   NABU_BOOT_TEST_VM=str(vm or self.vm))
        return subprocess.run([str(binary or self.binary), *phase], env=env,
                              text=True, capture_output=True)

    def assert_failure(self, result, error):
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'errno={error} ', result.stderr)
        self.assertNotIn('ready:', result.stderr)

    def test_both_phases_require_and_probe_real_writable_directories(self):
        for phase in ('1', '2'):
            with self.subTest(phase=phase):
                result = self.invoke(phase)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f'phase {phase} ready:', result.stderr)
                for path in (self.data, self.vm):
                    for operation in ('write', 'close', 'unlink'):
                        self.assertIn(f'probe {operation} {path}/', result.stderr)
                self.assertEqual(list(self.data.iterdir()), [self.vm_target])
                self.assertEqual(list(self.vm_target.iterdir()), [])

    def test_invalid_or_missing_phase_fails_before_any_probe(self):
        for arguments in ((), ('0',), ('3',), ('01',), ('1', 'extra')):
            with self.subTest(arguments=arguments):
                result = self.invoke(*arguments)
                self.assert_failure(result, errno.EINVAL)
                self.assertNotIn('probe write', result.stderr)

    def test_missing_data_or_vm_never_succeeds(self):
        for phase in ('1', '2'):
            for field in ('data', 'vm'):
                with self.subTest(phase=phase, field=field):
                    self.assert_failure(self.invoke(phase, **{
                        field: Path(self.work.name) / 'missing'}), errno.ENOENT)

    def test_data_and_vm_must_be_directories(self):
        file = self.data / 'regular-file'
        file.write_text('unchanged')
        for field in ('data', 'vm'):
            with self.subTest(field=field):
                self.assert_failure(self.invoke('1', **{field: file}), errno.ENOTDIR)
        self.assertEqual(file.read_text(), 'unchanged')

    def test_vm_must_share_data_filesystem(self):
        other = next((Path(p) for p in ('/dev/shm', '/proc', '/sys')
                      if Path(p).is_dir() and os.stat(p).st_dev != self.data.stat().st_dev), None)
        if other is None:
            self.skipTest('no directory on another filesystem')
        result = self.invoke('2', vm=other)
        self.assert_failure(result, errno.EXDEV)
        self.assertNotIn('probe write', result.stderr)

    @unittest.skipIf(os.geteuid() == 0, 'root bypasses directory write permissions')
    def test_vm_probe_failure_prevents_success_and_cleans_data_probe(self):
        self.vm_target.chmod(0o555)
        self.addCleanup(self.vm_target.chmod, 0o755)
        for phase in ('1', '2'):
            with self.subTest(phase=phase):
                result = self.invoke(phase)
                self.assert_failure(result, errno.EACCES)
                self.assertIn('create exclusive probe', result.stderr)
                self.assertIn(f'probe unlink {self.data}/', result.stderr)
                self.assertEqual(list(self.data.iterdir()), [self.vm_target])
                self.assertEqual(list(self.vm_target.iterdir()), [])

    def test_readonly_data_filesystem_fails_without_writing(self):
        for phase in ('1', '2'):
            with self.subTest(phase=phase):
                result = self.invoke(phase, binary=self.readonly_binary)
                self.assert_failure(result, errno.EROFS)
                self.assertNotIn('probe write', result.stderr)
        self.assertEqual(list(self.vm_target.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
