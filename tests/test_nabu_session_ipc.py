"""Compile the production observer and apply its patch to real prepared XNU."""
from pathlib import Path
import os
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_real_ipc_observer_locks_and_continuations(tmp_path):
    compiler = shutil.which('clang') or shutil.which('cc')
    executable = tmp_path / 'session-ipc'
    subprocess.run([compiler, '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    '-I', str(ROOT / 'platform/nabu/macos26/log'),
                    str(ROOT / 'tests/nabu_session_ipc.c'), '-o', str(executable)], check=True)
    subprocess.run([str(executable)], check=True)


def test_peer_patch_applies_to_prepared_xnu(tmp_path):
    source = Path(os.environ.get('NABU_SESSION_IPC_SOURCE',
        ROOT / 'out/nabu-cpu-desktop-repair/source-check/source'))
    original = source / 'osfmk/ipc/mach_msg.c'
    if not original.exists():
        pytest.skip('requires prepared 25F71 XNU source')
    destination = tmp_path / 'osfmk/ipc'
    destination.mkdir(parents=True)
    shutil.copyfile(original, destination / 'mach_msg.c')
    patch = ROOT / 'platform/nabu/macos26/patches/zzzzzzzz-nabu-session-peer.patch'
    if '#include "nabu_session_ipc.h"' in original.read_text():
        subprocess.run(['patch', '--batch', '--reverse', '-p1', '-d', str(tmp_path),
                        '-i', str(patch)], check=True)
    subprocess.run(['patch', '--batch', '--forward', '-p1', '-d', str(tmp_path),
                    '-i', str(patch)], check=True)
    text = (destination / 'mach_msg.c').read_text()
    assert text.index('#include <ipc/ipc_space.h>') < text.index('#include "nabu_session_ipc.h"')
    assert 'nabu_session_ipc("' not in text
    assert '(uint32_t)(mr_lp >> 32), (uint32_t)(dc_rn >> 32)' in text
    assert text.index('nabu_session_ipc_wait(&nabu_call)') < text.index(
        'mr = mach_msg_trap_receive(msg_addr, aux_addr, option64,')
    assert 'nabu_session_ipc_receive_result(options, mr)' in text
    # Verify the exact native helper/lifetime assumptions in this source version.
    port_source = (source / 'osfmk/ipc/ipc_port.c').read_text()
    assert 'ipc_port_get_receiver_task_locked(ipc_port_t port, task_t *task)' in port_source
    assert 'return waitq_lock_try(&port->ip_waitq);' in port_source
    assert 'return (int)os_atomic_load(&task->task_tokens->tc_pid, relaxed);' in (
        source / 'osfmk/kern/task.c').read_text()
