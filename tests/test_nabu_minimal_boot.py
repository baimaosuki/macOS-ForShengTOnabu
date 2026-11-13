"""Exercise the production policy and its integration into real XNU exec."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_production_minimal_boot_policy(tmp_path):
    compiler = shutil.which('clang') or shutil.which('cc')
    exe = tmp_path / 'minimal-boot'
    subprocess.run([compiler, '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
        '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
        '-I', str(ROOT / 'platform/nabu/macos26/desktop'),
        str(ROOT / 'tests/fixtures/nabu_minimal_boot.c'), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True, timeout=10)


def test_real_exec_patch_and_native_kernel_hold_contract(tmp_path):
    source = ROOT / 'out/nabu-ls-readdir-audit/source-prepare/source'
    original = (source / 'bsd/kern/kern_exec.c').read_text()
    destination = tmp_path / 'bsd/kern/kern_exec.c'
    destination.parent.mkdir(parents=True)
    destination.write_text(original)
    subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-d', str(tmp_path), '-i',
        str(ROOT / 'platform/nabu/macos26/desktop/minimal-boot.patch')], check=True)
    text = destination.read_text()
    assert text.index('#include "nabu_minimal_boot.h"') < text.index('exec_mach_imgact(')
    begin = text.index('exec_mach_imgact(')
    gate = text.index('error = nabu_minimal_boot_apply(p, imgp, task);', begin)
    assert text.index('set_proc_name(imgp, p);', begin) < gate
    assert gate < text.index('POSIX_SPAWN_START_SUSPENDED', gate)
    assert 'if (via_proxy && nabu_minimal_boot_enabled()) return false;' in text
    # Verify the actual native resume cannot consume a separate kernel hold.
    task = (source / 'osfmk/kern/task.c').read_text()
    hold = task[task.index('task_hold_and_wait(\n'):]
    hold = hold[:hold.index('\n/*')]
    assert 'task_hold_locked(task)' in hold and 'user_stop_count' not in hold
    release = task[task.index('release_task_hold(\n'):task.index('task_suspend_internal(task_t task)')]
    assert 'if (--task->user_stop_count == 0)' in release
    assert 'task_release_locked(task)' in release
    assert 'if (thread != self)' in task[task.index('task_wait_locked(\n'):]
