#!/usr/bin/env python3
"""Package the 25F71 Hardware/AKS/GOP/TimeSync integration from one real CI run."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib
import uuid
import zipfile

from assemble_nabu_desktop_candidate import assemble
from native_macho import symbols
from prepare_nabu_boot_addon import prepare as prepare_addon

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def package(artifact, metadata, output, installed_toml, identity_file, *, cpu_compositor=False,
            windowserver_log=False, session_repair=False):
    metadata = json.loads(metadata.read_text())
    if metadata['conclusion'] != 'success':
        raise ValueError('requires a successful native CI build')
    build = json.loads((artifact / 'build.json').read_text())
    native = artifact / 'kernel.development.nabu'
    if sha(native) != build['executable_kernel_sha256']:
        raise ValueError('CI kernel differs from its build record')
    if '_native_owner_hardware_volume_status' not in symbols(native):
        raise ValueError('native kernel omitted the writable Hardware view')
    if cpu_compositor:
        needed = {'_nabu_cpu_compositor_enabled', '_nabu_cpu_compositor_pages_applied',
                  '_nabu_cpu_compositor_page_mask'}
        if not needed <= symbols(native).keys() or not build.get('cpu_compositor'):
            raise ValueError('native kernel omitted the CPU compositor policy')
        contract = ROOT / 'platform/nabu/macos26/software-compositor/contract.json'
        if build['cpu_compositor']['contract_sha256'] != sha(contract):
            raise ValueError('CPU compositor contract differs from the compiled kernel')
    if windowserver_log:
        trace = build.get('windowserver_trace') or {}
        if trace.get('header_sha256') != sha(ROOT / 'platform/nabu/macos26/log/windowserver_trace.h'):
            raise ValueError('WindowServer observer differs from the compiled kernel')
        if b'NABU-WS event=' not in native.read_bytes():
            raise ValueError('native kernel omitted the WindowServer journal observer')
    if session_repair:
        repair = build.get('session_repair') or {}
        if not cpu_compositor or not windowserver_log:
            raise ValueError('session repair package requires CPU compositor and session exit observers')
        if repair.get('session_trace_sha256') != sha(ROOT / 'platform/nabu/macos26/log/session_trace.h'):
            raise ValueError('session observer differs from the compiled kernel')
        if repair.get('filesystem_patch_sha256') != sha(ROOT / 'platform/nabu/macos26/desktop/session-filesystem.patch'):
            raise ValueError('session filesystem fixes differ from the compiled kernel')
        if '_native_union_visflags' not in symbols(native):
            raise ValueError('native kernel omitted root union visibility fixes')
    output.mkdir(parents=True, exist_ok=False)

    def run(arguments, name):
        print(name, flush=True)
        with (output / name).open('w') as stream:
            subprocess.run([str(item) for item in arguments], cwd=ROOT,
                           stdout=stream, stderr=subprocess.STDOUT, check=True)

    linked = output / 'linked-ufs'
    run([sys.executable, ROOT / 'scripts/link_nabu_desktop_storage.py',
         '--compiled', artifact / 'ufs-driver', '--policy', ROOT / 'out/nabu-desktop-policy',
         '--output', linked], 'storage-link.log')
    compiled = json.loads((linked / 'build.json').read_text())
    if not compiled.get('desktop_services') or not compiled.get('native_gop_display'):
        raise ValueError('compiled companion omitted desktop services or display')
    config = json.loads((ROOT / 'config/nabu-macos26-desktop-prelink.json').read_text())
    roots = [arg for value in config['roots'] for arg in ('--root', value)]
    kernel = output / 'kernel.macho'
    report_path = output / 'prelink-report.json'
    run([sys.executable, ROOT / 'scripts/prelink_kcg_bind.py', '--native', native,
         '--kc', ROOT / 'out/nabu-bootkc/kernelcache.release.mac13g.nabu.kc',
         '--fileset', ROOT / 'out/macos26-assets/kc.mac13g.fileset.json',
         '--storage-driver', linked, '--output', kernel, '--report', report_path,
         *roots], 'prelink.log')
    run([sys.executable, ROOT / 'scripts/prelink_verify.py', '--image', kernel,
         '--report', report_path, '--native', native, '--samples', '1000'], 'verify.log')
    addon = output / 'boot-addon'
    prepare_addon(artifact / 'boot-shim',
                  ROOT / 'out/nabu-25F71-trustcache/verified/kernel.macho.trustcache', addon)
    if cpu_compositor and json.loads((addon / 'build.json').read_text())['addon']['files'] != 1:
        raise ValueError('CPU compositor package must preserve the original OD executable')
    # The board-owned public identity persists across rebuilt packages.
    if not identity_file.exists():
        identity_file.parent.mkdir(parents=True, exist_ok=True)
        with identity_file.open('x') as stream:
            json.dump(dict(board='xiaomi,nabu', platform_uuid=str(uuid.uuid4()).upper()), stream, indent=2)
            stream.write('\n')
    identity = json.loads(identity_file.read_text())
    if identity.get('board') != 'xiaomi,nabu':
        raise ValueError('wrong board identity')
    platform_uuid = uuid.UUID(identity['platform_uuid'])
    candidate = output / 'candidate'
    assembled = assemble(kernel, report_path, ROOT / 'out/nabu-desktop-policy',
        ROOT / 'out/nabu-desktop-data/data.dbr', ROOT / 'out/nabu-desktop-root',
        ROOT / 'out/nabu-desktop-loader-final', candidate,
        trust_cache=addon / 'kernel.macho.trustcache',
        boot_addon=addon / 'kernel.macho.gpuaddon', disable_watchdogd=True,
        trace_boot_processes=True, native_gop_display=True, platform_uuid=platform_uuid)
    # Preserve the device's working ESP logger allocation and boot entry.
    text = installed_toml.read_text()
    parsed = tomllib.loads(text)
    entry = next(e for e in parsed['entry'] if e['id'] == parsed['boot']['default'])
    tokens = entry['args'].split()
    if 'nabu-log=1' not in tokens or not any(t.startswith('nabu-log-token=') for t in tokens):
        raise ValueError('installed configuration has no working ESP logger contract')
    if any(t.startswith('native-gop-display=') and t != 'native-gop-display=1' for t in tokens):
        raise ValueError('installed display boot argument conflicts')
    if 'native-gop-display=1' not in tokens:
        matches = list(re.finditer(r'(?m)^(\s*args\s*=\s*")([^"\n]*)("\s*)$', text))
        if len(matches) != 1 or matches[0].group(2) != entry['args']:
            raise ValueError('expected one unambiguous installed args line')
        match = matches[0]
        text = text[:match.start(2)] + match.group(2) + ' native-gop-display=1' + text[match.end(2):]
    if cpu_compositor:
        parsed_args = tomllib.loads(text)['entry'][0]['args'].split()
        if any(t.startswith('sheng-recovery-ui=') and t != 'sheng-recovery-ui=0' for t in parsed_args):
            raise ValueError('CPU compositor conflicts with the legacy recovery page policies')
        if any(t.startswith('nabu-cpu-compositor=') and t != 'nabu-cpu-compositor=1' for t in parsed_args):
            raise ValueError('CPU compositor boot argument conflicts')
        if 'nabu-cpu-compositor=1' not in parsed_args:
            matches = list(re.finditer(r'(?m)^(\s*args\s*=\s*")([^"\n]*)("\s*)$', text))
            if len(matches) != 1:
                raise ValueError('expected one installed args line')
            match = matches[0]
            text = text[:match.end(2)] + ' nabu-cpu-compositor=1' + text[match.end(2):]
    if windowserver_log:
        matches = list(re.finditer(r'(?m)^(\s*args\s*=\s*")([^"\n]*)("\s*)$', text))
        if len(matches) != 1:
            raise ValueError('expected one installed args line')
        match = matches[0]
        changes = {'nabu-ws-log': '1', 'sheng-windowserver-stop': '0',
                   'sheng-stderr-log': '2', 'sheng-stderr-name': 'WindowServer'}
        if session_repair:
            changes.update({'nabu-session-log': '1', 'sheng-stderr-name': 'boot-filesystems'})
        args = [a for a in match.group(2).split() if a.split('=', 1)[0] not in changes]
        args += [key + '=' + value for key, value in changes.items()]
        text = text[:match.start(2)] + ' '.join(args) + text[match.end(2):]
    config_path = candidate / 'EFI/UniXNU/unixnu.toml'
    config_path.write_text(text)
    final_config = tomllib.loads(text)
    assert final_config['entry'][0]['args'].split().count('native-gop-display=1') == 1
    report = json.loads(report_path.read_text())
    names = ['kernel.macho', 'kernel.macho.adt', 'kernel.macho.trustcache', 'kernel.macho.gpuaddon']
    payload = candidate / 'EFI/UniXNU/Payloads'
    delivery = dict(target='NABU macOS 26.5 / 25F71', ci_run=metadata['id'],
        commit=metadata['head_sha'], ci_url=metadata['html_url'],
        native_sha256=sha(native), kernel_sha256=sha(kernel), kernel_bytes=kernel.stat().st_size,
        platform_uuid=str(platform_uuid).upper(), prelink_components=report['components'],
        implemented=['checked writable Hardware view', 'software AppleKeyStore',
                     'CPU scanout backend', 'local Ethernet identity for original TimeSync'],
        unresolved=['OD first-launch internal connection timeout',
                    'device validation of new services and actual WindowServer presentation'],
        cpu_compositor=cpu_compositor, windowserver_log=windowserver_log, od_overlay_included=False,
        session_repair=build.get('session_repair') if session_repair else None,
        gpu_acceleration=False, sep_backed=False, network_connectivity=False,
        device_tested=False, desktop_verified=False, root_reflash_required=False,
        data_reflash_required=False, esp_logger_allocation_preserved=True,
        payload_sha256={name: sha(payload / name) for name in names},
        toml_sha256=sha(config_path), assembled_boot_args_without_log_token=assembled['boot_args'])
    readme = '''NABU macOS 26.5 / 25F71 — Hardware / AKS / GOP / TimeSync

本包保留 Quarantine、只读 UFS 根盘、RAM Data、Preboot、watchdog 策略和 ESP 日志。
新增可写 Hardware 状态目录、主线软件 AppleKeyStore、25F71 CPU framebuffer 后端，
以及供原版 TimeSync 使用的稳定本地接口身份。保持 cpus=1；不提供 GPU、联网或 SEP。
OD 首次内部连接超时尚未修复；显示后端注册不代表 WindowServer 已完成桌面呈现。

TWRP 中将现有 ESP 挂到 /esp，先保存现有五个文件，再替换本包以下五个文件：
EFI/UniXNU/Payloads/kernel.macho
EFI/UniXNU/Payloads/kernel.macho.adt
EFI/UniXNU/Payloads/kernel.macho.trustcache
EFI/UniXNU/Payloads/kernel.macho.gpuaddon
EFI/UniXNU/unixnu.toml
adb shell sync
adb reboot

无需重刷 root_image.bin、data.dbr、引导器或分区。不覆盖 XNU2LOG.BIN。
启动配置沿用归档设备的日志 token，仅添加 native-gop-display=1。
新日志检查 Hardware status、NABU AKS、NabuTimeSyncIdentity、TimeSync、GOP swap；
继续保存完整日志，以区分 OD 连接、时钟和 GUI 的实际状态。
'''
    if cpu_compositor:
        delivery['implemented'].append('original CompositorSW with BGRA screen/intermediate allocation')
        delivery['unresolved'] = ['registered identity resolver query timeout remains unproven',
                                  'device validation of CPU compositor frames and login desktop']
        readme = '''NABU macOS 26.5 / 25F71 — CPU compositor test candidate

本包保留已实机启动的 2–5 项服务，新增 nabu-cpu-compositor=1。
通过三个原版缓存代码页的明确开发适配，让 Metal 能力检查返回无设备，
由原版 WindowServer 选择 CompositorSW，并在最终及中间表面分配前选择 BGRA。
该改动作用于共享内存代码页；不改根盘上的共享缓存，不提供 Metal/GPU。
页原始签名和完整 SHA-256 必须匹配，重载时重新验证再应用。
尚未在 NABU 验证首帧或登录桌面，软件路径的速度也未测定。
本包不携带 OD 同步队列候选，保留原版 OD 用户态。

TWRP 中备份现有五文件，然后替换 EFI/UniXNU 下五文件，sync 后重启。
无需重刷 root_image.bin、data.dbr、引导器或分区。不覆盖 XNU2LOG.BIN。
检查日志 NABU: CPU compositor page 0/1/2 applied、GOP swap，
以及 WindowServer 不再从 Metal 设备检查退出和实际画面。
'''
    if windowserver_log:
        readme += '''
本版增加 WindowServer 退出入口和异常现场日志：NABU-WS。
记录 PC/LR/SP/FP、ESR/FAR、指令、共享缓存 slide、通用寄存器和最多 12 层用户栈。
task-terminate 记录终止发起者 PID 与内核调用点；外部终止不读取目标用户内存。
新开关 nabu-ws-log=1 直接写 ESP 日志所用 RAM 环；每编译单元每次启动最多 32 次事件。
同时打开 WindowServer stdout/stderr。为让 journal 线程继续提交，关闭首异常强制停机；
这不改变原异常投递和原进程退出处理。本版增强诊断，不代表已修复桌面。
'''
    if session_repair:
        delivery['implemented'] += ['19.2MHz normalized microtime', 'build UTC calendar floor',
            'identity resolver and login IPC journal', 'named Ethernet interface rematch',
            'root union identity and firmlink semantics']
        delivery['unresolved'] = ['device validation of resolver replies and login client connection',
            'device validation of TimeSync clock and CPU compositor first frame']
        readme = readme.replace('OD 首次内部连接超时尚未修复；显示后端注册不代表 WindowServer 已完成桌面呈现。',
            'OD 已注册后的查询超时需要本轮复测；显示后端注册不代表 WindowServer 已完成桌面呈现。')
        readme = readme.replace('启动配置沿用归档设备的日志 token，仅添加 native-gop-display=1。',
            '启动配置沿用归档设备的日志 token，并启用 CPU compositor 和 session/退出记录。')
        readme += '''
本轮 1–5 修复：19.2 MHz 微秒换算、构建 UTC 日历下限、OD/登录 IPC 记录、
原生命名后的 Ethernet/TimeSync 重新匹配、union 身份和 firmlink 路径语义。
NABU-SESSION 记录 kauth-submit/getwork/result/wake 与登录 IPC；
NABU-DISPLAY 报告实际 VBL/队列/提交/显示计数；WS 退出观察也覆盖 loginwindow。
新增 nabu-session-log=1，stdout/stderr 选择 boot-filesystems；日志 token 保持不变。
这些是代码与构建结果；仍需本机确认登录连接、第一帧和 Dock/Finder。
'''
    (output / 'README.txt').write_text(readme)
    (output / 'delivery.json').write_text(json.dumps(delivery, indent=2) + '\n')
    archive = output.with_suffix('.zip')
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for name in names:
            bundle.write(payload / name, 'EFI/UniXNU/Payloads/' + name)
        bundle.write(config_path, 'EFI/UniXNU/unixnu.toml')
        for name in ('README.txt', 'delivery.json', 'prelink-report.json'):
            bundle.write(output / name, name)
        for name in ('read_xnu2log.py', 'nabu_log_volume.py'):
            bundle.write(ROOT / 'scripts' / name, 'tools/' + name)
        if windowserver_log:
            for name in ('symbolicate_nabu_ws_log.py', 'native_macho.py'):
                bundle.write(ROOT / 'scripts' / name, 'tools/' + name)
    delivery.update(package=str(archive), package_sha256=sha(archive), package_bytes=archive.stat().st_size)
    (output / 'delivery.json').write_text(json.dumps(delivery, indent=2) + '\n')
    print(json.dumps(delivery, indent=2), flush=True)
    return delivery


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('artifact', 'metadata', 'output', 'installed-toml', 'identity-file'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--cpu-compositor', action='store_true')
    parser.add_argument('--windowserver-log', action='store_true')
    parser.add_argument('--session-repair', action='store_true')
    args = parser.parse_args()
    package(args.artifact, args.metadata, args.output, args.installed_toml, args.identity_file,
            cpu_compositor=args.cpu_compositor, windowserver_log=args.windowserver_log,
            session_repair=args.session_repair)
