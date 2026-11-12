# SPDX-License-Identifier: GPL-3.0-or-later
"""Admit explicit SMP observations with the paired native PAC bootstrap."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIELDS = {
    'running_secondaries': ('_armcpu_smp_running', 4),
    'steps': ('_armcpu_smp_step', 32),
    'failures': ('_armcpu_smp_fail', 32),
    'psci': ('_armcpu_smp_psci_rc', 32),
    'requested_affinities': ('_armcpu_smp_mpidr', 32),
    'seen_affinities': ('_armcpu_smp_seen_mpidr', 32),
    'timer_interrupts': ('_armcpu_core_timer', 32),
    'ipi_interrupts': ('_armcpu_core_sgi', 32),
}


def audit_nabu_source(kernel_source: Path, cpu_source: Path) -> dict:
    """Record the real non-Apple reset/CPU registration prerequisites."""
    reset = (kernel_source / 'osfmk/arm64/start.s').read_text()
    cpu = cpu_source.read_text()
    machine = (kernel_source / 'osfmk/arm64/machine_routines.c').read_text()
    if ('#if HAS_CLUSTER || defined(NABU)' not in reset
            or 'and\t\tx0, x15, #0xFFFF' not in reset):
        raise ValueError('NABU reset lookup must preserve MPIDR Affinity1')
    if ('processor_boot(processor);' not in machine
            or cpu.count('  ml_cpu_init_completed ();') != 1
            or cpu.count('  registrationReturned ();') != 2
            or 'registered != expected' not in cpu
            or 'IOService::publishResource (gIOAllCPUInitializedKey, kOSBooleanTrue);' not in cpu
            or 'armcpu_smp_psci_rc[cpu]' not in cpu):
        raise ValueError('NABU requires the original processor boot and CPU completion barrier')
    return dict(reset_affinity_bits=16, registration_completion='ml_cpu_init_completed',
                completion_after_all_registrations=True, psci_failure_blocks_completion=True,
                secondary_start='ml_processor_register -> processor_boot -> PSCI CPU_ON',
                psci_method='smc', virtual_timer_intid=19, ipi='ICC_SGI1R_EL1',
                hardware_qualified=False)


def configure(build: dict, count: int, graphics: bool, boot_args: str, *, board: str = 'sheng') -> dict:
    if board not in ('sheng', 'nabu'):
        raise ValueError(f'Unsupported board for SMP configuration: {board}')
    if count not in (1, 2, 4, 8) or [item for item in boot_args.split() if item.startswith('cpus=')] != ['cpus=1']:
        raise ValueError('Requires an explicit supported CPU count and the single-CPU baseline')
    if 'armcpu-spi-ipi=1' in boot_args.split() and board == 'nabu':
        raise ValueError('nabu SM8150 GICv3 requires SGI IPI (armcpu-self-ipi=1) and forbids SPI IPI')
    if count == 1:
        return dict(board=board, cpu_count=1, boot_args=boot_args, graphics=graphics)
    if graphics:
        from native_framebuffer import require_smp_owner
        require_smp_owner(build)
    patch = ROOT / 'platform/sheng/macos26/patches/machine-cpu-smp-bootstrap.patch'
    asm_patch = ROOT / 'platform/sheng/macos26/patches/machine-cpu-inline-asm.patch'
    if board == 'nabu':
        components = ROOT / 'platform/nabu/macos26/patches/components'
        patch = components / 'machine-cpu-smp-bootstrap.patch'
        asm_patch = components / 'machine-cpu-inline-asm.patch'
        timer_patch = components / 'nabu-armcpu-timer-intid19.patch'
        completion_patch = components / 'machine-cpu-init-completed.patch'
        source = build.get('nabu_smp_source', {})
        if (build.get('cpu_smp_patch_sha256') != hashlib.sha256(patch.read_bytes()).hexdigest()
                or build.get('cpu_inline_asm_patch_sha256') != hashlib.sha256(asm_patch.read_bytes()).hexdigest()
                or build.get('cpu_timer_intid_patch_sha256') != hashlib.sha256(timer_patch.read_bytes()).hexdigest()
                or build.get('cpu_init_completed_patch_sha256') != hashlib.sha256(completion_patch.read_bytes()).hexdigest()
                or not build.get('cpu_sgi_instructions')
                or source.get('reset_affinity_bits') != 16
                or source.get('completion_after_all_registrations') is not True
                or source.get('virtual_timer_intid') != 19
                or source.get('psci_method') != 'smc'):
            raise ValueError('nabu SMP requires compiled SGI, SMC PSCI, timer INTID 19, Affinity1 reset lookup and CPU registration completion')
        arguments = boot_args.split()
        arguments[arguments.index('cpus=1')] = f'cpus={count}'
        for flag in ('sheng-smp=1', 'armcpu-self-ipi=1'):
            if flag not in arguments:
                arguments.append(flag)
        return dict(board='nabu', soc='SM8150', cpu_count=count,
                    affinities=[f'0x{i * 0x100:03x}' for i in range(count)],
                    psci_method='smc', virtual_timer_intid=19,
                    boot_args=' '.join(arguments), graphics=graphics,
                    ipi='GICv3 SGI (ICC_SGI1R_EL1)', spi_ipi_supported=False,
                    hardware_qualified=False)
    bootstrap = build.get('smp_bootstrap', {})
    expected = {name: hashlib.sha256((ROOT / 'platform/sheng/macos26/pac' / name).read_bytes()).hexdigest()
                for name in ('smp_keys.h', 'smp_keys_storage.h')}
    pac_patch = ROOT / 'platform/sheng/macos26/pac/0006-native-smp-key-bootstrap.patch'
    if (build.get('architecture') != 'arm64e' or bootstrap.get('source_sha256') != expected
            or bootstrap.get('reset_affinity_bits') != 16
            or build.get('cpu_smp_patch_sha256') != hashlib.sha256(patch.read_bytes()).hexdigest()
            or build.get('cpu_inline_asm_patch_sha256') != hashlib.sha256(asm_patch.read_bytes()).hexdigest()
            or not build.get('cpu_sgi_instructions')
            or build.get('patches', {}).get(str(pac_patch.relative_to(ROOT)))
            != hashlib.sha256(pac_patch.read_bytes()).hexdigest()):
        raise ValueError('Requires the compiled native secondary PAC and reset-affinity fixes')
    arguments = boot_args.split()
    arguments[arguments.index('cpus=1')] = f'cpus={count}'
    arguments += ['sheng-smp=1', 'armcpu-self-ipi=1']
    return dict(cpu_count=count, boot_args=' '.join(arguments), graphics=graphics,
                ipi='GIC SGI - QEMU', hardware_qualified=False)


def capture(memory: dict, addresses: dict, count: int) -> dict:
    import struct
    result = {}
    for field, (symbol, size) in FIELDS.items():
        raw = bytes.fromhex(memory[f'{addresses[symbol]:x}:{size:x}'])
        if len(raw) != size:
            raise ValueError('Incomplete native SMP counters')
        values = list(struct.unpack('<' + ('i' if field == 'psci' else 'I') * (size // 4), raw))
        result[field] = values[:count] if size > 4 else values[0]
    result['secondary_init_complete'] = (result['running_secondaries'] == count - 1
        and all(result['steps'][i] == 7 and result['failures'][i] == 0
                and result['psci'][i] == 0
                and result['requested_affinities'][i] & 0xffff == result['seen_affinities'][i] & 0xffff
                for i in range(1, count)))
    result['all_cores_received_timer'] = all(value > 0 for value in result['timer_interrupts'])
    result['all_cores_received_ipi'] = all(value > 0 for value in result['ipi_interrupts'])
    result['all_cores_received_timer_and_ipi'] = (
        result['all_cores_received_timer'] and result['all_cores_received_ipi'])
    result['scheduler_migration_qualified'] = False
    return result
