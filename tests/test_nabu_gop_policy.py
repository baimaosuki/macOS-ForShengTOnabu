import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class NabuGopPolicy(unittest.TestCase):
    def test_fixed_panel_mapping_and_bounds(self):
        source = r'''
#include <assert.h>
#include "platform/nabu/macos26/display/nabu_display_policy.h"
int main(void) {
    assert(nabuDisplayConsoleValid(0x9c000000,1600,2560,6400));
    assert(nabuDisplayConsoleValid(0x9c000003,1600,2560,6400));
    assert(!nabuDisplayConsoleValid(0x9c100000,1600,2560,6400));
    assert(!nabuDisplayConsoleValid(0x9c000000,2560,1600,10240));
    assert(!nabuDisplayConsoleValid(0x9c000000,1600,2560,6399));
    assert(!nabuDisplayConsoleValid(UINT64_MAX,1600,2560,6400));
    assert(!nabuDisplayConsoleValid(0x9c000000,1600,0,6400));
}
'''
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory)
            (path / 'policy.c').write_text(source)
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-I', str(ROOT), str(path / 'policy.c'), '-o', str(path / 'policy')], check=True)
            subprocess.run([str(path / 'policy')], check=True)
