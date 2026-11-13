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
/* 0: resolver; 1/2: legacy IPC; 3..8: per-process entry/result;
 * 9..11: per-process IPC errors. After the initial burst, each IPC channel
 * keeps one record per second rather than silently losing all later events. */
static unsigned nabu_session_events[12];
static uint64_t nabu_session_sample_second[12];

static inline int
nabu_session_enabled(void)
{
    unsigned enabled = 0;
    return PE_parse_boot_argn("nabu-session-log", &enabled, sizeof(enabled)) && enabled == 1;
}

static inline void __attribute__((format(printf, 2, 3)))
nabu_session_emit(unsigned channel, const char *format, ...)
{
    if (channel >= 12 || !nabu_session_enabled()) return;
    unsigned sequence = __atomic_fetch_add(&nabu_session_events[channel], 1, __ATOMIC_RELAXED);
    unsigned limit = channel == 0 ? 512U : channel < 3 ? 128U : 256U;
    uint64_t ns = 0;
    absolutetime_to_nanoseconds(mach_absolute_time(), &ns);
    const char *sampling = "initial";
    if (sequence >= limit) {
        if (channel < 3) return;
        sampling = sequence == limit ? "budget-exhausted-now-1-per-sec" : "1-per-sec";
        if (sequence != limit) {
            uint64_t second = ns / 1000000000ULL;
            uint64_t previous = __atomic_load_n(&nabu_session_sample_second[channel], __ATOMIC_RELAXED);
            if (second <= previous || !__atomic_compare_exchange_n(
                &nabu_session_sample_second[channel], &previous, second, 0,
                __ATOMIC_RELAXED, __ATOMIC_RELAXED)) return;
        } else {
            __atomic_store_n(&nabu_session_sample_second[channel], ns / 1000000000ULL, __ATOMIC_RELAXED);
        }
    }
    char text[512];
    int prefix;
    if (channel < 3) {
        prefix = snprintf(text, sizeof(text), "NABU-SESSION pid=%d tid=%llu t_ns=%llu ",
            proc_selfpid(), (unsigned long long)thread_tid(current_thread()), (unsigned long long)ns);
    } else {
        prefix = snprintf(text, sizeof(text), "NABU-SESSION pid=%d tid=%llu t_ns=%llu sample=%s seq=%u ",
            proc_selfpid(), (unsigned long long)thread_tid(current_thread()),
            (unsigned long long)ns, sampling, sequence);
    }
    if (prefix <= 0 || (unsigned)prefix >= sizeof(text)) return;
    va_list args;
    va_start(args, format);
    int count = vsnprintf(text + prefix, sizeof(text) - (unsigned)prefix, format, args);
    va_end(args);
    if (count <= 0) return;
    unsigned length = (unsigned)prefix + (unsigned)count;
    if (length >= sizeof(text)) {
        static const char tail[] = " truncated=1\n";
        length = sizeof(text) - 1;
        memcpy(text + length - (sizeof(tail) - 1), tail, sizeof(tail) - 1);
    } else if (!length || text[length - 1] != '\n') {
        if (length == sizeof(text) - 1) text[length - 1] = '\n';
        else text[length++] = '\n';
    }
    nabu_capture_append(text, length);
}

/* Call-reserved loginwindow records bypass event sampling. Each admission
 * reserves the maximum bytes before entry; the fixed-size writer cannot spend
 * more than that reservation. Physical journal contention/overwrite is still
 * measured by NabuLogCore, not claimed away by this producer. */
static inline void __attribute__((format(printf, 2, 3)))
nabu_session_reserved_emit(unsigned capacity, const char *format, ...)
{
    char text[512];
    if (capacity > sizeof(text) || capacity < 64 || !nabu_session_enabled()) return;
    uint64_t ns = 0;
    absolutetime_to_nanoseconds(mach_absolute_time(), &ns);
    int prefix = snprintf(text, capacity, "NABU-SESSION pid=%d tid=%llu t_ns=%llu ",
        proc_selfpid(), (unsigned long long)thread_tid(current_thread()), (unsigned long long)ns);
    if (prefix <= 0 || (unsigned)prefix >= capacity) return;
    va_list args;
    va_start(args, format);
    int count = vsnprintf(text + prefix, capacity - (unsigned)prefix, format, args);
    va_end(args);
    if (count <= 0) return;
    unsigned length = (unsigned)prefix + (unsigned)count;
    if (length >= capacity) {
        static const char tail[] = " truncated=1\n";
        length = capacity - 1;
        memcpy(text + length - (sizeof(tail) - 1), tail, sizeof(tail) - 1);
    } else if (text[length - 1] != '\n') {
        if (length == capacity - 1) text[length - 1] = '\n';
        else text[length++] = '\n';
    }
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
    if (strcmp(name, "WindowServer") && strcmp(name, "loginwindow") && strcmp(name, "opendirectoryd")) return;
    nabu_session_emit(result ? 2 : 1,
        "event=%s name=%s options=%llx msgid=%u remote=%x receive=%x result=%x\n",
        event, name, (unsigned long long)options, msgid, remote, receive, result);
}
#endif
