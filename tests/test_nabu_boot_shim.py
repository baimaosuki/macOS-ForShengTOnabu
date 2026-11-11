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

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'platform/nabu/macos26/bootshim/apfs_boot_util.c'


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
