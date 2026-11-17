/* SPDX-License-Identifier: MIT
 * Opt-in, read-only observation of the native macOS session. It does not
 * substitute an XPC reply, suspend a target or alter rendering/service policy.
 */
#ifndef NABU_SESSION_OBSERVER_H
#define NABU_SESSION_OBSERVER_H
#include <stdint.h>
#include <string.h>

static const unsigned nabu_session_offsets[] = {0, 120, 240, 320};
struct nabu_session_target {
    const char *name;
    uint64_t discovered;
    unsigned round;
};
static int
nabu_session_due(const struct nabu_session_target *target, uint64_t now)
{
    return now && target->round < 4 &&
        (!target->round || (now >= target->discovered &&
        now - target->discovered >= UINT64_C(1000000000) * nabu_session_offsets[target->round]));
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

extern char **environ;

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
    /* Lock the persistent inode, not a PID file: a reboot automatically
     * releases it and a repeated phase-2 invocation cannot start a second watch. */
    int directory = nabu_session_directory();
    int lock = directory < 0 ? -1 : openat(directory, "observer.lock",
        O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK, 0600);
    struct stat st;
    if (lock < 0 || fstat(lock, &st) || !S_ISREG(st.st_mode) || st.st_uid ||
        (st.st_mode & 077) || flock(lock, LOCK_EX | LOCK_NB)) {
        if (lock >= 0) close(lock);
        if (directory >= 0) close(directory);
        fprintf(stderr, "NABU27-SESSION: observer not started: lock/directory errno=%d\n", errno);
        return 1;
    }
    char name[96];
    snprintf(name, sizeof(name), "session-%llu-%d.log",
        (unsigned long long)time(NULL), getpid());
    int output = openat(directory, name,
        O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    close(directory);
    if (output < 0) { close(lock); return 1; }
    nabu_raw_output_fd = output;
    nabu_raw_console_fd = open("/dev/console", O_WRONLY | O_NOCTTY | O_CLOEXEC);
    int input = open("/dev/null", O_RDONLY);
    if (input >= 0) { dup2(input, STDIN_FILENO); close(input); }
    (void)setsid();
    signal(SIGALRM, SIG_DFL);
    alarm(960); /* A failed observer can never delay the mount boot task. */
    struct nabu_session_target targets[] = {
        {"WindowServer", 0, 0}, {"loginwindow", 0, 0}, {"lsd", 0, 0},
        {"syspolicyd", 0, 0}, {"opendirectoryd", 0, 0},
        {"coreservicesd", 0, 0}, {"launchservicesd", 0, 0},
        {"backgroundtaskmanagementd", 0, 0}, {"SecurityAgent", 0, 0}
    };
    const unsigned count = sizeof(targets) / sizeof(targets[0]);
    uint64_t started = nabu_session_clock();
    nabu_raw_timestamp_ns = started;
    nabu_raw_printf("NABU-RAWSTACK watch begin version=27 timeout=900 rounds=4 "
        "after_discovery=0,120,240,320 disk=/private/var/log/nabu-session27/%s "
        "mirror=/dev/console readonly=1\n", name);
    while (nabu_session_clock() - started < UINT64_C(900000000000)) {
        int mib[] = {CTL_KERN, KERN_PROC, KERN_PROC_ALL, 0};
        size_t bytes = 0;
        if (!sysctl(mib, 4, NULL, &bytes, NULL, 0) && bytes && bytes <= 8 * 1024 * 1024) {
            size_t capacity = bytes + 16 * sizeof(struct kinfo_proc);
            struct kinfo_proc *processes = malloc(capacity);
            if (processes) {
                bytes = capacity;
                if (!sysctl(mib, 4, processes, &bytes, NULL, 0) && bytes <= capacity &&
                    !(bytes % sizeof(*processes))) {
                    for (size_t i = 0; i < bytes / sizeof(*processes); ++i) {
                        pid_t pid = processes[i].kp_proc.p_pid;
                        if (pid <= 1 || pid == getpid()) continue;
                        for (unsigned n = 0; n < count; ++n) {
                            uint64_t now = nabu_session_clock();
                            if (!nabu_session_due(&targets[n], now)) continue;
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
                            nabu_raw_pid = pid;
                            nabu_raw_round = targets[n].round + 1;
                            nabu_raw_timestamp_ns = now;
                            nabu_raw_printf("NABU-RAWSTACK watch target=%s pid=%d path=%.160s\n",
                                targets[n].name, pid, path);
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
                                (void)fsync(output);
                            } else nabu_raw_printf("NABU-RAWSTACK fork errno=%d\n", errno);
                        }
                    }
                } else nabu_raw_printf("NABU-RAWSTACK process-list errno=%d bytes=%zu\n", errno, bytes);
                free(processes);
            }
        }
        unsigned complete = 0;
        for (unsigned n = 0; n < count; ++n) complete += targets[n].round == 4;
        if (complete == count) break;
        usleep(500000);
    }
    nabu_raw_timestamp_ns = nabu_session_clock();
    for (unsigned n = 0; n < count; ++n)
        nabu_raw_printf("NABU-RAWSTACK watch summary target=%s rounds=%u\n",
            targets[n].name, targets[n].round);
    nabu_raw_printf("NABU-RAWSTACK watch end io_errors=%u\n", nabu_raw_io_errors);
    (void)fsync(output);
    if (nabu_raw_console_fd >= 0) close(nabu_raw_console_fd);
    close(output); close(lock);
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
