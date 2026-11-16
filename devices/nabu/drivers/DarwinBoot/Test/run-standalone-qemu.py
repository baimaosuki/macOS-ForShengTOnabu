#!/usr/bin/env python3
"""Exercise the built ESP loader and menu with QEMU's real UEFI services."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

p = argparse.ArgumentParser(description=__doc__)
for name in ('loader', 'menu', 'test-app', 'firmware', 'output'):
    p.add_argument('--' + name, type=Path, required=True)
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='esp-loader-qemu-', dir=a.output) as temporary:
    w = Path(temporary)
    files = w / 'files'
    (files / 'EFI/BOOT').mkdir(parents=True)
    (files / 'EFI/UniXNU/Drivers').mkdir(parents=True)
    shutil.copyfile(a.test_app, files / 'EFI/BOOT/BOOTAA64.EFI')
    shutil.copyfile(a.loader, files / 'EFI/UniXNU/Drivers/DarwinBoot.efi')
    shutil.copyfile(a.menu, files / 'EFI/UniXNU/UniXNU.efi')
    (files / 'EFI/UniXNU/unixnu.toml').write_text('''version = 1
[boot]
timeout = 0
default = "probe"
[ui]
console = "text"
logo = false
[[driver]]
path = "Drivers/required-missing.efi"
required = true
connect = false
[[entry]]
id = "probe"
title = "Required loader failure check"
type = "xnu"
path = "not-read.macho"
''')
    disk = w / 'esp.img'
    subprocess.run(['mformat', '-C', '-F', '-T', '131072', '-i', str(disk), '::'], check=True)
    subprocess.run(['mcopy', '-s', '-i', str(disk), str(files / 'EFI'), '::/'], check=True)
    command = ['qemu-system-aarch64', '-machine', 'virt', '-cpu', 'cortex-a57', '-m', '1024',
               '-bios', str(a.firmware), '-drive', f'if=virtio,format=raw,file={disk}',
               '-nographic', '-monitor', 'none', '-net', 'none', '-no-reboot']
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
    except subprocess.TimeoutExpired as error:
        (a.output / 'qemu.log').write_bytes(error.stdout or b'')
        raise SystemExit('QEMU timed out; inspect qemu.log')
    (a.output / 'qemu.log').write_bytes(result.stdout)
    text = result.stdout.decode('utf-8', errors='replace')
    expected = ['PASS replacement requires explicit option',
                'PASS LocateProtocol selected ESP loader',
                'PASS original firmware loader restored',
                'PASS unknown protocol revision retained',
                'PASS ambiguous providers left intact',
                'PASS standalone installation without firmware loader',
                'PASS no stale loader after uninstall',
                'boot: required driver failed:',
                'PASS required driver failure kept old loader uncalled']
    missing = [item for item in expected if item not in text]
    assert result.returncode == 0 and not missing and 'ESP-LOADER-TEST: FAIL' not in text, (
        result.returncode, missing, str(a.output / 'qemu.log'))
    assert text.count('PASS original firmware loader restored') == 2
    report = dict(qemu_returncode=result.returncode, checks=expected,
                  revision_1_and_2_replacement=True, uninstall_restores_firmware=True,
                  missing_required_driver_prevents_old_loader=True,
                  actual_built_pe_executed=True, nabu_hardware_tested=False,
                  loader_sha256=hashlib.sha256(a.loader.read_bytes()).hexdigest(),
                  menu_sha256=hashlib.sha256(a.menu.read_bytes()).hexdigest())
    (a.output / 'qemu-verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print('PASS: actual EFI driver replacement/restoration and menu failure guard under QEMU UEFI')
