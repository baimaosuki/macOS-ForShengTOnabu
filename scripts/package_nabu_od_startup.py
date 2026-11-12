#!/usr/bin/env python3
"""Pair the OD startup repair with the already verified 25F71 services kernel."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

from prepare_nabu_boot_addon import prepare, validate_addon, check_trust_membership

ROOT = Path(__file__).resolve().parents[1]
FILES = ('kernel.macho', 'kernel.macho.adt', 'kernel.macho.trustcache', 'kernel.macho.gpuaddon')


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def package(base, shim_dir, od_dir, output, ci_metadata):
    baseline = json.loads((base / 'delivery.json').read_text())
    if baseline.get('target') != 'NABU macOS 26.5 / 25F71':
        raise ValueError('requires the verified 25F71 services package')
    original = base / 'candidate/EFI/UniXNU'
    for name in FILES:
        if sha(original / 'Payloads' / name) != baseline['payload_sha256'][name]:
            raise ValueError('base payload differs: ' + name)
    if sha(original / 'unixnu.toml') != baseline['toml_sha256']:
        raise ValueError('base configuration differs')
    ci = json.loads(ci_metadata.read_text())
    if ci.get('conclusion') != 'success':
        raise ValueError('owner boot tool CI did not succeed')
    shim_record = json.loads((shim_dir / 'build.json').read_text())
    source = ROOT / shim_record['source']
    if sha(source) != shim_record['source_sha256']:
        raise ValueError('compiled boot tool source differs from this checkout')
    if b'OD readonly union ready' not in (shim_dir / 'apfs_boot_util').read_bytes():
        raise ValueError('boot tool omits the OD overlay installer')
    output.mkdir(parents=True, exist_ok=False)
    addon = output / 'boot-addon'
    addon_record = prepare(shim_dir, ROOT / 'out/nabu-25F71-trustcache/verified/kernel.macho.trustcache',
                           addon, od_dir=od_dir)
    installed = output / 'EFI/UniXNU'
    payload = installed / 'Payloads'
    payload.mkdir(parents=True)
    for name in ('kernel.macho', 'kernel.macho.adt'):
        shutil.copy2(original / 'Payloads' / name, payload / name)
    for name in ('kernel.macho.trustcache', 'kernel.macho.gpuaddon'):
        shutil.copy2(addon / name, payload / name)
    shutil.copy2(original / 'unixnu.toml', installed / 'unixnu.toml')
    checked = validate_addon((payload / 'kernel.macho.gpuaddon').read_bytes())
    if checked['files'] != 2:
        raise ValueError('OD file missing from the final addon')
    check_trust_membership(checked, (payload / 'kernel.macho.trustcache').read_bytes())
    od_record = json.loads((od_dir / 'build.json').read_text())
    report = dict(target=baseline['target'], base_kernel_ci_run=baseline['ci_run'],
        boot_tools_ci=ci, kernel_recompiled=False, kernel_sha256=sha(payload / 'kernel.macho'),
        kernel_matches_services_package=True, od_repair=od_record, boot_addon=addon_record,
        base_implemented=baseline['implemented'],
        payload_sha256={name: sha(payload / name) for name in FILES},
        toml_sha256=sha(installed / 'unixnu.toml'),
        device_tested=False, desktop_verified=False,
        root_reflash_required=False, data_reflash_required=False,
        limits=['Candidate changes callback-free OD first-connection queue ordering.',
                'It does not establish the underlying cause of delayed worker execution.',
                'Real mount, codesign acceptance, directory queries and GUI still require device boot.'])
    (output / 'delivery.json').write_text(json.dumps(report, indent=2) + '\n')
    (output / 'README.txt').write_text('''NABU 25F71 — OpenDirectory first-connection repair candidate

本包包含此前 2–5 项服务集成和第 1 项 OD 首次连接队列修复候选。
内核沿用成功 CI 43115207926 的已验证产物，不重新编译。
OD 只修改 callback-free 首次 create 的连接调用：提交 state 队列后释放该队列，
同步排空原 operations 队列，再进入原超时/状态检查。保留原 worker 和信号发送。
新 boot shim 在 mount phase 1/2 将只读 OD 目录以 union bindfs 挂到 /usr/libexec；
校验 OD 替换和 xpcproxy 等下层文件仍可见。保留原签名权限及配套新信任缓存。

实机尚未验证，不能声称已进入桌面。
TWRP 下备份现有五文件，然后把 EFI/UniXNU 中的五文件放到 ESP 相同位置，sync 后重启。
无需重刷 root_image.bin、data.dbr、引导器或分区；不要覆盖 XNU2LOG.BIN。
看新日志的 owner OD overlay 状态、opendirectoryd 是否再次 SIGABRT，以及登录是否继续。
''')
    archive = output.with_suffix('.zip')
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted((output / 'EFI').rglob('*')):
            if path.is_file():
                bundle.write(path, path.relative_to(output))
        for name in ('delivery.json', 'README.txt'):
            bundle.write(output / name, name)
    receipt = dict(path=str(archive), bytes=archive.stat().st_size, sha256=sha(archive),
                   kernel_sha256=report['kernel_sha256'], device_tested=False)
    (output / 'package.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('base', 'shim-dir', 'od-dir', 'output', 'ci-metadata'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.base, args.shim_dir, args.od_dir, args.output, args.ci_metadata), indent=2))
