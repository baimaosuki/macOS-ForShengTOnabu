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
from native_devtree import configure_native_text_state
from prepare_nabu_boot_addon import prepare as prepare_addon

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def package(artifact, metadata, output, installed_toml, identity_file, *, cpu_compositor=False,
            windowserver_log=False, session_repair=False, cpu_desktop_repair=False, cpu_count=1,
            minimal_boot=False):
    metadata = json.loads(metadata.read_text())
    if metadata['conclusion'] != 'success':
        raise ValueError('requires a successful native CI build')
    build = json.loads((artifact / 'build.json').read_text())
    native = artifact / 'kernel.development.nabu'
    if cpu_desktop_repair and not (cpu_compositor and session_repair and windowserver_log):
        raise ValueError('CPU desktop repair requires the compositor and session policies')
    minimal_policy = None
    if minimal_boot:
        minimal_policy = (build.get('session_repair') or {}).get('minimal_boot') or {}
        if minimal_policy.get('header_sha256') != sha(ROOT / 'platform/nabu/macos26/desktop/minimal_boot.h') or minimal_policy.get('patch_sha256') != sha(ROOT / 'platform/nabu/macos26/desktop/minimal-boot.patch'):
            raise ValueError('minimal boot policy differs from compiled kernel')
        if '_nabu_minimal_boot_parked' not in symbols(native):
            raise ValueError('native kernel omitted minimal boot gate')
    timesync_policy = None
    if cpu_desktop_repair:
        from inspect_nabu_timesync_identity import inspect
        timesync_policy = inspect(ROOT / 'out/nabu-bootkc/kernelcache.release.mac13g.nabu.kc')['native_bringup_policy']
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
        if repair.get('session_ipc_sha256') != sha(ROOT / 'platform/nabu/macos26/log/session_ipc.h'):
            raise ValueError('session IPC peer observer differs from the compiled kernel')
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
        trace_boot_processes=True, native_gop_display=True, platform_uuid=platform_uuid,
        cpu_count=cpu_count, kernel_build=artifact / 'build.json')
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
    if cpu_desktop_repair or cpu_count != 1 or minimal_boot:
        matches = list(re.finditer(r'(?m)^(\s*args\s*=\s*")([^"\n]*)("\s*)$', text))
        if len(matches) != 1:
            raise ValueError('expected one installed args line')
        match = matches[0]
        arguments = match.group(2)
        if cpu_count != 1:
            from native_smp import configure
            arguments = configure(build, cpu_count, True, arguments, board='nabu')['boot_args']
        if cpu_desktop_repair:
            wanted = {'-timesync_bringup', 'nabu-session-probe=1', 'nabu-od-wait-log=1'}
            keys = {a.split('=', 1)[0] for a in wanted}
            arguments = ' '.join([a for a in arguments.split() if a.split('=', 1)[0] not in keys] + sorted(wanted))
        if minimal_boot:
            arguments = ' '.join([a for a in arguments.split() if a.split('=', 1)[0] != 'nabu-minimal-boot'] + ['nabu-minimal-boot=1'])
        if len(arguments.encode()) >= 1000:
            raise ValueError('CPU desktop boot arguments exceed the loader command line budget')
        text = text[:match.start(2)] + arguments + text[match.end(2):]
    config_path = candidate / 'EFI/UniXNU/unixnu.toml'
    config_path.write_text(text)
    final_config = tomllib.loads(text)
    assert final_config['entry'][0]['args'].split().count('native-gop-display=1') == 1
    report = json.loads(report_path.read_text())
    names = ['kernel.macho', 'kernel.macho.adt', 'kernel.macho.trustcache', 'kernel.macho.gpuaddon']
    payload = candidate / 'EFI/UniXNU/Payloads'
    # SHENG disables board sleep until the native suspend/resume path is ready.
    configure_native_text_state(payload / 'kernel.macho.adt', native_disable_sleep=True)
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
        system_sleep_disabled=True, cpu_count=cpu_count,
        cpu_desktop_repair=cpu_desktop_repair, timesync_bringup_policy=timesync_policy,
        minimal_boot=minimal_boot, minimal_boot_policy=minimal_policy,
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
    if cpu_desktop_repair:
        delivery['implemented'] += ['25F71 CPU persistent layer presentation',
                                    'native TimeSync bounded bringup wait',
                                    'directly spawned session stack capture']
        delivery['unresolved'] = ['device verification of CPU layer pixels and completed frames',
                                  'OD cold-start assertion and query latency need device retest',
                                  'native TimeSync clock resource and interactive desktop need device verification']
        readme += '''
本轮 CPU 桌面候选：按 25F71 swap 语义保留图层并由 CPU 呈现，
加入原版 -timesync_bringup，使时钟查找前的无限平台等待改为原生 5 秒等待。
此选项仍要求真实 MAC 派生时钟，不伪造 clock ready。
修复采样脚本仅安装不运行的问题：mount phase 2 直接启动独立 shell，
立即打印 NABU-SESSION-PROBE started，15 秒后开始有限采样。
新增 NABU-OD-WAIT 只记录 OD 实际 timedwait 输入/结果，不改其超时与断言。
旧版本未取得脚本栈，不能声称本轮已修复 OD 冷启动根因或桌面已经可见。
'''
    if cpu_count != 1:
        delivery['implemented'] += [f'{cpu_count} CPU nodes and PSCI/SGI startup configuration',
                                    'NABU Affinity1 reset lookup and original CPU registration completion']
        delivery['unresolved'].append('all secondary CPUs, timers and SGI require device validation')
        readme += f'\n本包启用 cpus={cpu_count} sheng-smp=1，ADT 同时生成 {cpu_count} 个真实 MPIDR CPU 节点。\n副核、timer、SGI 和调度仍待设备验证；主机测试不代表八核已经实机上线。\n'
    readme += '''
设备树 /defaults/sleep-disabled=1：沿用 SHENG board.toml 的禁整机睡眠策略，
避免进入尚未完成的 CPU 睡眠路径。此项无需重编内核，不等于已经修复登录会话。
'''
    if (build.get('session_repair') or {}).get('bulk_progress_patch_sha256'):
        repairs = build['session_repair']
        for name, key in [('bulk-progress.patch', 'bulk_progress_patch_sha256'),
                          ('lifecycle-diagnostics.patch', 'lifecycle_diagnostics_patch_sha256')]:
            if repairs[key] != sha(ROOT / 'platform/nabu/macos26/desktop' / name):
                raise ValueError('functional repair differs from the compiled kernel: ' + name)
        directory_progress = repairs.get('directory_progress_patch_sha256')
        if directory_progress:
            if directory_progress != sha(ROOT / 'platform/nabu/macos26/desktop/directory-progress.patch'):
                raise ValueError('directory progress repair differs from compiled kernel')
            delivery['implemented'].append('reject same-mount union directory fallback for getdirentries64 and getdirentriesattr')
        probe_build = json.loads((artifact / 'boot-shim/build.json').read_text())
        delivery['probe_policy'] = probe_build['probe_policy']
        delivery['implemented'] += [
            'prevent same-mount union bulk fallback from resetting directory enumeration',
            'reserve matching loginwindow IPC completion and final continuation result',
            'direct RAM lifecycle events for SecurityAgent/authd/lsd/launchservicesd']
        delivery['unresolved'] = [
            'device validation of union bulk progress guard',
            'actual LaunchServices mapdb reply and FileCache preparer lock ownership',
            'interactive login desktop and input validation']
        delivery['packaging_script_sha256'] = sha(Path(__file__))
        readme = f"""NABU 25F71 — union directory progress repair candidate

内核 CI {metadata['id']} / {metadata['head_sha']}，cpus={cpu_count}。
已修复：union getattrlistbulk 回到同一 mount 时清零偏移、循环或重复枚举。
修前/修后实际控制体覆盖12条路径。此缺陷尚未证明就是实机 lsd 高 CPU 的原因。
同时修复 loginwindow IPC 独立限流丢失返回；同步和 continuation 均保留最终结果。
SecurityAgent/authd/lsd/launchservicesd 生命周期改为 direct RAM，console 交接后继续记录。

默认 probe v7 只采原始栈，不运行符号 sample。coreservicesd/launchservicesd/lsd
为可选取栈目标，三轮在发现后0/150/300秒；真实x0–x28与PC/LR/FP/SP用于离线核查。
nabu-session-symbols=1 才启用120秒符号sample，已确认会干扰启动，正常验证不添加它。

尚未实机验证已进入桌面；不绕过FileCache锁，不伪造LaunchServices回复或显示完成。
TWRP 挂载现有 ESP 后，先保存旧文件，再替换本包 EFI 下五个对应文件并 sync/reboot。
不需要重刷 Data、root、引导器或分区，不覆盖 XNU2LOG.BIN；日志token沿用现有分配。
"""
        if directory_progress:
            readme += '''
本轮新增公共 lookup_traverse_union 进展检查，覆盖 libc opendir 的 getdirentries64
以及 getdirentriesattr；保持正常下层目录、原始错误和 vnode 清理。
LS 实机第二轮栈已落在 CFIterateDirectory/opendir；尚未捕获具体目录及同mount回退命中。
此包修复可复现的重复枚举缺陷，尚未实机验证解决 lsd 等待或黑屏。
'''
    if minimal_boot:
        readme = '''NABU 25F71 / 8-core Minimal GUI Boot test ESP

本包启用 nabu-minimal-boot=1。放行 minimal_boot.h 中的指定 xpcproxy 服务标签及前缀，
包括 loginwindow.UUID、SecurityAgent 的实例和用户指定的核心服务命名空间。
补回 sandboxd、iconservices 及 CoreServices 实际等待的 diskarbitrationd；
完整放行 tccd、lsd、iconservicesd/iconservicesagent 的系统/用户任务与动态代理标签。
放行 powerd 实际同步依赖的 com.apple.powerexperienced（resourceusage 服务）；
GX 两次现场栈均停在此同步 XPC；该任务此前被门控挂起。实机效果仍待验证。
cloudd、apsd、mds、trustd、syspolicyd、endpointsecurityd、keybagd 等继续拦截。
其他 xpcproxy 在进入用户态前施加内核 task hold：保持 PID 存活，避免普通退出触发
KeepAlive 重拉；正常 task resume / SIGCONT 不释放该独立内核 hold。
放行和挂起各有独立的64次日志额度；放行记录不等于服务已就绪。
这不是按进程名杀服务；直接执行且未经过 xpcproxy 的服务不在该门控范围。
被屏蔽服务的客户端仍可能等待或超时；本包用于实机验证最小服务集，未验证桌面。

仅替换 ESP 中本ZIP携带的 unixnu.toml 和四个 Payload 文件；先备份对应旧文件。
不覆盖 Data、root_image.bin、引导器或 XNU2LOG.BIN。保留八核、CPU显示和原日志token。
恢复旧ESP五文件可回退；或将 boot-arg 改为 nabu-minimal-boot=0 关闭此门控。

''' + readme
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
    parser.add_argument('--cpu-desktop-repair', action='store_true')
    parser.add_argument('--minimal-boot', action='store_true')
    parser.add_argument('--cpus', type=int, choices=(1, 2, 4, 8), default=1)
    args = parser.parse_args()
    package(args.artifact, args.metadata, args.output, args.installed_toml, args.identity_file,
            cpu_compositor=args.cpu_compositor, windowserver_log=args.windowserver_log,
            session_repair=args.session_repair, cpu_desktop_repair=args.cpu_desktop_repair,
            cpu_count=args.cpus, minimal_boot=args.minimal_boot)
