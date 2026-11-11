#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile the pinned macOS 26 source machine layer in an isolated SHENG tree."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BOARD = os.environ.get('MACOS26_BOARD', 'sheng')
PLATFORM = ROOT / f'platform/{BOARD}/macos26'
MACHINE = BOARD.upper()
KERNEL_NAME = f'kernel.development.{BOARD}'
BASE_PLATFORM = ROOT / 'platform/sheng/macos26'
OUT = ROOT / ('out/macos26-xnu' if BOARD == 'sheng' else f'out/macos26-xnu-{BOARD}')
ARCH = 'arm64'
DESKTOP_CFLAGS = ''
PATCHES = [
    BASE_PLATFORM / 'patches/0001-native-sheng-target.patch',
    BASE_PLATFORM / 'patches/0002-non-apple-machine-paths.patch',
    BASE_PLATFORM / 'patches/0003-staged-kernel-sdk.patch',
    BASE_PLATFORM / 'patches/0004-system-abi-identifiers.patch',
    BASE_PLATFORM / 'patches/0005-native-cache-and-unused-diagnostics.patch',
    BASE_PLATFORM / 'patches/0006-upstream-write-only-diagnostics.patch',
    BASE_PLATFORM / 'patches/0007-native-macos-page-layout.patch',
    BASE_PLATFORM / 'patches/0008-native-source-selection.patch',
    BASE_PLATFORM / 'patches/0009-normal-kernel-archive.patch',
    BASE_PLATFORM / 'patches/0010-native-kernel-link.patch',
    BASE_PLATFORM / 'patches/0011-native-platform-personalities.patch',
    BASE_PLATFORM / 'patches/0012-native-platform-services.patch',
    BASE_PLATFORM / 'patches/0013-native-retained-log.patch',
    BASE_PLATFORM / 'patches/0014-native-link-map.patch',
    BASE_PLATFORM / 'patches/0015-native-physmap-padding.patch',
    BASE_PLATFORM / 'patches/0016-native-first-exception-log.patch',
    BASE_PLATFORM / 'patches/0017-native-corecrypto-bootstrap.patch',
    BASE_PLATFORM / 'patches/0018-native-panic-console-mirror.patch',
    BASE_PLATFORM / 'patches/0019-native-console-fallback-mirror.patch',
    BASE_PLATFORM / 'patches/0020-native-crypto-module-view.patch',
    BASE_PLATFORM / 'patches/0021-native-panic-console-flush.patch',
    BASE_PLATFORM / 'patches/0022-native-early-framebuffer-console.patch',
    ROOT / 'UniXNU/patches/xnu/apple-only-cpu-tunables.patch',
    ROOT / 'UniXNU/patches/xnu/native-4k-bootstrap-capacity.patch',
    ROOT / 'UniXNU/patches/xnu/native-dt-panic-region.patch',
    ROOT / 'UniXNU/patches/xnu/local-availability.patch',
    ROOT / 'UniXNU/patches/xnu/cxx-flag-dependencies.patch',
    BASE_PLATFORM / 'patches/0023-native-early-exception-screen.patch',
    BASE_PLATFORM / 'patches/0024-native-bootstrap-sync-screen.patch',
    BASE_PLATFORM / 'patches/0025-native-mmu-transition-vectors.patch',
    BASE_PLATFORM / 'patches/0026-native-skip-early-debug-unlock.patch',
    BASE_PLATFORM / 'patches/0027-native-bootstrap-stage-strip.patch',
    BASE_PLATFORM / 'patches/0028-native-post-mmu-stage-strip.patch',
    BASE_PLATFORM / 'patches/0029-native-c-bootstrap-stage-strip.patch',
    BASE_PLATFORM / 'patches/0030-native-vm-console-stage-strip.patch',
    BASE_PLATFORM / 'patches/0031-native-retained-log-panel.patch',
    BASE_PLATFORM / 'patches/0032-native-panel-bounded-conversions.patch',
    BASE_PLATFORM / 'patches/0033-native-mapped-status-line.patch',
    BASE_PLATFORM / 'patches/0034-native-status-panel-layout.patch',
    BASE_PLATFORM / 'patches/0035-native-preconsole-live-log.patch',
    BASE_PLATFORM / 'patches/0036-native-live-log-rebind.patch',
    BASE_PLATFORM / 'patches/0037-native-panic-io-zero.patch',
    BASE_PLATFORM / 'patches/0038-native-security-prelink.patch',
    BASE_PLATFORM / 'patches/0039-native-security-init-build.patch',
    BASE_PLATFORM / 'patches/0040-native-prelink-structor-signing.patch',
    BASE_PLATFORM / 'patches/0041-native-prelink-index-bounds.patch',
    BASE_PLATFORM / 'patches/0042-native-security-entry-pac.patch',
    BASE_PLATFORM / 'patches/0043-native-corecrypto-single-init.patch',
    BASE_PLATFORM / 'patches/0044-native-platform-compatible-match.patch',
    BASE_PLATFORM / 'patches/0045-native-fatal-exception-panel.patch',
    BASE_PLATFORM / 'patches/0046-native-platform-personality-config.patch',
    BASE_PLATFORM / 'patches/0047-native-storage-vm-mappings.patch',
    BASE_PLATFORM / 'patches/0048-qemu-development-dyld-ignition.patch',
    BASE_PLATFORM / 'patches/0049-native-boot-prelink-identity.patch',
    BASE_PLATFORM / 'patches/0050-native-platform-uuid.patch',
    BASE_PLATFORM / 'patches/0051-native-multiline-panic-panel.patch',
    BASE_PLATFORM / 'patches/0052-native-independent-panic-surface.patch',
    BASE_PLATFORM / 'patches/0053-development-dyld-commpage.patch',
    BASE_PLATFORM / 'patches/0054-development-recovery-ui-pages.patch',
    BASE_PLATFORM / 'patches/0055-native-process-output-log.patch',
    BASE_PLATFORM / 'patches/0056-native-boot-progress.patch',
    BASE_PLATFORM / 'patches/0057-native-vector-output-log.patch',
    BASE_PLATFORM / 'patches/0058-native-progress-clock-ready.patch',
    BASE_PLATFORM / 'patches/0059-native-progress-scheduler-header.patch',
    BASE_PLATFORM / 'patches/0060-native-progress-thread-noreturn.patch',
    ROOT / 'UniXNU/patches/xnu/zzx-pmap-sync-64bit-pa.patch',
    BASE_PLATFORM / 'patches/0061-native-windowserver-exception-stop.patch',
    BASE_PLATFORM / 'patches/0062-native-windowserver-alignment-map.patch',
    BASE_PLATFORM / 'patches/0063-native-managed-ram-writecombine.patch',
    BASE_PLATFORM / 'patches/0064-native-language-chooser-trace.patch',
    BASE_PLATFORM / 'patches/0065-native-panic-framebuffer-owner.patch',
    BASE_PLATFORM / 'patches/0066-native-smp-progress.patch',
]
if BOARD != 'sheng':
    PATCHES = PATCHES + sorted((PLATFORM / 'patches').glob('*.patch'))
def board_patch(name: str) -> Path:
    for candidate in (PLATFORM / 'patches/components' / name, PLATFORM / 'patches' / name):
        if candidate.is_file():
            return candidate
    return PLATFORM / 'patches' / name


MACHINE_PATCH = board_patch('machine-driver-declarations.patch')
CPU_PATCH = board_patch('machine-cpu-registration.patch')
CPU_INTERRUPT_PATCH = board_patch('machine-cpu-interrupt-order.patch')
CPU_SMP_PATCH = board_patch('machine-cpu-smp-bootstrap.patch')
CPU_ASM_PATCH = board_patch('machine-cpu-inline-asm.patch')
CPU_GIC_PATCH = board_patch('machine-cpu-gic-group.patch')
CPU_TIMER_PATCH = board_patch('nabu-armcpu-timer-intid19.patch')


# Preserve diagnostics for exported Apple source/tool-generated code on Clang 27.
# Machine-layer translation units restore these categories to errors below.
UPSTREAM_WARNINGS = (
    'unused-but-set-variable', 'gnu-folding-constant', 'implicit-int-float-conversion',
    'pointer-to-int-cast', 'void-pointer-to-int-cast', 'void-pointer-to-enum-cast',
)


def worktree_copy(destination: Path) -> Path:
    """Materialize a private copy of the pinned tree even when it has no .git."""
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(ROOT / 'sources/macos26/xnu', destination)
    if not (destination / '.git').exists():
        subprocess.run(['git', '-C', str(destination), 'init', '-q'], check=True)
        subprocess.run(['git', '-C', str(destination), 'add', '-A'], check=True,
                       stdout=subprocess.DEVNULL)
        subprocess.run(['git', '-C', str(destination), '-c', 'user.email=build@local',
                        '-c', 'user.name=build', 'commit', '-qm', 'pinned tree'], check=True,
                       stdout=subprocess.DEVNULL)
    return destination


