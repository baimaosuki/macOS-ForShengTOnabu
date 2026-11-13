/* SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef SHENG_PROCESS_OUTPUT_H
#define SHENG_PROCESS_OUTPUT_H

#include <kern/clock.h>
#include <kern/locks.h>

/* This selector is intentionally inert without the NABU lifecycle opt-in. */
static TUNABLE(unsigned, nabu_boot_filesystem_output, "nabu-boot-proc", 0);
static TUNABLE(unsigned, nabu_probe_output, "nabu-session-probe", 0);
extern void nabu_capture_append(const char *, unsigned);
static unsigned nabu_probe_output_bytes, nabu_probe_output_dropped;

/* Preserve real boot-shim diagnostic records independently of /dev/console.
 * Observe attempted fd2 writes without changing write validation or results.
 * Single-line writes only; no arbitrary shell/cat stream or outputv bypass. */
static void
nabu_probe_output_capture(struct proc *process, int fd, user_addr_t address,
    user_size_t bytes)
{
    static const char prefix[] = "NABU-RAWSTACK ";
    if (nabu_probe_output != 1 || fd != 2 ||
        strcmp(process->p_comm, "apfs_boot_util") || !kauth_cred_issuser(kauth_cred_get()) ||
        bytes < sizeof(prefix) || bytes > 512)
        return;
    char text[512];
    if (copyin(address, text, (size_t)bytes) ||
        memcmp(text, prefix, sizeof(prefix) - 1) || text[bytes - 1] != '\n')
        return;
    for (size_t i = 0; i + 1 < bytes; ++i)
        if ((unsigned char)text[i] < 32 || text[i] == 127)
            return;
    unsigned old = __atomic_load_n(&nabu_probe_output_bytes, __ATOMIC_RELAXED);
    for (;;) {
        if (bytes > 1048576u - old) {
            if (!__atomic_exchange_n(&nabu_probe_output_dropped, 1, __ATOMIC_RELAXED)) {
                static const char drop[] = "NABU-RAWSTACK dropped reason=global-1MiB-budget\n";
                nabu_capture_append(drop, sizeof(drop) - 1);
            }
            return;
        }
        if (__atomic_compare_exchange_n(&nabu_probe_output_bytes, &old,
            old + (unsigned)bytes, 0, __ATOMIC_RELAXED, __ATOMIC_RELAXED))
            break;
    }
    nabu_capture_append(text, (unsigned)bytes);
}
static LCK_GRP_DECLARE(nabu_output_group, "nabu boot stderr");
static LCK_MTX_DECLARE(nabu_output_lock, &nabu_output_group);
static struct {
    uint64_t last_ns, suppressed;
    unsigned initial;
} nabu_output_buckets[12];

static int nabu_output_kind(const char *name)
{
    const char *const names[] = {"mount", "mount_apfs", "fskitd", "WindowServer",
                               "diskarbitrationd", "cryptexd", "cfprefsd", "digest-service",
                               "eligibilityd", "endpointsecurity", "opendirectoryd", "loginwindow"};
    for (unsigned i = 0; i < sizeof(names) / sizeof(names[0]); i++)
        if (!strncmp(name, names[i], MAXCOMLEN + 1)) return (int)i;
    if (!strcmp(name, "endpointsecurityd")) return 9;
    return -1;
}

static int nabu_output_admit(int kind, uint64_t *suppressed)
{
    uint64_t ns = 0;
    int emit = 0;
    absolutetime_to_nanoseconds(mach_absolute_time(), &ns);
    lck_mtx_lock(&nabu_output_lock);
    if (nabu_output_buckets[kind].initial < 8 ||
        ns - nabu_output_buckets[kind].last_ns >= 5000000000ULL) {
        if (nabu_output_buckets[kind].initial < 8) nabu_output_buckets[kind].initial++;
        *suppressed = nabu_output_buckets[kind].suppressed;
        nabu_output_buckets[kind].suppressed = 0;
        nabu_output_buckets[kind].last_ns = ns;
        emit = 1;
    } else {
        nabu_output_buckets[kind].suppressed++;
    }
    lck_mtx_unlock(&nabu_output_lock);
    return emit;
}

