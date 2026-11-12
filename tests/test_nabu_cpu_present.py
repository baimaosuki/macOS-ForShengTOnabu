from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

class NabuCpuPresent(unittest.TestCase):
    def test_clipped_scaled_scene_cursor_and_half_float(self):
        compiler = shutil.which('c++')
        if not compiler:
            self.skipTest('requires c++')
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / 'present'
            subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', str(Path(__file__).with_name('nabu_cpu_present.cpp')),
                            '-o', str(binary)], check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True)

    def test_native_layer_lifetime_updates_and_failure_retirement(self):
        compiler = shutil.which('c++')
        if not compiler:
            self.skipTest('requires c++')
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / 'lifecycle'
            subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-Wno-missing-field-initializers', '-fsanitize=address,undefined',
                            str(Path(__file__).with_name('nabu_cpu_present_lifecycle.cpp')),
                            '-o', str(binary)], check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True)