def apply_series(tree: Path, patch: Path) -> None:
    """Apply one patch, tolerating the fuzz the reference series requires."""
    strict = subprocess.run(['git', '-C', str(tree), 'apply', '--whitespace=nowarn', str(patch)],
                            capture_output=True)
    if strict.returncode == 0:
        return
    if patch.name in ('integrated.patch', 'late-storage-start.patch') and patch.parent.name == 'desktop':
        raise ValueError('desktop filesystem integration preimage changed; refuse fuzzy placement: '
                         + strict.stderr.decode(errors='replace')[:500])
    fuzzed = subprocess.run(['patch', '-p1', '-N', '-s', '--fuzz=3', '-r', '-', '-i', str(patch)],
                            cwd=str(tree), capture_output=True)
    if fuzzed.returncode != 0:
        raise ValueError(f'{patch}: cannot apply ({fuzzed.stderr.decode(errors="replace").strip()[:200]})')


def pinned_identity(directory: Path) -> str:
    """Return the pinned commit, accepting a tarball checkout without .git."""
    pin = directory / '.source-pin'
    if pin.is_file():
        return json.loads(pin.read_text())['commit']
    return capture('git', '-C', str(directory), 'rev-parse', 'HEAD')


def sha(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def capture(*command: str, cwd: Path = ROOT) -> str:
    return subprocess.check_output(command, cwd=cwd, text=True).strip()


def stage_file(source: Path, destination: Path) -> None:
    """Keep cached dependency timestamps when the staged content is unchanged."""
    source, destination = Path(source), Path(destination)
    if not destination.is_file() or source.read_bytes() != destination.read_bytes():
        shutil.copy2(source, destination)


def audit_desktop_source(source: Path) -> dict:
    """Check the actual patched callsites, not just patch application status."""
    text = (source / 'bsd/kern/kern_exec.c').read_text()
    startup = text[text.index('void\nload_init_program(proc_t p)'):]
    ordered = (startup.index('mach_vm_allocate_kernel(map, &scratch_addr')
               < startup.index('native_owner_cache_prepare(p, (user_addr_t)scratch_addr')
               < startup.index('native_owner_macos_data_prepare(p, (user_addr_t)scratch_addr')
               < startup.index('load_init_program_at_path(p, (user_addr_t)scratch_addr'))
    if not ordered or 'NABU desktop: init scratch allocation failed' not in startup:
        raise ValueError('desktop owner mount is not after checked user scratch allocation and before launchd')
    if not (text.index('native_owner_macos_app_cryptex(proc_t p')
            < text.index('native_owner_macos_data_prepare(proc_t p')):
        raise ValueError('desktop App cryptex helper must be declared before the Data caller')
    data = text[text.index('native_owner_macos_data_prepare(proc_t p'):text.index('static int\nload_init_program_at_path')]
    for required in ('NATIVE_OWNER_DATA_MOUNT_POINT', 'native_owner_data_firmlinks',
                     'native_owner_macos_vm_volume(p, scratch)',
                     'native_owner_macos_cryptex_view(p, scratch)', 'native_owner_macos_app_cryptex(p, scratch)',
                     'error = native_owner_macos_boot_shims(p, scratch)'):
        if required not in data:
            raise ValueError('desktop Data path omitted: ' + required)
    if not (data.index('native_owner_data_firmlinks(vfs_context_current())')
            < data.index('native_owner_macos_vm_volume(p, scratch)')
            < data.index('native_owner_macos_cryptex_view(p, scratch)')):
        raise ValueError('desktop VM firmlink must follow the writable Data mount and firmlinks')
    vm = text[text.index('native_owner_macos_vm_volume(proc_t p'):text.index('#ifdef XNU_OWNER_APP_VOLUME_UUID',
        text.index('native_owner_macos_vm_volume(proc_t p'))]
    if any(needle not in vm for needle in ('"/System/Volumes/VM"', '"/private/var/vm"',
           'vnode_setasfirmlink(source, target)', 'native_owner_vm_volume_status = error')):
        raise ValueError('desktop VM path must expose and report the actual owner Data firmlink')
    shim = text[text.index('native_owner_macos_boot_shims(proc_t p'):text.index(
        'native_owner_macos_data_prepare(proc_t p')]
    if any(needle not in shim for needle in ('"apfs-boot-shim"', 'native_owner_vm_volume_status',
           'native_gpu_addon_files < 1 || native_gpu_addon_failed || native_gpu_addon_error',
           'apfs.fs/Contents/Resources/apfs_checkseal', 'return native_owner_boot_shim_status;')):
        raise ValueError('desktop APFS boot shim must validate VM, addon installation and mount visibility')
    watchdog = text[text.index('native_owner_watchdog_file('):text.index(
        'native_owner_macos_data_prepare(proc_t p')]
    for needle in ('"disable-watchdogd"', 'enabled != 1',
                   '/private/var/db/com.apple.xpc.launchd/disabled.plist',
                   '/private/var/db/com.apple.xpc.launchd/disabled.migrated',
                   '<key>com.apple.watchdogd</key><true/>',
                   'error = EEXIST;', 'O_CREAT | O_EXCL | O_NOFOLLOW',
                   'va.va_uid != 0 || va.va_gid != 0', '(va.va_mode & 07777) != 0644',
                   '(va.va_mode & 07777) != 0755', 'VNOP_FSYNC(vp, MNT_WAIT, ctx)',
                   'memcmp(data, readback, size)', 'native_owner_watchdog_policy_status = error'):
        if needle not in watchdog:
            raise ValueError('desktop watchdog launch policy omitted: ' + needle)
    if not (data.index('error = mount(p, &request, &result)')
            < data.index('native_owner_data_firmlinks(vfs_context_current())')
            < data.index('error = native_owner_macos_boot_shims(p, scratch)')
            < data.index('if (!error) error = native_owner_macos_watchdog_policy(p, scratch)')):
        raise ValueError('watchdog launch policy must follow checked Data mount and boot shims')
    if not watchdog.index('native_owner_watchdog_file(policy, contents, sizeof(contents) - 1, ctx)') < watchdog.index(
            'native_owner_watchdog_file(marker, (const uint8_t *)"", 0, ctx)'):
        raise ValueError('watchdog migration marker must follow verified policy publication')
    spawn_gate = text[text.index('native_owner_watchdog_spawn_denied(proc_t parent'):text.index(
        'posix_spawn(proc_t ap, struct posix_spawn_args *uap, int32_t *retval)')]
    for needle in ('proc_getpid(parent) != 1', '"disable-watchdogd"', 'enabled != 1',
                   '"/usr/libexec/watchdogd"', 'copyinstr(path, name, sizeof(name), &copied)',
                   'copied != sizeof(target)', 'memcmp(name, target, sizeof(target))',
                   'native_owner_watchdog_spawn_blocked', '"/usr/libexec/xpcproxy"',
                   '"com.apple.watchdogd"', 'IS_64BIT_PROCESS(parent)',
                   'copyin(argv + sizeof(user64_addr_t), &job_address, sizeof(job_address))',
                   'copied != sizeof(label)', 'memcmp(job, label, sizeof(label))',
                   'native_owner_watchdog_proxy_blocked'):
        if needle not in spawn_gate:
            raise ValueError('incomplete explicit watchdog spawn gate: ' + needle)
    spawn = text[text.index('posix_spawn(proc_t ap, struct posix_spawn_args *uap, int32_t *retval)'):]
    if not (spawn.index('if (native_owner_watchdog_spawn_denied(ap, uap->path, uap->argv)) return ENOENT;')
            < spawn.index('__spawn_data = kalloc_type') < spawn.index('p->p_crash_behavior = crash_behavior;')):
        raise ValueError('watchdog spawn gate must return before allocation/child crash policy')
    if 'native_owner_publish_boot_manifest(root_device, XNU_OWNER_OS_VOLUME_UUID,' not in text:
        raise ValueError('desktop boot manifest must use the paired candidate OS UUID')
    lifecycle = text[text.index('struct nabu_life_record {'):text.index('void\nset_proc_name(')]
    for needle in ('"nabu-boot-proc"', 'enabled == 1', 'vn_getpath(imgp->ip_vp',
                   'imgp->ip_startargv', 'imgp->ip_endargv', 'proc_uniqueid(p)',
                   'r->ppid = proc_ppid(p)', 'r->gpid = proc_ppid(parent)',
                   '"com.apple.filesystems.fskitd"', '"com.apple.WindowServer"',
                   '"com.apple.diskarbitrationd"', '"com.apple.security.cryptexd"',
                   '5000000000ULL', 'nabu_life_buckets[NABU_LIFE_KINDS][3]',
                   'b->suppressed++', 'absolutetime_to_nanoseconds(mach_absolute_time()',
                   'p->p_xstat, p->p_exit_reason', 'nabu_lifecycle_trace_events'):
        if needle not in lifecycle:
            raise ValueError('incomplete bounded boot process diagnostics: ' + needle)
    activation = text[text.index('p->p_acflag &= ~AFORK;'):]
    if not activation.index('set_proc_name(imgp, p)') < activation.index('nabu_lifecycle_exec(p, imgp)'):
        raise ValueError('boot process trace must follow final image identity')
    exit_source = (source / 'bsd/kern/kern_exit.c').read_text()
    final_exit = exit_source[exit_source.index('pid = proc_getpid(p);\n\texitval = p->p_xstat;'):]
    if not final_exit.index('nabu_lifecycle_exit(p)') < final_exit.index('proc_refdrain(p)'):
        raise ValueError('boot process exit trace must precede proc reference drain')
    stderr = (source / 'bsd/kern/sheng_process_output.h').read_text()
    for needle in ('"boot-filesystems"', '"nabu-boot-proc"',
                   'nabu_boot_filesystem_output == 1', 'nabu_output_buckets[6]',
                   'initial < 8', '5000000000ULL', 'suppressed++',
                   '"mount_apfs"', '"fskitd"', '"WindowServer"',
                   '"diskarbitrationd"', '"cryptexd"'):
        if needle not in stderr:
            raise ValueError('incomplete bounded selected boot stderr: ' + needle)
    root = (source / 'bsd/kern/bsd_init.c').read_text()
    if 'native_verified_block_root_authorized(device)' not in root or 'if (autherr && !owner_authorized)' not in root:
        raise ValueError('desktop rootauth must call the actual owner authorization bridge')
    if (root.count('nabu_source_storage_start();') != 1 or
            not root.index('vfsinit();') < root.index('nabu_source_storage_start();') < root.index('\n\tbsd_autoconf();')):
        raise ValueError('desktop storage must start once after vfsinit and before IOBSD publication')
    return dict(scratch_allocation_before_mount=True, owner_query_in_rootauth=True,
                cache_before_data_before_launchd=True, data_firmlinks=True,
                app_called_from_data=True, root_uuid_from_paired_build=True,
                storage_start_after_vfsinit=True, vm_path_on_owner_data=True,
                checked_apfs_boot_shim=True, watchdog_policy_explicit_opt_in=True,
                watchdog_policy_before_launchd=True, watchdog_policy_exclusive_create=True,
                watchdog_policy_root_owned_readback=True, watchdog_policy_boolean_true=True,
                watchdog_spawn_gate_before_child=True, watchdog_spawn_gate_pid1_exact_path=True,
                watchdog_spawn_gate_xpcproxy_exact_label=True,
                boot_process_trace_final_activation=True, boot_process_trace_recurring_bounded=True,
                boot_process_trace_committed_exit=True, boot_stderr_trace_recurring_bounded=True)


def audit_nabu_arm64e_source(source: Path) -> dict:
    """Refuse an admission-only arm64e build lacking its no-PAuth loaders."""
    required = {
        'bsd/dev/arm/kern_machdep.c': ('#if NABU_ARM64E_COMPAT', 'CPU_SUBTYPE_ARM64_PTR_AUTH_CURRENT_VERSION'),
        'bsd/kern/kern_exec.c': ('#if __has_feature(ptrauth_calls) || NABU_ARM64E_COMPAT', 'ptrauth_disabled=1'),
        'osfmk/vm/vm_dyld_pager.c': ('return fixupPageAuth64(', 'return fixupCachePageAuth64(', '*signedAddr = unsignedAddr;'),
        'osfmk/arm64/nabu_v82_emul.c': ('if (nabu_nopac_opcode(insn))', 'if (!nabu_nopac_step('),
        'osfmk/arm64/nabu_nopac.h': ('nabu_nopac_authenticate(',
            'if (user && data) plain |= value & UINT64_C(0xff00000000000000);'),
        'osfmk/arm64/nabu_el0_sysreg.h': ('0xd53b42a0U', '0xd51b42a0U',
            '0xd503405fU', '0xd50330ffU', 'if (!barrier) return false;', 'barrier();'),
    }
    for name, needles in required.items():
        text = (source / name).read_text()
        if any(needle not in text for needle in needles):
            raise ValueError('incomplete NABU arm64e compatibility: ' + name)
    shared = (source / 'osfmk/vm/vm_shared_region.c').read_text()
    if shared.count('#elif NABU_ARM64E_COMPAT') != 2:
        raise ValueError('NABU requires both v3 and v5 authenticated cache pointer stores')
    arm64_makefile = (source / 'osfmk/arm64/Makefile').read_text()
    exported = arm64_makefile.split('EXPORT_MD_LIST =', 1)[1].split('\n\n', 1)[0]
    if any(name not in exported.split() for name in ('nabu_arm64e_compat.h', 'nabu_nopac.h', 'nabu_el0_sysreg.h')):
        raise ValueError('NABU arm64e headers must be exported for BSD component imports')
    emulator = (source / 'osfmk/arm64/nabu_v82_emul.c').read_text()
    if any(needle not in emulator for needle in ('if (from_user) {',
           'nabu_el0_sysreg_step(insn, &value, &write, nabu_v82_speculation_barrier)',
           '__asm__ volatile("dsb sy\\n\\tisb" ::: "memory")')):
        raise ValueError('NABU EL0 DIT/SB compatibility must use the real speculation barrier')
    return dict(software_abi=True, host_cpu_subtype_unchanged=True,
        accepted_arm64e_abi_version=0, keys_off_apple_parameter=True,
        shared_cache_v3_v5_plain_rebases=True, dyld_pager_arm64e_decoders=True,
        source_pac_emulator=True, data_auth_preserves_top_byte=True,
        el0_dit_effectively_disabled=True, el0_sb_dsb_sy_isb=True,
        binary_or_signature_rewriting=False, device_qualified=False)


def save(record: dict) -> None:
    (OUT / 'build.json').write_text(json.dumps(record, indent=2) + '\n')


def run(command: list, name: str, record: dict, cwd: Path = ROOT, env=None) -> None:
    command = [str(item) for item in command]
    log = OUT / 'logs' / (name + '.log')
    print(f'Running - {name}', flush=True)
    with log.open('w') as stream:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT)
    record.setdefault('commands', []).append(dict(command=command, cwd=str(cwd),
                                                 log=str(log.relative_to(OUT)), returncode=result.returncode))
    save(record)
    if result.returncode:
        lines = log.read_text(errors='replace').splitlines()
        first = next((index for index, line in enumerate(lines)
                      if 'error:' in line or 'Undefined symbols' in line or 'fatal error' in line), None)
        if first is not None:
            print('\n'.join(lines[max(0, first - 3):first + 18]), file=sys.stderr)
        print('\n'.join(lines)[-5000:], file=sys.stderr)
        raise subprocess.CalledProcessError(result.returncode, command)


