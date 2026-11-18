/* SPDX-License-Identifier: MIT */
#ifndef NABU_DISPLAY_AWAKE_H
#define NABU_DISPLAY_AWAKE_H
/* Process existence gates early boot attempts; assertion success is still
 * determined by the native tool and a failed helper remains retryable. */
static int
nabu_session_powerd_running(void)
{
    size_t bytes = 0;
    struct kinfo_proc *processes = nabu_session_process_list(&bytes);
    if (!processes) return 0;
    int running = 0;
    for (size_t i = 0; i < bytes / sizeof(*processes); ++i) {
        if (processes[i].kp_proc.p_pid <= 1 ||
            strcmp(processes[i].kp_proc.p_comm, "powerd")) continue;
        char path[PROC_PIDPATHINFO_MAXSIZE];
        int length = proc_pidpath(processes[i].kp_proc.p_pid, path, sizeof(path));
        if (length <= 0 || length >= (int)sizeof(path)) continue;
        path[length] = 0;
        const char *base = strrchr(path, '/');
        if (base && !strcmp(base + 1, "powerd")) { running = 1; break; }
    }
    free(processes);
    return running;
}

static void
nabu_session_hold_display(pid_t *helper, unsigned *attempts, uint64_t *last, uint64_t now, const char *flag)
{
    if (*helper > 0) {
        int status = 0;
        pid_t result = waitpid(*helper, &status, WNOHANG);
        if (result == 0 || (result < 0 && errno != ECHILD)) return;
        nabu_raw_timestamp_ns = now;
        nabu_raw_printf("NABU-RAWSTACK display-awake helper exit flag=%s pid=%d status_raw=%d result=%d\n",
            flag, *helper, status, result);
        *helper = 0;
    }
    if (*last && (now < *last || now - *last < UINT64_C(15000000000))) return;
    *last = now;
    if (!nabu_session_powerd_running()) {
        nabu_raw_timestamp_ns = now;
        nabu_raw_printf("NABU-RAWSTACK display-awake waiting flag=%s powerd_running=0\n", flag);
        return;
    }
    ++*attempts;
    char parent[24];
    snprintf(parent, sizeof(parent), "%d", getpid());
    const char *path = "/usr/bin/caffeinate";
    /* Separate native processes keep a failed system assertion from aborting
     * display assertion creation. Retry only while the bounded watch runs.
     * Each helper releases its assertion when this observer exits. */
    char *argv[] = {(char *)path, (char *)flag, "-w", parent, NULL};
    pid_t child;
    int error = posix_spawn(&child, path, NULL, NULL, argv, environ);
    if (!error) *helper = child;
    nabu_raw_timestamp_ns = now;
    nabu_raw_printf("NABU-RAWSTACK display-awake spawn flag=%s attempt=%u error=%d pid=%d\n",
        flag, *attempts, error, error ? -1 : child);
}

#endif
