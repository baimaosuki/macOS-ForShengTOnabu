"""Run the production input state/register algorithms with error-injected buses."""
from pathlib import Path
import subprocess
import shutil
import pytest
from usb_runtime27 import (check_mmio_snapshot_and_clocks,
                          check_actual_level_irq_nub_and_old_window,
                          check_original_abi_and_diagnostic_claims,
                          check_actual_dma_allocation_and_failure_paths)
DEVICE=Path(__file__).resolve().parents[1]

def test_usb_mmio_snapshot_and_clocks(tmp_path):
    check_mmio_snapshot_and_clocks(tmp_path)

def test_usb_level_irq_and_registration_window(tmp_path):
    check_actual_level_irq_nub_and_old_window(tmp_path)

def test_usb_original_abi_and_diagnostics():
    check_original_abi_and_diagnostic_claims()

def test_usb_dma_allocation_and_failures(tmp_path):
    check_actual_dma_allocation_and_failure_paths(tmp_path)
@pytest.mark.parametrize('source',['test_input_hardware27.cpp','test_touch_pointer27.cpp'])
def test_input_algorithms(tmp_path,source):
    compiler=shutil.which('c++') or shutil.which('clang++-17')
    binary=tmp_path/'input-test'
    subprocess.run([compiler,'-std=c++17','-Wall','-Wextra','-Werror',
                    '-fsanitize=address,undefined','-fno-sanitize-recover=all',
                    '-I',str(DEVICE/'package'),str(DEVICE/'tests'/source),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True)


def test_xhci_dispatch_is_native_inherited():
    import importlib.util
    import json
    script=DEVICE/'scripts/build-nabu-usb27.py'
    spec=importlib.util.spec_from_file_location('usb_header',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    abi=json.loads((script.parent/'usb-xhci-26C5035r.json').read_text())
    # This target was resolved in the original 26C5035r com.apple.kernel symbols.
    assert any(s['target']=='0xfffffe000c2edb3c' for s in abi['slots'])
    assert 'Dispatch(IORPC)' not in module.header(abi)
