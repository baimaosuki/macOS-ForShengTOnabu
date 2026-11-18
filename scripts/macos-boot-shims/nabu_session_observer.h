/* SPDX-License-Identifier: MIT
 * Opt-in native macOS session observation, LS-store sync and idle sleep hold.
 * The ordinary observation deadline does not restart or halt the system.
 */
#ifndef NABU_SESSION_OBSERVER_H
#define NABU_SESSION_OBSERVER_H
#include <stdint.h>
#include <string.h>

static const unsigned nabu_session_offsets[] = {0, 120, 240, 320, 480, 660};
static const unsigned nabu_session_auth_offsets[] = {0, 30, 90, 180, 360, 540};
static const unsigned nabu_session_late_offsets[] = {1000, 1250};
#define NABU_SESSION_ROUNDS 6u
#define NABU_SESSION_LATE_ROUNDS 8u
static int
nabu_session_auth_target(const char *name)
{
    return !strcmp(name, "authd") || !strcmp(name, "authorizationhost") ||
        !strcmp(name, "SecurityAgent") || !strcmp(name, "usermanagerd");
}
static int
nabu_session_late_target(const char *name)
{
    return !strcmp(name, "WindowServer") || !strcmp(name, "loginwindow") ||
        !strcmp(name, "lsd") || !strcmp(name, "backgroundtaskmanagementd") ||
        !strcmp(name, "usermanagerd");
}
struct nabu_session_target {
    const char *name;
    uint64_t discovered;
    unsigned round;
};
static int
nabu_session_due(const struct nabu_session_target *target, uint64_t now)
{
    const unsigned *offsets = nabu_session_auth_target(target->name) ?
        nabu_session_auth_offsets : nabu_session_offsets;
    unsigned round = target->round;
    if (!now || (round && now < target->discovered)) return 0;
    if (!round) return 1;
    if (round >= NABU_SESSION_ROUNDS) {
        if (round >= NABU_SESSION_LATE_ROUNDS || !nabu_session_late_target(target->name)) return 0;
        return now - target->discovered >= UINT64_C(1000000000) *
            nabu_session_late_offsets[round - NABU_SESSION_ROUNDS];
    }
    return now - target->discovered >= UINT64_C(1000000000) * offsets[round];
}

/* Match complete boot-argument tokens, not substrings or later false values. */
static int
nabu_session_enabled(const char *arguments)
{
    const char wanted[] = "nabu-session-observer=1";
    while (*arguments) {
        while (*arguments == ' ' || *arguments == '\t') ++arguments;
        const char *start = arguments;
        while (*arguments && *arguments != ' ' && *arguments != '\t') ++arguments;
        if ((size_t)(arguments - start) == sizeof(wanted) - 1 &&
            !memcmp(start, wanted, sizeof(wanted) - 1)) return 1;
    }
    return 0;
}

#ifndef NABU_SESSION_HOST_TEST
#include "nabu_raw_stacks.h"
#include <fcntl.h>
#include <libproc.h>
#include <spawn.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/sysctl.h>
#include <sys/wait.h>
#include <sys/reboot.h>
#include "nabu_observer_output.h"
#include "nabu_clean_shutdown.h"
#include "nabu_process_list.h"
#include "nabu_store_sync.h"

extern char **environ;

#include "nabu_display_awake.h"

static uint64_t
nabu_session_clock(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_UPTIME_RAW, &now)) return 0;
    return (uint64_t)now.tv_sec * UINT64_C(1000000000) + (uint64_t)now.tv_nsec;
}