def refresh_comment_only_patches(source: Path, applied: dict) -> None:
    """Refresh recorded patch comments while retaining unchanged source timestamps."""
    changed = [p for p in PATCHES if str(p.relative_to(ROOT)) in applied
               and sha(p) != applied[str(p.relative_to(ROOT))]]
    if not changed:
        return
    tokens = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|/\*.*?\*/|//[^\n]*', re.S)

    def code(text):
        return tokens.sub(lambda m: ''.join('\n' for c in m[0] if c == '\n')
                          if m[0].startswith(('/*', '//')) else m[0], text)

    with tempfile.TemporaryDirectory(prefix='comment-refresh-', dir=OUT) as temporary:
        clean = Path(temporary) / 'source'
        worktree_copy(clean)
        for patch in PATCHES:
            if str(patch.relative_to(ROOT)) in applied:
                apply_series(clean, patch)
        paths = subprocess.check_output(['git', '-C', clean, 'ls-files', '--modified',
                                         '--others', '--exclude-standard', '-z']).decode().split('\0')
        updates = []
        for name in filter(None, paths):
            actual, desired = source / name, clean / name
            if (not actual.is_file() or not desired.is_file()
                    or code(actual.read_text()) != code(desired.read_text())):
                raise ValueError('previously applied patch changed beyond comments: ' + name)
            if actual.read_bytes() != desired.read_bytes():
                updates.append((desired, actual))
        for desired, actual in updates:
            stage_file(desired, actual)
    applied.update({str(p.relative_to(ROOT)): sha(p) for p in changed})