/* Adapted from DroidVM ba207d7's bounded stdout/stderr diagnostic.
 * Records attempted output; a concurrent writer can change the user buffer.
 * The original write still owns its validation, side effects and return value.
 */
static int sheng_process_output_enabled(struct proc *process, int fd, unsigned mode, const char *name)
{
    if (!mode || mode > 2 || (fd != 2 && (mode != 2 || fd != 1))) return 0;
    if (!strncmp(name, "boot-filesystems", MAXCOMLEN + 1))
        return nabu_boot_filesystem_output == 1 && nabu_output_kind(process->p_comm) >= 0;
    if (!strncmp(name, "recovery-ui", MAXCOMLEN + 1)) {
        const char *const services[] = {"recoveryosd", "RecoveryOS Agent", "Language Chooser", "Recovery Springb"};
        for (unsigned i = 0; i < sizeof(services) / sizeof(services[0]); i++)
            if (!strncmp(process->p_comm, services[i], MAXCOMLEN + 1)) return 1;
        return 0;
    }
    return !name[0] || !strncmp(process->p_comm, name, MAXCOMLEN + 1);
}

static void sheng_process_output_text(struct proc *process, int fd, char text[160], size_t count, int truncated, const char *selector)
{
    uint64_t suppressed = 0;
    int bounded = !strncmp(selector, "boot-filesystems", MAXCOMLEN + 1);
    if (bounded) {
        int kind = nabu_output_kind(process->p_comm);
        if (kind < 0 || !nabu_output_admit(kind, &suppressed)) return;
    }
    for (size_t i = 0; i < count; i++)
        if ((unsigned char)text[i] < 32 || text[i] == 127)
            text[i] = ' ';
    text[count] = 0;
    if (bounded)
        printf("SHENG: fd%d pid %d %s suppressed=%llu - %s%s\n", fd, proc_getpid(process),
            process->p_comm, suppressed, text, truncated ? " [truncated]" : "");
    else
        printf("SHENG: fd%d pid %d %s - %s%s\n", fd, proc_getpid(process),
            process->p_comm, text, truncated ? " [truncated]" : "");
}

static void
sheng_process_output(struct proc *process, int fd, user_addr_t address,
    user_size_t bytes, unsigned mode, const char *name)
{
    nabu_probe_output_capture(process, fd, address, bytes);
    if (!bytes || !sheng_process_output_enabled(process, fd, mode, name)) return;
    char text[160];
    size_t count = bytes < sizeof(text) - 1 ? (size_t)bytes : sizeof(text) - 1;
    if (copyin(address, text, count) != 0) return;
    sheng_process_output_text(process, fd, text, count, bytes > count, name);
}

/* Inspect the validated UIO without advancing it; cap the entire vector write. */
static void sheng_process_outputv(struct proc *process, int fd, uio_t uio, unsigned mode, const char *name)
{
    if (!sheng_process_output_enabled(process, fd, mode, name)) return;
    char text[160];
    size_t count = 0;
    int index = 0, truncated = 0, vectors = uio_iovcnt(uio);
    for (; index < vectors && index < 16; index++) {
        user_addr_t address; user_size_t bytes;
        if (uio_getiov(uio, index, &address, &bytes) != 0) return;
        size_t take = bytes < 159 - count ? (size_t)bytes : 159 - count;
        if (take && copyin(address, text + count, take) != 0) return;
        count += take;
        if (bytes > take) { truncated = 1; break; }
        if (count == 159) { index++; break; }
    }
    if (count) sheng_process_output_text(process, fd, text, count, truncated || index < vectors, name);
}

#endif
