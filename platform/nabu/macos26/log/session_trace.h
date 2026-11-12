/* SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef NABU_SESSION_TRACE_H
#define NABU_SESSION_TRACE_H

#ifndef NABU_SESSION_TRACE_HOST_TEST
#include <kern/clock.h>
#include <kern/thread.h>
#include <pexpert/pexpert.h>
#include <stdarg.h>
extern int proc_selfpid(void);
extern void proc_name(int, char *, int);
extern int snprintf(char *, size_t, const char *, ...) __attribute__((format(printf, 3, 4)));
extern int vsnprintf(char *, size_t, const char *, va_list) __attribute__((format(printf, 3, 0)));
extern void nabu_capture_append(const char *, unsigned);
#endif

/* RAM-only observer. Safe at resolver-lock call sites: no console, allocator,
 * proc lookup, user copy or blocking journal lock. Limits are per source file. */
static unsigned nabu_session_events[3];

static inline int
nabu_session_enabled(void)
{
    unsigned enabled = 0;
    return PE_parse_boot_argn("nabu-session-log", &enabled, sizeof(enabled)) && enabled == 1;
}

static inline void __attribute__((format(printf, 2, 3)))
nabu_session_emit(unsigned channel, const char *format, ...)
{
    if (channel >= 3 || !nabu_session_enabled()) return;
    unsigned sequence = __atomic_fetch_add(&nabu_session_events[channel], 1, __ATOMIC_RELAXED);
    if (sequence >= (channel == 0 ? 512U : 128U)) return;
    char text[384];
    uint64_t ns = 0;
    absolutetime_to_nanoseconds(mach_absolute_time(), &ns);
    int prefix = snprintf(text, sizeof(text), "NABU-SESSION pid=%d tid=%llu t_ns=%llu ",
        proc_selfpid(), (unsigned long long)thread_tid(current_thread()), (unsigned long long)ns);
    if (prefix <= 0 || (unsigned)prefix >= sizeof(text)) return;
    va_list args;
    va_start(args, format);
    int count = vsnprintf(text + prefix, sizeof(text) - (unsigned)prefix, format, args);
    va_end(args);
    if (count <= 0) return;
    unsigned length = (unsigned)prefix + (unsigned)count;
    if (length >= sizeof(text)) length = sizeof(text) - 1;
    nabu_capture_append(text, length);
}

/* Called only outside IPC locks. Thread ids correlate trap entry with receive
 * completion even when the receive resumes through a continuation. */
static inline void
nabu_session_ipc(const char *event, uint64_t options, uint32_t msgid,
    uint32_t remote, uint32_t receive, uint32_t result)
{
    if (!nabu_session_enabled()) return;
    char name[32] = {0};
    proc_name(proc_selfpid(), name, sizeof(name));
    if (strcmp(name, "loginwindow") && strcmp(name, "opendirectoryd")) return;
    nabu_session_emit(result ? 2 : 1,
        "event=%s name=%s options=%llx msgid=%u remote=%x receive=%x result=%x\n",
        event, name, (unsigned long long)options, msgid, remote, receive, result);
}
#endif
