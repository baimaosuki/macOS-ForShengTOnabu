/* SPDX-License-Identifier: GPL-3.0-or-later
 * Opt-in development policy. Match launchd job labels exactly, never prefixes.
 */
#ifndef NABU_MINIMAL_BOOT_H
#define NABU_MINIMAL_BOOT_H

static const char *const nabu_minimal_boot_allowlist[] = {
    "com.apple.opendirectoryd",
    "com.apple.cfprefsd.xpc.daemon",
    "com.apple.cfprefsd.xpc.agent",
    "com.apple.notifyd",
    "com.apple.logd",
    "com.apple.distnoted.xpc.daemon",
    "com.apple.distnoted.xpc.agent",
    "com.apple.configd",
    "com.apple.powerd",
    "com.apple.xpc.roleaccountd",
    "com.apple.smd",
    "com.apple.WindowServer",
    "com.apple.hidd",
    "com.apple.iohideventsystem",
    "com.apple.MultitouchSupport",
    "com.apple.loginwindow",
    "com.apple.SecurityServer",
    "com.apple.securityd",
    "com.apple.authd",
    "com.apple.coreservicesd",
    "com.apple.coreservices.launchservicesd",
    "com.apple.lsd",
    "com.apple.runningboardd",
    "com.apple.tccd",
    "com.apple.tccd.system",
    /* Paired 25F71 SecurityAgent job labels, not executable/service guesses. */
    "com.apple.security.agent",
    "com.apple.security.agent.login",
};

static inline bool
nabu_minimal_boot_allowed(const char *label)
{
    for (unsigned i = 0; i < sizeof(nabu_minimal_boot_allowlist) /
        sizeof(nabu_minimal_boot_allowlist[0]); ++i) {
        if (!strcmp(label, nabu_minimal_boot_allowlist[i])) return true;
    }
    return false;
}

#ifndef NABU_MINIMAL_BOOT_POLICY_ONLY
volatile unsigned int nabu_minimal_boot_parked;
volatile unsigned int nabu_minimal_boot_hold_failures;

static bool
nabu_minimal_boot_enabled(void)
{
    uint32_t value = 0;
    return PE_parse_boot_argn("nabu-minimal-boot", &value, sizeof(value)) && value == 1;
}

/* The arguments have already been copied and validated by exec. Never reread
 * the parent's mutable userspace argv to make the final image decision. */
static const char *
nabu_minimal_boot_label(const struct image_params *imgp)
{
    if (imgp->ip_argc < 2 || !imgp->ip_startargv || !imgp->ip_endargv ||
        imgp->ip_endargv <= imgp->ip_startargv) return NULL;
    const char *start = imgp->ip_startargv, *end = imgp->ip_endargv;
    const char *zero = memchr(start, 0, (size_t)(end - start));
    if (!zero || zero + 1 >= end) return NULL;
    start = zero + 1;
    zero = memchr(start, 0, (size_t)(end - start));
    if (!zero || zero == start || zero - start >= 128) return NULL;
    return start;
}

static int
nabu_minimal_boot_apply(proc_t p, struct image_params *imgp, task_t task)
{
    if (strcmp(p->p_comm, "xpcproxy") || !nabu_minimal_boot_enabled()) return 0;
    const char *label = nabu_minimal_boot_label(imgp);
    if (label && nabu_minimal_boot_allowed(label)) return 0;

    /* This is a kernel hold, independent of POSIX_SPAWN_START_SUSPENDED and
     * user_stop_count. launchd's normal resume cannot consume it. Keep the
     * proxy alive so KeepAlive does not see an exit and restart the job. The
     * new task has not entered userspace; task_wait_locked skips the current
     * thread for an exec route. Parent spawn completion must never be parked.
     * No task/port reference is retained; termination destroys the held task. */
    kern_return_t kr = task_hold_and_wait(task, false);
    if (kr != KERN_SUCCESS) {
        os_atomic_inc(&nabu_minimal_boot_hold_failures, relaxed);
        return EIO;
    }
    unsigned count = os_atomic_inc_orig(&nabu_minimal_boot_parked, relaxed) + 1;
    if (count <= 64) {
        char line[256];
        int length = snprintf(line, sizeof(line),
            "NABU-MINIMAL: parked pid=%d ppid=%d label=%s mode=kernel-hold seq=%u\n",
            proc_getpid(p), proc_ppid(p), label ? label : "<invalid-label>", count);
        if (length > 0 && (size_t)length < sizeof(line))
            nabu_capture_append(line, (unsigned)length);
    } else if (count == 65) {
        static const char capped[] = "NABU-MINIMAL: park-record-limit=64 further-records-suppressed\n";
        nabu_capture_append(capped, sizeof(capped) - 1);
    }
    return 0;
}
#endif
#endif