def prepare() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'logs').mkdir(exist_ok=True)
    lock = json.loads((ROOT / 'sources.lock.json').read_text())['sources']
    upstream = next(entry for entry in lock if entry['path'] == 'UniXNU')
    if pinned_identity(ROOT / 'UniXNU') != upstream['commit']:
        raise ValueError('the hardware implementation must come from the pinned UniXNU revision')
    sources = json.loads((ROOT / 'config/macos26-sources.json').read_text())['sources']
    identities = {}
    for source in sources:
        directory = ROOT / source['path']
        actual = pinned_identity(directory)
        dirty = (directory / '.git').is_dir() and capture(
            'git', '-C', str(directory), 'status', '--porcelain')
        if actual != source['commit'] or dirty:
            raise ValueError(f'{source["path"]}: requires the clean pinned macOS 26 source')
        identities[source['path']] = actual
    baseline = json.loads((ROOT / f'config/{BOARD}-baseline.json').read_text())
    board_defconfig = ROOT / f'config/{BOARD}_defconfig'
    if sha(board_defconfig) != baseline['defconfig_sha256']:
        raise ValueError(f'{BOARD}: the recorded board configuration has changed')
    upstream_defconfig = baseline.get('defconfig_upstream')
    if upstream_defconfig:
        upstream_path = ROOT / upstream_defconfig
        if not upstream_path.exists():
            raise ValueError(f'{BOARD}: pinned upstream defconfig is missing: {upstream_defconfig}')
    source = OUT / 'source'
    if not source.exists():
        worktree_copy(source)
    if pinned_identity(source) != identities['sources/macos26/xnu']:
        raise ValueError('refuse to reset a different existing source worktree')
    previous = OUT / 'build.json'
    applied = {}
    if previous.exists():
        previous_record = json.loads(previous.read_text())
        current_diff = subprocess.check_output(['git', '-C', source, 'diff', 'HEAD', '--binary'])
        if hashlib.sha256(current_diff).hexdigest() != previous_record['source_diff_sha256']:
            raise ValueError('isolated source has unrecorded changes; preserve it for inspection')
        applied = previous_record['patches']
        refresh_comment_only_patches(source, applied)
    for patch in PATCHES:
        identity = str(patch.relative_to(ROOT))
        if identity in applied:
            if sha(patch) != applied[identity]:
                raise ValueError(f'previously applied patch changed: {identity}')
            continue
        reverse = subprocess.run(['git', '-C', source, 'apply', '--reverse', '--check', patch],
                                 capture_output=True)
        if reverse.returncode:
            apply_series(source, patch)
    io_zero = PLATFORM / 'log/io_zero.h'
    stage_file(io_zero, source / 'pexpert/arm/pe_sheng_io_zero.h')
    framebuffer_owner = PLATFORM / 'log/framebuffer_owner.h'
    stage_file(framebuffer_owner, source / 'pexpert/arm/pe_sheng_framebuffer.h')
    retained_log = PLATFORM / 'log/retained_log.h'
    stage_file(retained_log, source / 'pexpert/arm/pe_sheng_log.h')
    smp_log = PLATFORM / 'log/smp_log.h'
    stage_file(smp_log, source / 'pexpert/arm/pe_sheng_smp_log.h')
    boot_progress = PLATFORM / 'log/boot_progress.h'
    boot_progress_kernel = PLATFORM / 'log/boot_progress_kernel.h'
    stage_file(boot_progress, source / 'pexpert/arm/pe_sheng_boot_progress.h')
    stage_file(boot_progress_kernel, source / 'pexpert/arm/pe_sheng_boot_progress_kernel.h')
    process_output = PLATFORM / 'log/process_output.h'
    stage_file(process_output, source / 'bsd/kern/sheng_process_output.h')
    panel_console = PLATFORM / 'log/panel_console.h'
    stage_file(panel_console, source / 'pexpert/arm/pe_sheng_panel.h')
    panel_font = source / 'osfmk/console/iso_font.c'
    stage_file(panel_font, source / 'pexpert/arm/pe_sheng_font.h')
    from native_panic_qr import render as render_panic_qr
    qr_encoder, qr_contract = render_panic_qr(ROOT)
    qr_generated = OUT / 'pe_sheng_qr_encoder.h'
    if not qr_generated.exists() or qr_generated.read_text() != qr_encoder:
        qr_generated.write_text(qr_encoder)
    stage_file(qr_generated, source / 'pexpert/arm/pe_sheng_qr_encoder.h')
    panic_qr = PLATFORM / 'log/panic_qr.h'
    qr_runtime = PLATFORM / 'log/qr/runtime.h'
    stage_file(panic_qr, source / 'pexpert/arm/pe_sheng_panic_qr.h')
    stage_file(qr_runtime, source / 'pexpert/arm/pe_sheng_qr_runtime.h')
    stage_file(PLATFORM / 'log/qr/qrcodegen.h', source / 'pexpert/arm/qrcodegen.h')
    early_exception = PLATFORM / 'log/early_exception.h'
    stage_file(early_exception, source / 'osfmk/arm64/sheng_early_exception.h')
    bootstrap_sync = PLATFORM / 'log/bootstrap_sync.h'
    stage_file(bootstrap_sync, source / 'osfmk/arm64/sheng_bootstrap_sync.h')
    bootstrap_trace = PLATFORM / 'log/bootstrap_trace.h'
    stage_file(bootstrap_trace, source / 'osfmk/arm64/sheng_bootstrap_trace.h')
    c_trace = PLATFORM / 'log/c_trace.h'
    stage_file(c_trace, source / 'osfmk/arm64/sheng_c_trace.h')
    vm_trace = PLATFORM / 'log/vm_trace.h'
    stage_file(vm_trace, source / 'osfmk/arm64/sheng_vm_trace.h')
    crypto_image = PLATFORM / 'crypto/native_image.h'
    stage_file(crypto_image, source / 'osfmk/prng/sheng_crypto_image.h')
    from native_recovery_ui import render as render_recovery_ui
    recovery_ui = PLATFORM / 'recovery/ui_policy.h'
    recovery_ui_kernel = PLATFORM / 'recovery/ui_policy_kernel.h'
    recovery_ui_manifest = ROOT / 'config/macos26-recovery-ui-pages.json'
    recovery_ui_generated = OUT / 'sheng_recovery_ui_pages.h'
    rendered = render_recovery_ui(json.loads(recovery_ui_manifest.read_text()))
    if not recovery_ui_generated.exists() or recovery_ui_generated.read_text() != rendered:
        recovery_ui_generated.write_text(rendered)
    stage_file(recovery_ui, source / 'osfmk/vm/sheng_recovery_ui.h')
    stage_file(recovery_ui_kernel, source / 'osfmk/vm/sheng_recovery_ui_kernel.h')
    stage_file(recovery_ui_generated, source / 'osfmk/vm/sheng_recovery_ui_pages.h')
    from native_language_trace import render as render_language_trace
    language_manifest = ROOT / 'config/macos26-language-trace.json'
    language_generated = OUT / 'sheng_language_trace_pages.h'
    rendered = render_language_trace(json.loads(language_manifest.read_text()))
    if not language_generated.exists() or language_generated.read_text() != rendered:
        language_generated.write_text(rendered)
    language_headers = ('language_trace.h', 'language_trace_kernel.h', 'language_pages_kernel.h')
    for name in language_headers:
        stage_file(PLATFORM / 'recovery' / name, source / ('osfmk/vm/sheng_' + name))
    stage_file(language_generated, source / 'osfmk/vm/sheng_language_trace_pages.h')
    smp_headers = ('smp_keys.h', 'smp_keys_storage.h')
    for name in smp_headers:
        stage_file(PLATFORM / 'pac' / name, source / ('osfmk/arm64/sheng_' + name))
    subprocess.run(['git', '-C', source, 'add', '-N', 'osfmk/prng/sheng_crypto_image.h', 'pexpert/pexpert/arm64/SHENG.h',
                    'pexpert/arm/pe_sheng_log.h', 'osfmk/arm64/sheng_early_exception.h',
                    'osfmk/arm64/sheng_bootstrap_sync.h', 'osfmk/arm64/sheng_bootstrap_trace.h',
                    'osfmk/arm64/sheng_c_trace.h', 'osfmk/arm64/sheng_vm_trace.h',
                    'pexpert/arm/pe_sheng_panel.h', 'pexpert/arm/pe_sheng_font.h',
                    'pexpert/arm/pe_sheng_io_zero.h', 'osfmk/vm/sheng_recovery_ui.h',
                    'osfmk/vm/sheng_recovery_ui_kernel.h', 'osfmk/vm/sheng_recovery_ui_pages.h',
                    'osfmk/vm/sheng_language_trace.h', 'osfmk/vm/sheng_language_trace_kernel.h',
                    'osfmk/vm/sheng_language_pages_kernel.h', 'osfmk/vm/sheng_language_trace_pages.h',
                    'osfmk/arm64/sheng_smp_keys.h', 'osfmk/arm64/sheng_smp_keys_storage.h',
                    'bsd/kern/sheng_process_output.h', 'pexpert/arm/pe_sheng_boot_progress.h',
                    'pexpert/arm/pe_sheng_boot_progress_kernel.h', 'pexpert/arm/pe_sheng_panic_qr.h',
                    'pexpert/arm/pe_sheng_framebuffer.h', 'pexpert/arm/pe_sheng_smp_log.h',
                    'pexpert/arm/pe_sheng_qr_encoder.h', 'pexpert/arm/pe_sheng_qr_runtime.h',
                    'pexpert/arm/qrcodegen.h'], check=True)
    record = dict(xnu='12377.121.6', darwin='25.5.0', build='25F71', machine=MACHINE,
                  deployment='macOS 26.5', sources=identities,
                  architecture=ARCH, native_el1_pac=ARCH == 'arm64e',
                  el0_pac_lifecycle_qualified=False,
                  pac_key_origin='unchanged EL1 firmware handoff banks' if ARCH == 'arm64e' else None,
                  hardware_source_commit=upstream['commit'],
                  patches={str(p.relative_to(ROOT)): sha(p) for p in PATCHES},
                  io_zero_source_sha256=sha(io_zero),
                  retained_log_source_sha256=sha(retained_log),
                  framebuffer_owner=dict(version=1, source_sha256=sha(framebuffer_owner),
                      panic_bypasses_writer=True, late_panic_redraw=True, max_cpus=8),
                  smp_log=dict(source_sha256=sha(smp_log), interval_seconds=30, per_core=True),
                  boot_progress=dict(default_off=True, boot_argument='sheng-boot-progress=1',
                      stage_count=10, supported_cpus=[1, 2, 4, 8], model_sha256=sha(boot_progress),
                      kernel_sha256=sha(boot_progress_kernel), stalled_is_not_deadlock=True,
                      repeated_events_advance=False, periodic_summary_seconds=30,
                      renderer_handoff='first native GOP frame', runtime_qualified=False),
                  process_output_log=dict(default_off=True, boot_argument='sheng-stderr-log',
                      stderr_mode=1, stdout_and_stderr_mode=2, process_filter='sheng-stderr-name',
                      max_bytes_per_write=159, max_iovecs=16, vector_writes=True,
                      source_sha256=sha(process_output),
                      records_attempted_output=True, runtime_qualified=False),
                  panic_qr=dict(contract=qr_contract, source_sha256=sha(panic_qr),
                      runtime_sha256=sha(qr_runtime), encoder_sha256=sha(qr_generated),
                      heap_allocation=False, disk_writes=False, network=False,
                      registers='panic hook, separate from earlier EL1 exception',
                      hardware_qualified=False),
                  panel_console_source_sha256=sha(panel_console),
                  panel_font_source_sha256=sha(panel_font),
                  early_exception_source_sha256=sha(early_exception),
                  bootstrap_sync_source_sha256=sha(bootstrap_sync),
                  bootstrap_trace_source_sha256=sha(bootstrap_trace),
                  bootstrap_post_mmu_stages=True,
                  bootstrap_c_trace_source_sha256=sha(c_trace),
                  bootstrap_vm_trace_source_sha256=sha(vm_trace),
                  crypto_image_source_sha256=sha(crypto_image),
                  recovery_ui_policy=dict(default_off=True, boot_argument='sheng-recovery-ui=1',
                      page_count=len(json.loads(recovery_ui_manifest.read_text())['pages']),
                      manifest_sha256=sha(recovery_ui_manifest), engine_sha256=sha(recovery_ui),
                      kernel_hook_sha256=sha(recovery_ui_kernel), generated_sha256=sha(recovery_ui_generated),
                      original_signature_validation_required=True, disk_writes=False,
                      runtime_qualified=False, hardware_admitted=False),
                  language_trace=dict(default_off=True, boot_argument='sheng-language-trace=1',
                      requires_recovery_ui=True, page_count=2, point_count=5,
                      manifest_sha256=sha(language_manifest), generated_sha256=sha(language_generated),
                      source_sha256={name: sha(PLATFORM / 'recovery' / name)
                                     for name in language_headers},
                      disk_writes=False, samples_per_point=32,
                      runtime_qualified=False, hardware_qualified=False),
                  smp_bootstrap=dict(kernel_keys='cold-boot bank shared before secondary C entry'
                                        if ARCH == 'arm64e' else 'not used by the ARM64 target',
                      reset_affinity_bits=16 if ARCH == 'arm64e' else 8, key_material_logged=False,
                      source_sha256={name: sha(PLATFORM / 'pac' / name)
                                     for name in smp_headers},
                      hardware_qualified=False, graphics_smp_qualified=False),
                  upstream_board_defconfig_sha256=baseline['defconfig_sha256'],
                  upstream_sheng_archives=baseline['archives'],
                  purpose='Native machine-layer source compilation; not a bootable kernel',
                  source_archive_built=False, platform_objects_built=False, runnable_kernel=False,
                  stage='source-compilation', full_upstream_sheng_config=False,
                  kernel_page_bytes=16384, virtual_address_bits=47,
                  upstream_warning_only_categories=list(UPSTREAM_WARNINGS),
                  upstream_binary_archives_reused=False, boot_image_generated=False,
                  original_component_abi_qualified=False, hardware_executed=False,
                  source_diff_sha256=hashlib.sha256(subprocess.check_output(
                      ['git', '-C', source, 'diff', 'HEAD', '--binary'])).hexdigest())
    if BOARD == 'nabu' and ARCH == 'arm64':
        record['arm64e_userspace_compatibility'] = audit_nabu_arm64e_source(source)
    if DESKTOP_CFLAGS:
        record['desktop_source_audit'] = audit_desktop_source(source)
        record['boot_process_trace'] = dict(default_off=True, boot_argument='nabu-boot-proc=1',
            marker='nabu_lifecycle_trace_events', observational_only=True,
            targets=['mount', 'mount_apfs', 'fskitd', 'WindowServer', 'diskarbitrationd', 'cryptexd'],
            final_exec_activation=True, tracked_processes=64, argv_bytes=512, argv_count=24,
            first_events_per_bucket=4, recurring_interval_seconds=5,
            separate_exec_success_exit_failure_exit_buckets=True,
            stderr_selector='boot-filesystems', stderr_first_events_per_target=8,
            stderr_recurring_interval_seconds=5,
            monotonic_nanoseconds=True, hardware_qualified=False)
        record['watchdog_launch_policy'] = dict(default_off=True, boot_argument='disable-watchdogd=1',
            job='com.apple.watchdogd', policy='/private/var/db/com.apple.xpc.launchd/disabled.plist',
            migration_marker='/private/var/db/com.apple.xpc.launchd/disabled.migrated',
            existing_policy_replaced=False, hardware_watchdog_available=False,
            crash_and_panic_policy_modified=False, hardware_qualified=False)
        record['watchdog_launch_policy']['spawn_gate'] = dict(marker='native_owner_watchdog_spawn_blocked',
            parent_pid=1, executable='/usr/libexec/watchdogd', errno='ENOENT',
            proxy='/usr/libexec/xpcproxy', proxy_argument_index=1,
            proxy_label='com.apple.watchdogd', proxy_marker='native_owner_watchdog_proxy_blocked',
            before_child_creation=True, policy_kind='explicit bring-up bypass',
            launchd_job_remains_imported=True, retry_possible=True)
    save(record)
    return record


