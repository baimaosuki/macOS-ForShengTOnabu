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


def test_replay_observed_403_second_loginwindow_call(tmp_path):
    # Use actual retained entry arguments/old channel seq. This reproduces the
    # failure that the older "250 seconds still samples" test never exercised.
    import re
    evidence = ROOT / 'out/nabu-logs-20261113/verified-evi.txt'
    if not evidence.exists():
        pytest.skip('requires retained EVI log')
    entries = []
    for line in evidence.read_text().split('\n'):
        if ('NABU-SESSION pid=168 tid=620 ' in line and 'event=ipc-enter ' in line
                and 'msgid=30176 ' in line):
            entries.append(dict(re.findall(r'(\w+)=([^\s]+)', line)))
    assert len(entries) == 1
    entry = entries[0]
    assert entry['peer_pid'] == '166' and entry['peer_name_at_lookup'] == 'WindowServer'
    compiler = shutil.which('clang') or shutil.which('cc')
    executable = tmp_path / 'evi-call-replay'
    subprocess.run([compiler, '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
        '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
        '-DNABU_SESSION_EVI_ENTRY_SEQ=' + entry['seq'] + 'U',
        '-DNABU_SESSION_EVI_TIME_NS=' + entry['t_ns'] + 'ULL',
        '-DNABU_SESSION_EVI_MSGID=' + entry['msgid'] + 'U',
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
        # Existing prepared trees may have the earlier peer patch without the
        # new continuation hunk. Reconstruct only that preimage in this copy.
        text = (destination / 'mach_msg.c').read_text()
        needle = '\tmr = mach_msg_receive_results(NULL);\n\tthread_syscall_return(mr);'
        if 'nabu_session_ipc_continuation_end(mr);' not in text:
            assert text.count(needle) == 1
            text = text.replace(needle, '\tmr = mach_msg_receive_results(NULL);\n'
                '#if defined(NABU) && NABU\n\tnabu_session_ipc_continuation_end(mr);\n'
                '#endif\n\tthread_syscall_return(mr);')
            (destination / 'mach_msg.c').write_text(text)
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
    continuation = text[text.index('mach_msg_receive_continue(void)'):]
    continuation = continuation[:continuation.index('\n}')]
    assert continuation.index('mr = mach_msg_receive_results(NULL);') < continuation.index(
        'nabu_session_ipc_continuation_end(mr);') < continuation.index('thread_syscall_return(mr);')
    # Verify the exact native helper/lifetime assumptions in this source version.
    port_source = (source / 'osfmk/ipc/ipc_port.c').read_text()
    assert 'ipc_port_get_receiver_task_locked(ipc_port_t port, task_t *task)' in port_source
    assert 'return waitq_lock_try(&port->ip_waitq);' in port_source
    assert 'return (int)os_atomic_load(&task->task_tokens->tc_pid, relaxed);' in (
        source / 'osfmk/kern/task.c').read_text()
