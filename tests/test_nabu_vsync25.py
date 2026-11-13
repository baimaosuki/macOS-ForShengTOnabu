from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class NabuVsync25(unittest.TestCase):
    def test_native_state_and_sender_arguments(self):
        compiler = shutil.which('c++')
        if not compiler:
            self.skipTest('requires c++')
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / 'vsync25'
            subprocess.run([
                compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                '-fsanitize=address,undefined',
                str(Path(__file__).with_name('nabu_vsync25.cpp')),
                '-o', str(binary),
            ], check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True)
