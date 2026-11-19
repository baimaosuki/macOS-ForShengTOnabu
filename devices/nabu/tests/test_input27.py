"""Run the production input state/register algorithms with error-injected buses."""
from pathlib import Path
import subprocess
import shutil
import pytest
DEVICE=Path(__file__).resolve().parents[1]
@pytest.mark.parametrize('source',['test_input_hardware27.cpp','test_touch_pointer27.cpp'])
def test_input_algorithms(tmp_path,source):
    compiler=shutil.which('c++') or shutil.which('clang++-17')
    binary=tmp_path/'input-test'
    subprocess.run([compiler,'-std=c++17','-Wall','-Wextra','-Werror',
                    '-fsanitize=address,undefined','-fno-sanitize-recover=all',
                    '-I',str(DEVICE/'package'),str(DEVICE/'tests'/source),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