def audit_object(path: Path) -> dict:
    data = path.read_bytes()
    if len(data) < 32:
        raise ValueError(f'{path.name}: truncated Mach-O object')
    magic, cpu, subtype, kind = struct.unpack_from('<4I', data)
    if (magic, cpu, subtype & 0xff, kind) != (0xfeedfacf, 0x100000c, 2 if ARCH == 'arm64e' else 0, 1):
        raise ValueError(f'{path.name}: expected a real {ARCH} Mach-O object')
    cursor = 32
    deployment = None
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        if cursor + 8 > len(data):
            raise ValueError('truncated Mach-O object command')
        command, length = struct.unpack_from('<II', data, cursor)
        if length < 8 or cursor + length > len(data):
            raise ValueError('invalid Mach-O object command')
        if command == 0x32:
            if length < 24:
                raise ValueError('truncated deployment command')
            deployment = struct.unpack_from('<II', data, cursor + 8)
        cursor += length
    if deployment != (1, 0x1a0500):
        raise ValueError(f'{path.name}: deployment differs from macOS 26.5: {deployment}')
    return dict(sha256=sha(path), bytes=len(data), deployment='macOS 26.5', architecture=ARCH)


def compile_sources(args, record: dict) -> None:
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise ValueError('compile on the ARM64 macOS runner; Linux supports --prepare-only')
    record['toolchain'] = {name: capture(*command) for name, command in {
        'xcode': ('xcodebuild', '-version'), 'clang': ('xcrun', 'clang', '--version'),
        'sdk': ('xcrun', '--sdk', 'macosx', '--show-sdk-version'),
    }.items()}
    if not record['toolchain']['xcode'].startswith('Xcode 27') or record['toolchain']['sdk'] != '27.0':
        if not os.environ.get('MACOS26_ALLOW_SDK_DRIFT'):
            raise ValueError('requires the recorded Xcode 27 / SDK 27.0 build tools')
        record['toolchain_sdk_drift'] = True
        print('WARNING: building with drifted SDK', record['toolchain']['sdk'], flush=True)
    kdk_report = json.loads((ROOT / 'out/kdk-verification.json').read_text())
    config = json.loads((ROOT / 'config/macos26.json').read_text())
    if kdk_report['build'] != config['build'] or not kdk_report['trusted_apple_package']:
        raise ValueError('requires the signed, paired macOS 26 KDK')
    for board, spec in config['kernels'].items():
        identity = kdk_report['kernels'][board]
        if identity['kernel_uuid'] != spec['uuid'] or not identity['paired_dwarf_uuid_verified']:
            raise ValueError(f'{board}: unpaired KDK identity')
    expected_kernel = args.kdk_root / 'System/Library/Kernels/kernel.release.t8103'
    if config['kernels']['j313ap']['uuid'] not in capture('xcrun', 'dwarfdump', '--uuid', str(expected_kernel)):
        raise ValueError('selected KDK directory differs from the verified report')
    expanded = args.kdk_root.resolve().parent.parent
    sdk_payload = expanded / 'KDK_SDK.pkg/Payload'
    sdk_headers = kdk_report.get('sdk_headers', {})
    required = 'System/Library/Frameworks/Kernel.framework/Versions/A/PrivateHeaders/platform/CoreEntitlements/V2/API.h'
    if required not in sdk_headers:
        raise ValueError('verified KDK SDK payload lacks the required CoreEntitlements V2 header')
    for relative, digest in sdk_headers.items():
        path = sdk_payload / relative
        if not path.resolve().is_relative_to(sdk_payload.resolve()) or sha(path) != digest:
            raise ValueError(f'verified KDK SDK header changed: {relative}')
    record['kdk_verification_sha256'] = sha(ROOT / 'out/kdk-verification.json')
    record['kdk_sdk_headers'] = sdk_headers
    save(record)
    env = dict(os.environ)
    source = OUT / 'source'
    sdk = OUT / 'kernel-sdk'
    sdk.mkdir(exist_ok=True)
    for relative in sdk_headers:
        destination = sdk / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage_file(sdk_payload / relative, destination)
    env['XNU_AVAILABILITY_PL'] = str(sdk / 'usr/local/libexec/availability.pl')
    run(['make', '-C', ROOT / 'sources/macos26/AvailabilityVersions', 'install',
         f'DSTROOT={sdk}', f'OBJROOT={OUT / "availability"}', f'SYMROOT={OUT / "availability"}',
         f'CMAKE={shutil.which("cmake")}', f'NINJA={shutil.which("ninja")}'], 'availability', record, env=env)
    for relative in ('include', 'private'):
        shutil.copytree(ROOT / 'sources/macos26/libplatform' / relative,
                        sdk / 'usr/local/include', dirs_exist_ok=True, copy_function=stage_file)
    shutil.copytree(ROOT / 'sources/macos26/libplatform/private/platform',
                    sdk / 'System/Library/Frameworks/Kernel.framework/Versions/A/PrivateHeaders/platform',
                    dirs_exist_ok=True, copy_function=stage_file)
    dispatch = sdk / 'usr/local/include/os'
    dispatch.mkdir(parents=True, exist_ok=True)
    for header in sorted((ROOT / 'sources/macos26/libdispatch/os').glob('*.h')):
        result = subprocess.run(['unifdef', '-DKERNEL=1', '-DOS_FIREHOSE_SPI=1',
                                 '-DOS_VOUCHER_ACTIVITY_SPI_TYPES=1',
                                 '-UOS_VOUCHER_ACTIVITY_SPI', str(header)], capture_output=True)
        if result.returncode not in (0, 1):
            raise ValueError(result.stderr.decode(errors='replace'))
        destination = dispatch / header.name
        if not destination.is_file() or destination.read_bytes() != result.stdout:
            destination.write_bytes(result.stdout)
    record['staged_dependency_headers'] = {
        str(path.relative_to(sdk)): sha(path)
        for path in sorted(sdk.rglob('*.h')) if path.is_file()}
    save(record)
    common = ['make', '-C', source, 'SDKROOT=macosx', f'KDKROOT={args.kdk_root}',
              f'TARGET_CONFIGS=DEVELOPMENT ARM64 {MACHINE}', f'ARCH_STRING_FOR_CURRENT_MACHINE_CONFIG={ARCH}',
              'ARM64_LARGE_MEMORY=1', 'ARM64_LINK_ADDR=0xfffffe0007004000',
              'RC_DARWIN_KERNEL_VERSION=25.5.0', 'RC_ProjectSourceVersion=12377.121.6',
              'DEPLOYMENT_TARGET_FLAGS=-mmacosx-version-min=26.5 -DXNU_TARGET_OS_OSX',
              'BUILD_LTO=0', 'BOUND_CHECKS=0', 'BUILD_WERROR=1', f'MAKEJOBS=-j{args.jobs}',
              f'KERNEL_SDKROOT={sdk}',
              f'OBJROOT={OUT / "obj"}', f'SYMROOT={OUT / "sym"}', f'DSTROOT={sdk}']
    run(common + ['installhdrs'], 'install-headers', record, env=env)
    # Archive the ordinary kernel translation units without substituting an
    # Apple SoC support library or producing an executable with unresolved ABI.
    failures = []
    try:
        run(common + ['-k', 'build', 'BUILD_STATIC_LINK=1', 'BUILD_XNU_LIBRARY=1',
                      'CFLAGS_KERNEL=-mkernel', 'CXXFLAGS_KERNEL=-fapple-kext',
                      # SFLAGS_GEN is built from OTHER_CFLAGS, so this is the only
                      # knob that reaches start.s/locore.s without editing a patch.
                      'OTHER_CFLAGS=-DSHENG=1',
                      'CFLAGS_EXTRA=-DXNU_NATIVE_ARCH_TIMER=1 -DXNU_DT_PANIC_REGION=1 -DSHENG=1 '
                      '-Xclang -target-feature -Xclang -sha3 -Xclang -target-feature -Xclang -sm4 ' +
                      DESKTOP_CFLAGS + ' '.join('-Wno-error=' + warning for warning in UPSTREAM_WARNINGS)],
            'source-archive', record, env=env)
    except subprocess.CalledProcessError:
        failures.append('source-archive: compilation failed; see logs/source-archive.log')
    else:
        archive = OUT / f'obj/DEVELOPMENT_ARM64_{MACHINE}/libkernel.development.{BOARD}.a'
        if not archive.is_file() or archive.stat().st_size < 1 << 20:
            failures.append('source-archive: no real kernel archive was produced')
        else:
            record.update(source_archive_built=True, source_archive_sha256=sha(archive),
                          source_archive_bytes=archive.stat().st_size)
        save(record)
    recipe = OUT / f'obj/DEVELOPMENT_ARM64_{MACHINE}/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json'
    if not recipe.is_file():
        record['build_failures'] = failures + ['no IOPlatformExpert compilation record was produced']
        save(record)
        raise ValueError('; '.join(record['build_failures']))
    compile_record = json.loads(recipe.read_text().strip().rstrip(','))
    machine = OUT / 'machine'
    for component in ('ArmDTPlatform', 'ArmCpu'):
        destination = machine / component / (component + '.cpp')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / 'UniXNU/drivers' / component / destination.name, destination)
    subprocess.run(['git', 'apply', '--directory=' + str(machine.relative_to(ROOT)),
                    str(MACHINE_PATCH)], cwd=ROOT, check=True)
    record['machine_patch_sha256'] = sha(MACHINE_PATCH)
    subprocess.run(['git', 'apply', '--directory=' + str(machine.relative_to(ROOT)),
                    str(CPU_PATCH)], cwd=ROOT, check=True)
    record['cpu_registration_patch_sha256'] = sha(CPU_PATCH)
    subprocess.run(['git', 'apply', '--directory=' + str(machine.relative_to(ROOT)),
                    str(CPU_INTERRUPT_PATCH)], cwd=ROOT, check=True)
    record['cpu_interrupt_order_patch_sha256'] = sha(CPU_INTERRUPT_PATCH)
    subprocess.run(['git', 'apply', '--directory=' + str(machine.relative_to(ROOT)),
                    str(CPU_SMP_PATCH)], cwd=ROOT, check=True)
    record['cpu_smp_patch_sha256'] = sha(CPU_SMP_PATCH)
    subprocess.run(['git', 'apply', '--directory=' + str(machine.relative_to(ROOT)),
                    str(CPU_ASM_PATCH)], cwd=ROOT, check=True)
    record['cpu_inline_asm_patch_sha256'] = sha(CPU_ASM_PATCH)
    subprocess.run(['git', 'apply', '--directory=' + str(machine.relative_to(ROOT)),
                    str(CPU_GIC_PATCH)], cwd=ROOT, check=True)
    record['cpu_gic_group_patch_sha256'] = sha(CPU_GIC_PATCH)
    subprocess.run(['git', 'apply', '--directory=' + str(machine.relative_to(ROOT)),
                    str(CPU_TIMER_PATCH)], cwd=ROOT, check=True)
    record['cpu_timer_intid_patch_sha256'] = sha(CPU_TIMER_PATCH)
    record['platform_objects'] = {}
    for component in ('ArmDTPlatform', 'ArmCpu'):
        original = ROOT / f'UniXNU/drivers/{component}/{component}.cpp'
        adapted = machine / component / (component + '.cpp')
        destination = OUT / (component + '.o')
        command, index = [], 0
        arguments = compile_record['arguments']
        while index < len(arguments):
            arg = arguments[index]
            if arg == compile_record['file']:
                command.append(str(adapted))
            elif arg in ('-o', '-MF', '-MT', '-MQ', '-MJ'):
                if arg == '-o':
                    command += ['-o', str(destination)]
                index += 1
            elif arg not in ('-MMD', '-MD', '-MP'):
                command.append(arg)
            index += 1
        command += ['-I', str(source / 'osfmk'), '-Werror', '-Wno-unused-parameter']
        command += ['-Werror=' + warning for warning in UPSTREAM_WARNINGS]
        try:
            run(command, component, record, cwd=Path(compile_record['directory']), env=env)
            record['platform_objects'][component] = dict(source_sha256=sha(original),
                adapted_source_sha256=sha(adapted), **audit_object(destination))
            if component == 'ArmCpu':
                from native_cpu_asm import audit_sgi
                record['cpu_sgi_instructions'] = audit_sgi(destination,
                    ('__ZN6ArmCpu9signalCPUEP5IOCPU', '_armcpu_ipi_selftest', '_armcpu_sgi_selfprobe'))
        except (subprocess.CalledProcessError, ValueError) as error:
            failures.append(f'{component}: {error}')
        save(record)
    shim = PLATFORM / 'shim/nabu_link_stubs.cpp'
    if shim.is_file():
        shim_object = OUT / 'nabu_link_stubs.o'
        command, index = [], 0
        arguments = compile_record['arguments']
        while index < len(arguments):
            arg = arguments[index]
            if arg == compile_record['file']:
                command.append(str(shim))
            elif arg in ('-o', '-MF', '-MT', '-MQ', '-MJ'):
                if arg == '-o':
                    command += ['-o', str(shim_object)]
                index += 1
            elif arg not in ('-MMD', '-MD', '-MP'):
                command.append(arg)
            index += 1
        try:
            run(command, 'nabu-link-shim', record, cwd=Path(compile_record['directory']), env=env)
            record['link_shim_object'] = dict(source_sha256=sha(shim), **audit_object(shim_object))
        except (subprocess.CalledProcessError, ValueError) as error:
            failures.append(f'nabu-link-shim: {error}')
        save(record)
    record['platform_objects_built'] = len(record['platform_objects']) == 2
    if args.ufs_root_mode == 'desktop':
        original = PLATFORM / 'storage/NabuRootAuthorization.cpp'
        obj = OUT / 'NabuRootAuthorization.o'
        command, index = [], 0
        arguments = compile_record['arguments']
        while index < len(arguments):
            value = arguments[index]
            if value == compile_record['file']:
                command.append(str(original))
            elif value in ('-o', '-MF', '-MT', '-MQ', '-MJ'):
                if value == '-o':
                    command += ['-o', str(obj)]
                index += 1
            elif value not in ('-MMD', '-MD', '-MP'):
                command.append(value)
            index += 1
        command += ['-Werror']
        # compile_record already carries the same desktop CFLAGS_EXTRA; make
        # the required configuration visible in the audit object itself.
        try:
            run(command, 'nabu-root-authorization', record, cwd=Path(compile_record['directory']), env=env)
            record['root_authorization_object'] = dict(source_sha256=sha(original), **audit_object(obj))
        except (subprocess.CalledProcessError, ValueError) as error:
            failures.append(f'nabu-root-authorization: {error}')
    record['build_failures'] = failures
    save(record)
    if failures:
        raise ValueError('; '.join(failures))
    if args.link_kernel:
        link_error = None
        try:
            link_kernel(args, record, common, sdk, source, env)
        except (subprocess.CalledProcessError, ValueError) as error:
            link_error = error
        if ARCH == 'arm64e' or args.build_ufs_driver:
            from build_macos26_storage import build as build_storage
            if ARCH == 'arm64e':
                build_storage(ROOT, OUT, compile_record, source, run, record, env)
            if args.build_ufs_driver:
                build_storage(ROOT, OUT, compile_record, source, run, record, env,
                              transport='ufs', gop_display=args.build_gop_display,
                              recovery_input=args.build_recovery_input,
                              root_mode=args.ufs_root_mode, desktop_policy=args.ufs_desktop_policy,
                              board=BOARD, defer_desktop_policy=args.ufs_defer_desktop_policy)
            record['storage_kernel_integration'] = 'separate bundles; requires explicit prelink and verification'
            save(record)
        if link_error:
            raise link_error


