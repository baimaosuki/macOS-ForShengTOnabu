"""Exercise the production retry helper across assertion failures and recovery."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]


def test_independent_assertions_and_late_recovery(tmp_path):
    source = tmp_path / 'awake.c'
    source.write_text(r'''
#include <assert.h>
#include <errno.h>
#include <stdlib.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/types.h>
#include <sys/wait.h>
static uint64_t nabu_raw_timestamp_ns;
static unsigned spawns, waits;
static int wait_error, spawn_error, alive;
static char last_flag[3];
#define PROC_PIDPATHINFO_MAXSIZE 4096
struct kinfo_proc { struct {pid_t p_pid; char p_comm[17];} kp_proc; };
static unsigned powerd_mode;
static struct kinfo_proc *nabu_session_process_list(size_t *bytes) {
    if (powerd_mode == 3) { *bytes = 0; return NULL; }
    struct kinfo_proc *p = calloc(1, sizeof(*p)); assert(p);
    p->kp_proc.p_pid = 115;
    strcpy(p->kp_proc.p_comm, powerd_mode == 1 ? "launchd" : "powerd");
    *bytes = sizeof(*p); return p;
}
static int proc_pidpath(pid_t pid, void *buffer, uint32_t capacity) {
    assert(pid == 115 && capacity == PROC_PIDPATHINFO_MAXSIZE);
    const char *path = powerd_mode == 2 ? "/usr/libexec/other" :
        "/System/Library/CoreServices/powerd.bundle/powerd";
    strcpy(buffer, path); return (int)strlen(path);
}
static char *environment[] = {NULL};
static char **environ = environment;
static int nabu_raw_printf(const char *format, ...) {
    char output[512]; va_list ap; va_start(ap, format);
    int n = vsnprintf(output, sizeof(output), format, ap); va_end(ap); return n;
}
static pid_t getpid(void) { return 88; }
static pid_t fake_waitpid(pid_t child, int *status, int options) {
    assert(options == WNOHANG); ++waits;
    if (wait_error) { errno = wait_error; return -1; }
    if (alive) return 0;
    *status = 256; return child;
}
static int fake_spawn(pid_t *child, const char *path, const void *a,
        const void *b, char *const argv[], char *const env[]) {
    (void)a; (void)b; assert(env == environ);
    assert(!strcmp(path, "/usr/bin/caffeinate"));
    assert(!strcmp(argv[0], path));
    assert(!strcmp(argv[1], "-d") || !strcmp(argv[1], "-i"));
    assert(!strcmp(argv[2], "-w")); assert(!strcmp(argv[3], "88"));
    assert(!argv[4]); strcpy(last_flag, argv[1]); ++spawns;
    if (!spawn_error) *child = 100 + spawns;
    return spawn_error;
}
#define waitpid fake_waitpid
#define posix_spawn fake_spawn
#include "nabu_display_awake.h"
int main(void) {
    const uint64_t second = UINT64_C(1000000000);
    pid_t display = 0, system = 0; unsigned da = 0, sa = 0;
    uint64_t dl = 0, sl = 0;
    /* No early spawn before the actual powerd executable exists. */
    for (powerd_mode = 1; powerd_mode <= 3; ++powerd_mode) {
        uint64_t now = powerd_mode * 15 * second;
        nabu_session_hold_display(&display, &da, &dl, now, "-d");
        assert(!display && !da && !spawns);
    }
    powerd_mode = 0; dl = 0;
    /* Fail more than eight times, including after the former 161s cutoff. */
    for (unsigned n = 0; n < 12; ++n) {
        uint64_t now = (54 + n*15)*second;
        nabu_session_hold_display(&display, &da, &dl, now, "-d");
        assert(display > 0 && da == n+1 && !strcmp(last_flag,"-d"));
        nabu_session_hold_display(&system, &sa, &sl, now, "-i");
        assert(system > 0 && sa == n+1 && !strcmp(last_flag,"-i"));
    }
    assert(da == 12 && sa == 12 && spawns == 24);
    /* Once display succeeds, system failures cannot reap or replace it. */
    pid_t held_display = display; alive = 1;
    nabu_session_hold_display(&display, &da, &dl, 240*second, "-d");
    assert(display == held_display && da == 12);
    alive = 0;
    nabu_session_hold_display(&system, &sa, &sl, 240*second, "-i");
    assert(display == held_display && sa == 13 && spawns == 25);
    /* Spawn failure is throttled, then recovers at the interval boundary. */
    pid_t missing = 0; unsigned ma = 0; uint64_t ml = 0;
    spawn_error = ENOENT;
    nabu_session_hold_display(&missing,&ma,&ml,300*second,"-d");
    assert(!missing && ma == 1);
    nabu_session_hold_display(&missing,&ma,&ml,314*second,"-d");
    assert(!missing && ma == 1);
    nabu_session_hold_display(&missing,&ma,&ml,299*second,"-d");
    assert(!missing && ma == 1);
    spawn_error = 0;
    nabu_session_hold_display(&missing,&ma,&ml,315*second,"-d");
    assert(missing > 0 && ma == 2);
    /* Interrupted wait retains ownership; ECHILD permits replacement. */
    wait_error = EINTR; unsigned before = spawns;
    nabu_session_hold_display(&missing,&ma,&ml,400*second,"-d");
    assert(spawns == before && ma == 2);
    wait_error = ECHILD;
    nabu_session_hold_display(&missing,&ma,&ml,400*second,"-d");
    assert(spawns == before+1 && ma == 3);
    assert(waits && nabu_raw_timestamp_ns == 400*second);
    return 0;
}
''')
    binary = tmp_path / 'awake'
    subprocess.run([shutil.which('clang') or 'cc', '-std=c11', '-Wall', '-Wextra',
                    '-Werror', '-fsanitize=address,undefined',
                    '-fno-sanitize-recover=all', '-I',
                    str(ROOT / 'scripts/macos-boot-shims'), str(source),
                    '-o', str(binary)], check=True)
    subprocess.run([binary], check=True)


if __name__ == '__main__':
    import tempfile
    with tempfile.TemporaryDirectory(prefix='nabu-display-awake-') as directory:
        test_independent_assertions_and_late_recovery(Path(directory))
    print('display/system assertion independence and late-recovery checks passed')
