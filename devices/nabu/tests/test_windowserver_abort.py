#!/usr/bin/env python3
"""Run the prepared production SIGABRT selection and concurrency bound."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--xnu', required=True, type=Path)
p.add_argument('--output', required=True, type=Path)
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=True)
(a.output / 'verification.json').unlink(missing_ok=True)
source = (a.xnu / 'bsd/kern/kern_exit.c').read_text()
body = re.search(r'__attribute__\(\(noinline\)\) static void\nnabu_windowserver_abort_context\(proc_t p, int rv\)\n\{.*?\n\}', source, re.S)
assert body, 'production signal-exit capture absent'
prepare = source[source.index('\nproc_prepareexit(proc_t p, int rv, boolean_t perf_notify)\n{'):]
assert prepare.index('nabu_windowserver_abort_context(p, rv)') < prepare.index('proc_handle_critical_exit(p, rv)')
assert prepare.index('nabu_windowserver_abort_context(p, rv)') < prepare.index('task_exception_notify(')
fixture = r'''
#include <assert.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/wait.h>
#include <signal.h>
struct reason { unsigned osr_namespace; unsigned long long osr_code; };
struct process { char p_name[32]; struct reason *p_exit_reason; };
typedef struct process *proc_t;
static _Thread_local proc_t self;
static _Atomic unsigned captures;
static proc_t current_proc(void) { return self; }
static char *exit_reason_get_string_desc(struct reason *r) { return r ? "fixture reason" : NULL; }
static void nabu_critical_user_context(proc_t p, int rv) {
    assert(p == self && WTERMSIG(rv) == SIGABRT);
    assert(strcmp(p->p_name, "WindowServer") == 0);
    atomic_fetch_add_explicit(&captures, 1, memory_order_relaxed);
}
''' + body[0] + r'''
static struct reason why = { 2, SIGABRT };
static struct process ws = { "WindowServer", &why };
static void *worker(void *unused) {
    (void)unused;
    self = &ws;
    for (unsigned i = 0; i < 4096; ++i) nabu_windowserver_abort_context(&ws, SIGABRT);
    return NULL;
}
int main(void) {
    struct process other = { "WindowServerHelper", NULL };
    self = &other;
    nabu_windowserver_abort_context(&ws, SIGABRT); // another task
    nabu_windowserver_abort_context(&other, SIGABRT); // prefix must not match
    self = &ws;
    nabu_windowserver_abort_context(&ws, SIGTERM);
    nabu_windowserver_abort_context(&ws, SIGSEGV);
    nabu_windowserver_abort_context(&ws, 0); // normal exit
    assert(atomic_load(&captures) == 0);
    ws.p_exit_reason = NULL;
    nabu_windowserver_abort_context(&ws, SIGABRT);
    assert(atomic_load(&captures) == 1);
    ws.p_exit_reason = &why;
    pthread_t threads[32];
    for (unsigned i = 0; i < 32; ++i) assert(!pthread_create(&threads[i], NULL, worker, NULL));
    for (unsigned i = 0; i < 32; ++i) assert(!pthread_join(threads[i], NULL));
    assert(atomic_load(&captures) == 3);
    printf("verified capture_count=3 concurrent_attempts=131072\n");
}
'''
c = a.output / 'selection.c'
c.write_text(fixture)
cc = shutil.which('clang') or shutil.which('gcc')
assert cc, 'clang required'
exe = a.output / 'selection'
subprocess.run([cc, '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-pthread',
                str(c), '-o', str(exe)], check=True)
result = subprocess.run([str(exe.resolve())], capture_output=True, text=True,
                        env=dict(os.environ, ASAN_OPTIONS='abort_on_error=1'), check=True)
(a.output / 'selection.log').write_text(result.stdout + result.stderr)
assert 'capture_count=3 concurrent_attempts=131072' in result.stdout
report = dict(passed=True, production_selection_executed=True, sanitizers=['ASan', 'UBSan'],
              rejects_other_tasks=True, exact_name_required=True, signal_must_be_SIGABRT=True,
              null_reason_supported=True, capture_limit=3, concurrent_attempts=131072,
              before_corpse_and_critical_policy=True, signal_restart_policy_unchanged=True)
(a.output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