def link_kernel(args, record, common, sdk, source, env):
    """Use XNU's executable link recipe; missing native contracts remain errors."""
    record.update(stage='executable-link', link_attempted=True, executable_kernel_built=False)
    save(record)
    dispatch = ROOT / 'sources/macos26/libdispatch'
    headers = [dispatch, sdk / 'System/Library/Frameworks/Kernel.framework/Versions/A/PrivateHeaders',
               sdk / 'System/Library/Frameworks/Kernel.framework/Versions/A/Headers',
               sdk / 'usr/local/include', sdk / 'usr/local/include/os',
               sdk / 'usr/local/include/firehose', source / 'EXTERNAL_HEADERS',
               Path(capture('xcrun', '--sdk', 'macosx', '--show-sdk-path')) / 'usr/include']
    run(['xcodebuild', 'install', '-project', dispatch / 'libdispatch.xcodeproj',
         '-target', 'libfirehose_kernel', '-configuration', 'Release', '-sdk', 'macosx',
         '-jobs', str(args.jobs), f'ARCHS={ARCH}', f'VALID_ARCHS={ARCH}', 'ONLY_ACTIVE_ARCH=NO',
         'MACOSX_DEPLOYMENT_TARGET=26.5', 'CODE_SIGNING_ALLOWED=NO',
         'GCC_TREAT_WARNINGS_AS_ERRORS=NO', 'USE_HEADERMAP=NO',
         'HEADER_SEARCH_PATHS=' + ' '.join(str(h) for h in headers),
         f'OBJROOT={OUT / "firehose/obj"}', f'SYMROOT={OUT / "firehose/sym"}',
         f'DSTROOT={sdk}'], 'firehose-kernel', record, env=env)
    firehose = sdk / 'usr/local/lib/kernel/libfirehose_kernel.a'
    alternate = firehose.with_name('liblibfirehose_kernel.a')
    if not firehose.exists() and alternate.exists():
        shutil.copy2(alternate, firehose)
    if not firehose.exists():
        raise ValueError('libdispatch did not install its actual kernel firehose library')
    record['firehose_kernel'] = dict(sha256=sha(firehose), bytes=firehose.stat().st_size)
    # Xcode ships its kernel compiler runtime separately from userspace libSystem.
    resources = Path(capture('xcrun', 'clang', '-print-resource-dir'))
    runtimes = [resources / 'lib/darwin' / name for name in
                ('libclang_rt.cc_kext_osx.a', 'libclang_rt.cc_kext.a')
                if (resources / 'lib/darwin' / name).is_file()]
    if not runtimes:
        raise ValueError('the selected Xcode toolchain has no compiler kernel runtime')
    from build_macos26_crypto import build_provider
    crypto = build_provider(args, record, sdk, source, env)
    crypto_input = '-Wl,-force_load,' + str(crypto) if ARCH == 'arm64e' else str(crypto)
    archives = [crypto_input, str(OUT / 'ArmDTPlatform.o'), str(OUT / 'ArmCpu.o'), str(firehose)]
    if (OUT / 'nabu_link_stubs.o').is_file():
        archives.append(str(OUT / 'nabu_link_stubs.o'))
    if args.ufs_root_mode == 'desktop':
        archives.append(str(OUT / 'NabuRootAuthorization.o'))
    native_link_flags = ('-Wl,-headerpad,0x2000 -Wl,-map,'
                         + str(OUT / (KERNEL_NAME + '.map')))
    run(common + ['build', 'BUILD_STATIC_LINK=1', 'BUILD_XNU_LIBRARY=0',
                  'CFLAGS_KERNEL=-mkernel', 'CXXFLAGS_KERNEL=-fapple-kext',
                  'OTHER_CFLAGS=-DSHENG=1',
                  'CFLAGS_EXTRA=-DXNU_NATIVE_ARCH_TIMER=1 -DXNU_DT_PANIC_REGION=1 -DSHENG=1 '
                      '-Xclang -target-feature -Xclang -sha3 -Xclang -target-feature -Xclang -sm4 ' +
                  DESKTOP_CFLAGS + ' '.join('-Wno-error=' + w for w in UPSTREAM_WARNINGS),
                  'LD_KERNEL_LIBS=' + str(runtimes[0]),
                  'LD_KERNEL_ARCHIVES=' + ' '.join(archives),
                  'NATIVE_KERNEL_LINK_FLAGS=' + native_link_flags,
                  'DO_CTFMERGE=0'], 'kernel-link', record, env=env)
    binary = OUT / f'obj/DEVELOPMENT_ARM64_{MACHINE}/kernel.development.{BOARD}.unstripped.noctf'
    if not binary.exists() or struct.unpack_from('<I', binary.read_bytes(), 12)[0] != 2:
        raise ValueError('the native linker did not produce an executable Mach-O kernel')
    kernel_bytes = binary.read_bytes()
    for personality in (b"'IOClass'         = ArmDTPlatform;",
                        b"'IOClass'         = ArmCpu;",
                        b"'IONameMatch'     = ( 'device-tree', 'xiaomi," + BOARD.encode() + b"' );"):
        if personality not in kernel_bytes:
            raise ValueError('linked kernel omitted a required ' + MACHINE + ' platform personality')
    record['native_platform_personalities_compiled'] = True
    if ARCH == 'arm64e':
        from native_macho import native_chained_fixups
        record['native_pointer_fixups'] = native_chained_fixups(kernel_bytes)
    destination = OUT / KERNEL_NAME
    shutil.copy2(binary, destination)
    if BOARD != 'sheng':
        # Kryo 485 has none of Apple's S3_*_C15_* performance-monitor registers and
        # faults on the first access inside cpc_*/kpc_*/awl_*. Neutralise them in
        # the linked image, before sealing, so the CI artefact is bootable as built.
        run([sys.executable, str(ROOT / 'scripts' / 'nabu_neutralize_apple_sysregs.py'),
             str(destination), '--quiet'], 'kernel-sysreg', record, env=env)
    from seal_macos26_crypto import seal
    record['corecrypto_integrity'] = seal(destination, ARCH)
    run(['xcrun', 'dsymutil', binary, '-o', OUT / (KERNEL_NAME + '.dSYM')],
        'kernel-dsym', record, env=env)
    record.update(executable_kernel_built=True, executable_kernel_sha256=sha(destination),
                  kernel_uuid=capture('xcrun', 'dwarfdump', '--uuid', str(destination)),
                  dsym_uuid=capture('xcrun', 'dwarfdump', '--uuid',
                                   str(OUT / (KERNEL_NAME + '.dSYM'))),
                  link_map_sha256=sha(OUT / (KERNEL_NAME + '.map')),
                  purpose='Native executable link; runtime and userland ABI remain unqualified')
    if args.ufs_root_mode == 'desktop':
        from native_macho import symbols
        actual = symbols(destination)
        required = ('_native_verified_block_root_authorized', '_native_verified_block_manifest_sha384',
                    '_native_verified_block_volume_device', '_nabu_register_root_authenticator',
                    '_nabu_register_ram_seed', '_native_ram_block_volume_seed',
                    '_nabu_register_storage_start', '_nabu_source_storage_start',
                    '_native_owner_root_accepted', '_native_owner_cache_status',
                    '_native_owner_data_stage', '_native_owner_firmlink_count',
                    '_native_owner_app_cryptex_stage', '_nabu_lifecycle_trace_events')
        missing = [name for name in required if name not in actual]
        if missing:
            raise ValueError('linked desktop kernel omitted owner/root/Data code: ' + ', '.join(missing))
        from nabu_storage_contract import audit_kernel
        record['late_storage_kernel'] = audit_kernel(destination.read_bytes(), actual)
        record['desktop_kernel'] = dict(build='25F71', owner_callback_bridges=True,
            required_symbols={name: hex(actual[name]) for name in required},
            systemos_union=True, data_mount='/System/Volumes/Data', firmlinks=True,
            app_cryptex=True, ram_data_volatile=True, runtime_qualified=False)
        record['desktop_kernel']['source_audit'] = audit_desktop_source(source)
        record['desktop_kernel']['configuration_compile_assertions'] = [
            'XNU_TARGET_OS_OSX', 'CONFIG_FIRMLINKS', 'CONFIG_UNION_MOUNTS', 'BINDFS',
            'XNU_OWNER_ROOT_TRUST', 'XNU_OWNER_CACHE_MOUNT', 'XNU_OWNER_DATA_MOUNT', 'XNU_NATIVE_RAM_BLOCK']
    save(record)