static int
nabu_session_directory(void)
{
    const char *path = "/private/var/log/nabu-session27";
    if (mkdir(path, 0700) && errno != EEXIST) return -1;
    int directory = open(path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    struct stat st;
    if (directory < 0) return -1;
    if (fstat(directory, &st) || st.st_uid || (st.st_mode & 077)) {
        close(directory); return -1;
    }
    return directory;
}

static int
nabu_session_watch(void)
{
    nabu_raw_console_fd = open("/dev/console", O_WRONLY | O_NOCTTY | O_CLOEXEC | O_NONBLOCK);
    if (nabu_raw_console_fd < 0)
        fprintf(stderr, "NABU27-SESSION: console open failed errno=%d\n", errno);
    nabu_raw_output_fd = -1;
    nabu_raw_timestamp_ns = nabu_session_clock();
    nabu_raw_printf("NABU-RAWSTACK startup version=35 pid=%d console_errno=%d\n",
        getpid(), nabu_raw_console_fd < 0 ? errno : 0);
    /* Lock the persistent inode, not a PID file: a reboot automatically
     * releases it and a repeated phase-2 invocation cannot start a second watch. */
    int directory = nabu_session_directory();
    int lock = directory < 0 ? -1 : openat(directory, "observer.lock",
        O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK, 0600);
    struct stat st;
    int lock_ok = lock >= 0 && !fstat(lock, &st);
    if (lock_ok && (!S_ISREG(st.st_mode) || st.st_uid || (st.st_mode & 077))) {
        errno = EPERM; lock_ok = 0;
    }
    if (!lock_ok || flock(lock, LOCK_EX | LOCK_NB)) {
        int error = errno;
        nabu_raw_printf("NABU-RAWSTACK startup disk_lock_failed errno=%d directory=%d lock=%d\n",
            error, directory, lock);
        if (lock >= 0) close(lock);
        if (directory >= 0) close(directory);
        if (nabu_raw_console_fd >= 0) close(nabu_raw_console_fd);
        return 1;
    }
    char name[96];
    int output = nabu_observer_output(directory, (unsigned long long)time(NULL),
        getpid(), name, sizeof(name));
    int output_error = output < 0 ? errno : 0;
    close(directory);
    if (output < 0) {
        nabu_raw_printf("NABU-RAWSTACK startup disk_open_failed errno=%d file=%s fallback=console\n",
            output_error, name);
        if (nabu_raw_console_fd < 0) { close(lock); return 1; }
    }
    nabu_raw_output_fd = output;
    int input = open("/dev/null", O_RDONLY);
    if (input >= 0) { dup2(input, STDIN_FILENO); close(input); }
    (void)setsid();
    signal(SIGALRM, SIG_DFL);
    alarm(1860); /* A failed observer can never delay the mount boot task. */
    struct nabu_session_target targets[] = {
        {"WindowServer", 0, 0}, {"loginwindow", 0, 0}, {"lsd", 0, 0},
        {"syspolicyd", 0, 0}, {"opendirectoryd", 0, 0},
        {"coreservicesd", 0, 0}, {"launchservicesd", 0, 0},
        {"backgroundtaskmanagementd", 0, 0}, {"SecurityAgent", 0, 0},
        {"trustd", 0, 0}, {"securityd", 0, 0}, {"amfid", 0, 0},
        {"authd", 0, 0}, {"authorizationhost", 0, 0}, {"usermanagerd", 0, 0}
    };
    const unsigned count = sizeof(targets) / sizeof(targets[0]);
    uint64_t started = nabu_session_clock();
    char bootargs[8192];
    size_t bootargs_bytes = sizeof(bootargs) - 1;
    unsigned halt_seconds = 0;
    if (!sysctlbyname("kern.bootargs", bootargs, &bootargs_bytes, NULL, 0) &&
        bootargs_bytes < sizeof(bootargs)) {
        bootargs[bootargs_bytes] = 0;
        halt_seconds = nabu_clean_shutdown_seconds(bootargs);
    }
    nabu_raw_timestamp_ns = started;
    nabu_raw_printf("NABU-RAWSTACK watch begin version=35 timeout=1800 rounds=6 late_after_discovery=1000,1250 "
        "after_discovery=0,120,240,320,480,660 auth_after_discovery=0,30,90,180,360,540 "
        "disk=/private/var/log/nabu-session27/%s "
        "disk_open_errno=%d mirror=/dev/console readonly=1\n", name, output_error);
    nabu_raw_printf("NABU-RAWSTACK clean-shutdown configured_seconds=%u opt_in=%u\n",
        halt_seconds, halt_seconds != 0);
    uint64_t last_store_poll = 0, last_display_attempt = 0, last_system_attempt = 0;
    pid_t display_helper = 0, system_helper = 0;
    unsigned display_attempts = 0, system_attempts = 0;
    int store_synced = 0;
    const uint64_t duration = UINT64_C(1000000000) * (halt_seconds ? halt_seconds : 1800u);
    for (;;) {
        uint64_t poll_now = nabu_session_clock();
        if (!poll_now || poll_now < started || poll_now - started >= duration) break;
        nabu_session_hold_display(&display_helper, &display_attempts, &last_display_attempt, poll_now, "-d");
        nabu_session_hold_display(&system_helper, &system_attempts, &last_system_attempt, poll_now, "-i");
        if (!store_synced && (!last_store_poll || poll_now-last_store_poll >= UINT64_C(10000000000))) {
            last_store_poll = poll_now;
            store_synced = nabu_store_sync("/private/var/folders/zz/zyxvpxvq6csfxvn_n0000000000000/0/com.apple.LaunchServices.dv/");
        }
        unsigned due = 0;
        for (unsigned n = 0; n < count; ++n)
            if (nabu_session_due(&targets[n], poll_now)) due |= 1u << n;
        if (!due) { usleep(500000); continue; }
        size_t bytes = 0;
        struct kinfo_proc *processes = nabu_session_process_list(&bytes);
        if (processes) {
            for (size_t i = 0; i < bytes / sizeof(*processes); ++i) {
                pid_t pid = processes[i].kp_proc.p_pid;
                if (pid <= 1 || pid == getpid()) continue;
                for (unsigned n = 0; n < count; ++n) {
                    if (!(due & (1u << n))) continue;
                    /* p_comm is truncated. Confirm the executable basename
                     * so the long backgroundtaskmanagementd name works. */
                    if (strncmp(processes[i].kp_proc.p_comm, targets[n].name,
                        sizeof(processes[i].kp_proc.p_comm) - 1)) continue;
                    char path[PROC_PIDPATHINFO_MAXSIZE];
                    int size = proc_pidpath(pid, path, sizeof(path));
                    if (size <= 0 || size >= (int)sizeof(path)) continue;
                    path[size] = 0;
                    const char *base = strrchr(path, '/');
                    if (!base || strcmp(base + 1, targets[n].name)) continue;
                    uint64_t now = nabu_session_clock();
                    if (!now || now < started || now - started >= duration) break;
                    nabu_raw_pid = pid;
                    nabu_raw_round = targets[n].round + 1;
                    nabu_raw_timestamp_ns = now;
                    nabu_raw_printf("NABU-RAWSTACK watch target=%s pid=%d path=%.160s\n",
                        targets[n].name, pid, path);
                    if (!targets[n].round)
                        nabu_raw_printf("NABU-RAWSTACK schedule target=%s "
                            "after_discovery=%s\n", targets[n].name,
                            nabu_session_auth_target(targets[n].name) ?
                            "0,30,90,180,360,540" : "0,120,240,320,480,660");
                    (void)fsync(output);
                    pid_t child = fork();
                    if (!child) {
                        alarm(15);
                        _exit(nabu_raw_target(pid));
                    }
                    if (child > 0) {
                        int status = 0;
                        pid_t waited;
                        do { waited = waitpid(child, &status, 0); }
                        while (waited < 0 && errno == EINTR);
                        nabu_raw_printf("NABU-RAWSTACK watch result=%s pid=%d "
                            "wait=%d status_raw=%d truncated=%u\n",
                            targets[n].name, pid, waited, status,
                            waited < 0 || !WIFEXITED(status) || WEXITSTATUS(status));
                        if (!targets[n].round) targets[n].discovered = now;
                        ++targets[n].round;
                        due &= ~(1u << n);
                        (void)fsync(output);
                    } else nabu_raw_printf("NABU-RAWSTACK fork errno=%d\n", errno);
                }
            }
            free(processes);
        } else nabu_raw_printf("NABU-RAWSTACK process-list errno=%d bytes=%zu retries=5 capacity_factor=2 slack=65536\n", errno, bytes);
        usleep(500000);
    }
    nabu_raw_timestamp_ns = nabu_session_clock();
    for (unsigned n = 0; n < count; ++n)
        nabu_raw_printf("NABU-RAWSTACK watch summary target=%s rounds=%u\n",
            targets[n].name, targets[n].round);
    nabu_raw_printf("NABU-RAWSTACK watch end io_errors=%u\n", nabu_raw_io_errors);
    (void)fsync(output);
    if (halt_seconds && nabu_raw_timestamp_ns >= started && nabu_raw_timestamp_ns - started >= duration) {
        nabu_raw_timestamp_ns = nabu_session_clock();
        nabu_raw_printf("NABU-RAWSTACK clean-shutdown request sync=1 reboot_flags=RB_HALT\n");
        (void)fsync(output);
        alarm(0);
        int result = nabu_clean_shutdown_request(halt_seconds, sync, reboot, RB_HALT);
        int error = errno;
        nabu_raw_printf("NABU-RAWSTACK clean-shutdown returned result=%d errno=%d\n",
            result, error);
    }
    if (nabu_raw_console_fd >= 0) close(nabu_raw_console_fd);
    if (output >= 0) close(output);
    close(lock);
    return 0;
}

static void
nabu_session_start(const char *phase)
{
    if (strcmp(phase, "2")) return;
    char arguments[8192];
    size_t bytes = sizeof(arguments) - 1;
    if (sysctlbyname("kern.bootargs", arguments, &bytes, NULL, 0) || bytes >= sizeof(arguments)) return;
    arguments[bytes] = 0;
    if (!nabu_session_enabled(arguments)) return;
    const char *path = "/System/Library/Filesystems/apfs.fs/Contents/Resources/apfs_boot_util";
    char *argv[] = {(char *)path, "--nabu-session-watch", NULL};
    pid_t pid;
    /* A fresh spawn without PANIC_ON_EXIT/CRASH flags; never wait for the
     * observer from a RequireSuccess boot task. */
    int error = posix_spawn(&pid, path, NULL, NULL, argv, environ);
    fprintf(stderr, "NABU27-SESSION: observer spawn error=%d pid=%d\n", error, error ? -1 : pid);
}
#endif
#endif
