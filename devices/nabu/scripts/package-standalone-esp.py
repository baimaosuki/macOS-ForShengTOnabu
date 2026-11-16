#!/usr/bin/env python3
"""Stage the existing NABU payload with a required, independently loaded EFI driver."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import tomllib
import zipfile


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def pe(path, subsystem):
    data = path.read_bytes()
    assert data[:2] == b'MZ'
    at = struct.unpack_from('<I', data, 60)[0]
    assert data[at:at + 4] == b'PE\0\0'
    assert struct.unpack_from('<H', data, at + 4)[0] == 0xaa64
    assert struct.unpack_from('<H', data, at + 24 + 68)[0] == subsystem


p = argparse.ArgumentParser(description=__doc__)
for name in ('base', 'loader', 'menu', 'qemu-report', 'output'):
    p.add_argument('--' + name, type=Path, required=True)
p.add_argument('--source-commit', required=True)
a = p.parse_args()
assert not a.output.exists()
pe(a.loader, 11)
pe(a.menu, 10)
qemu = json.loads(a.qemu_report.read_text())
assert qemu['actual_built_pe_executed'] and qemu['revision_1_and_2_replacement']
assert qemu['missing_required_driver_prevents_old_loader']
assert qemu['loader_sha256'] == sha(a.loader) and qemu['menu_sha256'] == sha(a.menu)
shutil.copytree(a.base / 'EFI', a.output / 'EFI')
drivers = a.output / 'EFI/UniXNU/Drivers'
drivers.mkdir(exist_ok=True)
shutil.copyfile(a.loader, drivers / 'DarwinBoot.efi')
shutil.copyfile(a.menu, a.output / 'EFI/BOOT/BOOTAA64.EFI')
shutil.copyfile(a.menu, a.output / 'EFI/UniXNU/UniXNU.efi')
config = a.output / 'EFI/UniXNU/unixnu.toml'
text = config.read_text()
assert not tomllib.loads(text).get('driver')
text += '''
[[driver]]
path = "Drivers/DarwinBoot.efi"
args = "replace-existing"
required = true
connect = false
'''
config.write_text(text)
parsed = tomllib.loads(text)
assert parsed['driver'] == [dict(path='Drivers/DarwinBoot.efi', args='replace-existing', required=True, connect=False)]
for f in (a.base / 'EFI/UniXNU/Payloads').iterdir():
    if f.is_file():
        assert sha(f) == sha(a.output / f.relative_to(a.base))
shutil.copyfile(a.qemu_report, a.output / 'qemu-verification.json')
(a.output / 'README.txt').write_text('''NABU macOS 27.3 / 26C5035r 独立 ESP 加载器包

安装：
1. 沿用已有、能显示画面的 NABU UEFI。将 ESP 挂载为 /esp，先备份现有 EFI 目录到电脑或其他分区。
2. 将本包 EFI/ 目录覆盖到 ESP 根目录的 EFI/，包括 BOOTAA64.EFI、UniXNU.efi、DarwinBoot.efi 和配置。
3. sync 后正常进入已有 UEFI，选择 ESP 上的 \\EFI\\BOOT\\BOOTAA64.EFI（或 \\EFI\\UniXNU\\UniXNU.efi）。
4. 本轮 27.3 Data 首次初始化：选 Initialize macOS Data and boot，再从 Cancel 切到 Initialize Data and boot 确认。
   该操作会重建 macOS Data。若本轮种子已经成功初始化，以后选 macOS 27.3。

DarwinBoot.efi 从 EFI/UniXNU/Drivers/ 加载。replace-existing 显式替换已有 loader v1/v2 接口；旧驱动仍在内存。
required=true 由本包配套菜单执行：加载器启动失败时显示错误并拒绝系统启动。
必须使用本包菜单文件，避免固件中旧版内置 UniXNU 忽略 required 字段。

macosroot 系统镜像与上一份 26C5035r 完全相同，已刷入该镜像可继续使用。
本包不包含 boot.img；ZIP 是文件更新包，不是原始 ESP 分区镜像。
QEMU 已运行实际 EFI 驱动，验证替换、恢复、错误处理。实际内核通过生产解析器。
尚未验证 NABU 实机内核交接或桌面显示。
''')
files = {str(f.relative_to(a.output)): dict(bytes=f.stat().st_size, sha256=sha(f))
         for f in sorted((a.output / 'EFI').rglob('*')) if f.is_file()}
(a.output / 'SHA256SUMS').write_text(''.join(f'{item["sha256"]}  {name}\n' for name, item in files.items()))
report = dict(format='NABU independent ESP file update', build='26C5035r',
              source_commit=a.source_commit, loader_path='EFI/UniXNU/Drivers/DarwinBoot.efi',
              loader_sha256=sha(a.loader), menu_sha256=sha(a.menu), files=files,
              total_efi_bytes=sum(e['bytes'] for e in files.values()),
              native_payload_preserved=True, uses_existing_uefi=True,
              requires_new_boot_image=False, required_driver=True,
              qemu_executed=True, hardware_tested=False, desktop_verified=False)
(a.output / 'delivery.json').write_text(json.dumps(report, indent=2) + '\n')
archive = Path(str(a.output) + '.zip')
assert not archive.exists()
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=3) as z:
    for f in a.output.rglob('*'):
        if f.is_file():
            z.write(f, f.relative_to(a.output))
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
print(json.dumps(dict(archive=str(archive.resolve()), sha256=sha(archive),
                      bytes=archive.stat().st_size, total_efi_bytes=report['total_efi_bytes']), indent=2))