def main() -> None:
    global OUT, ARCH, DESKTOP_CFLAGS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--board', default=None,
                        help='board layer to build (default: MACOS26_BOARD or sheng)')
    parser.add_argument('--print-series', action='store_true',
                        help='print resolved board, platform dir and patch series, then exit')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--link-kernel', action='store_true', help='build runtime and attempt the native executable link')
    parser.add_argument('--build-ufs-driver', action='store_true',
                        help='compile the default-off sheng UFS transport with the paired ARM64E ABI')
    parser.add_argument('--ufs-root-mode', choices=('recovery', 'desktop'), default='recovery')
    parser.add_argument('--ufs-desktop-policy', type=Path,
                        help='owner-signed OS/SystemOS/App policies for the paired desktop build')
    parser.add_argument('--ufs-defer-desktop-policy', action='store_true',
                        help='compile desktop driver/runtime objects; final local link must supply signed policies')
    parser.add_argument('--build-gop-display', action='store_true',
                        help='include the default-off paired CPU GOP display in the UFS companion')
    parser.add_argument('--build-recovery-input', action='store_true',
                        help='include the default-off QEMU VirtIO HID input companion')
    parser.add_argument('--arch', choices=('arm64', 'arm64e'), default='arm64')
    parser.add_argument('--kdk-root', type=Path)
    parser.add_argument('-j', '--jobs', type=int, default=4, choices=range(1, 33))
    args = parser.parse_args()
    if getattr(args, 'board', None) and args.board != BOARD:
        os.environ['MACOS26_BOARD'] = args.board
        os.execv(sys.executable, [sys.executable, *sys.argv])
    if getattr(args, 'print_series', False):
        print('BOARD', BOARD)
        print('PLATFORM', PLATFORM)
        print('PATCHES', len(PATCHES))
        for item in PATCHES:
            print(' ', item.relative_to(ROOT), 'OK' if item.exists() else 'MISSING')
        return 0
    if args.build_ufs_driver and not args.link_kernel:
        parser.error('--build-ufs-driver requires --link-kernel')
    if args.ufs_defer_desktop_policy and args.ufs_root_mode != 'desktop':
        parser.error('--ufs-defer-desktop-policy requires --ufs-root-mode desktop')
    if args.ufs_root_mode == 'desktop' and (not args.build_ufs_driver or (not args.ufs_desktop_policy and not args.ufs_defer_desktop_policy) or BOARD != 'nabu'):
        parser.error('desktop root requires --board nabu --build-ufs-driver --ufs-desktop-policy')
    if args.build_gop_display and not args.build_ufs_driver:
        parser.error('--build-gop-display requires --build-ufs-driver')
    if args.build_recovery_input and not args.build_gop_display:
        parser.error('--build-recovery-input requires --build-gop-display')
    ARCH = args.arch
    if args.ufs_root_mode == 'desktop':
        desktop = PLATFORM / 'desktop'
        manifest = json.loads((desktop / 'upstream.json').read_text())
        for item in manifest['patches']:
            path = ROOT / item['path']
            if sha(path) != item['sha256']:
                raise ValueError('pinned desktop filesystem patch changed: ' + item['path'])
        integrated = ROOT / manifest['integrated_patch']
        if sha(integrated) != manifest['integrated_sha256']:
            raise ValueError('desktop integrated filesystem patch changed')
        PATCHES.append(integrated)
        PATCHES.append(desktop / 'late-storage-start.patch')
        DESKTOP_CFLAGS = (' -DXNU_OWNER_ROOT_AUDIT=1 -DXNU_OWNER_ROOT_TRUST=1 '
            '-DXNU_OWNER_CACHE_MOUNT=1 -DXNU_OWNER_CACHE_STARTUP=1 -DXNU_OWNER_BOOT_MANIFEST=1 '
            '-DXNU_OWNER_UNION_SYSTEMOS=1 -DXNU_OWNER_DATA_MOUNT=1 -DXNU_NATIVE_RAM_BLOCK=1 '
            '-DXNU_OWNER_OS_VOLUME_UUID=\\"47719F50-5527-44A3-9F59-8170528D8C14\\" '
            '-DXNU_OWNER_CACHE_VOLUME_UUID=\\"C7DFE43C-0A23-43F5-B3E2-471BEAC4539D\\" '
            '-DXNU_OWNER_APP_VOLUME_UUID=\\"F894E20A-CE25-41F4-ABE2-C00E9E922B83\\" '
            '-DXNU_OWNER_DATA_VOLUME_UUID=\\"0A5EED00-0000-4000-8000-000000000001\\" ')
    if ARCH == 'arm64e':
        OUT = ROOT / ('out/macos26-xnu-arm64e' if BOARD == 'sheng' else f'out/macos26-xnu-{BOARD}-arm64e')
        before = PATCHES.index(PLATFORM / 'patches/0040-native-prelink-structor-signing.patch')
        PATCHES[before:before] = [PLATFORM / 'pac/0001-native-el1-pac-abi.patch',
                                 PLATFORM / 'pac/0002-native-arm64e-chained-startup.patch']
        PATCHES.append(PLATFORM / 'pac/0003-native-user-pac-lifecycle.patch')
        PATCHES.append(PLATFORM / 'pac/0004-native-signing-window-failure.patch')
        PATCHES.append(PLATFORM / 'pac/0005-native-user-a-key-policy.patch')
        PATCHES.append(PLATFORM / 'pac/0006-native-smp-key-bootstrap.patch')
    # Build trees are caller-owned outputs. An explicit new directory allows
    # revising desktop integration without touching an older recorded tree.
    if os.environ.get('MACOS26_OUT'):
        OUT = Path(os.environ['MACOS26_OUT']).expanduser().resolve()
    # Keep helpers on this configured module when invoked as a script.
    sys.modules['build_macos26_xnu'] = sys.modules[__name__]
    record = prepare()
    if args.prepare_only:
        print(json.dumps(record, indent=2))
        return
    if args.kdk_root is None:
        parser.error('--kdk-root is required for compilation')
    try:
        compile_sources(args, record)
    except Exception as error:
        record['error'] = str(error)
        save(record)
        raise


if __name__ == '__main__':
    main()
