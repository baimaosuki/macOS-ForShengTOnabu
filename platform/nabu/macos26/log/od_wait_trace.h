/* SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef NABU_OD_WAIT_TRACE_H
#define NABU_OD_WAIT_TRACE_H

#ifndef NABU_OD_WAIT_TRACE_HOST_TEST
#include <kern/clock.h>
#include <kern/thread.h>
#include <pexpert/pexpert.h>
#include <stdarg.h>
#include <string.h>
extern int proc_selfpid(void);
extern void proc_name(int, char *, int);
extern int snprintf(char *, size_t, const char *, ...) __attribute__((format(printf, 3, 4)));
extern void nabu_capture_append(const char *, unsigned);
#endif

/* Observer only: no changes to deadlines, wait options, references or results.
 * Reserve a return event when admitting an entry so earlier IPC trace budgets
 * cannot hide the timeout. Slots are owned by one live thread until completion.
 * Full tables/budgets simply omit observations and keep the original path. */
#define NABU_OD_WAIT_SLOTS 64
#define NABU_OD_WAIT_LIMIT 256
static struct nabu_od_wait_slot {
    uint64_t tid;
    uint64_t started;
    unsigned name;
    int pid;
} nabu_od_wait_slots[NABU_OD_WAIT_SLOTS];
static unsigned nabu_od_wait_entries;

static inline void
nabu_od_wait_write(const char *text, int length, unsigned capacity)
{
    if (length <= 0) return;
    nabu_capture_append(text, (unsigned)length < capacity ? (unsigned)length : capacity - 1);
}

static inline int
nabu_od_wait_begin(unsigned name, unsigned sec, unsigned nsec)
{
    unsigned enabled = 0;
    if (!PE_parse_boot_argn("nabu-od-wait-log", &enabled, sizeof(enabled)) || enabled != 1)
        return 0;
    char process[32] = {0};
    int pid = proc_selfpid();
    proc_name(pid, process, sizeof(process));
    if (strcmp(process, "opendirectoryd")) return 0;
    if (__atomic_fetch_add(&nabu_od_wait_entries, 1, __ATOMIC_RELAXED) >= NABU_OD_WAIT_LIMIT)
        return 0;
    uint64_t tid = thread_tid(current_thread());
    if (!tid) return 0;
    for (unsigned i = 0; i < NABU_OD_WAIT_SLOTS; i++) {
        struct nabu_od_wait_slot *slot = &nabu_od_wait_slots[i];
        uint64_t empty = 0;
        if (!__atomic_compare_exchange_n(&slot->tid, &empty, tid, 0,
            __ATOMIC_ACQ_REL, __ATOMIC_RELAXED)) continue;
        slot->started = mach_absolute_time();
        slot->name = name;
        slot->pid = pid;
        uint64_t ns = 0;
        clock_sec_t calendar_sec;
        clock_nsec_t calendar_nsec;
        mach_timebase_info_data_t ratio;
        absolutetime_to_nanoseconds(slot->started, &ns);
        clock_get_calendar_nanotime(&calendar_sec, &calendar_nsec);
        clock_timebase_info(&ratio);
        char text[384];
        int length = snprintf(text, sizeof(text),
            "NABU-OD-WAIT event=enter pid=%d tid=%llu port=%x sec=%u nsec=%u abs=%llu t_ns=%llu calendar=%llu.%09u numer=%u denom=%u\n",
            pid, (unsigned long long)tid, name, sec, nsec,
            (unsigned long long)slot->started, (unsigned long long)ns,
            (unsigned long long)calendar_sec, (unsigned)calendar_nsec, ratio.numer, ratio.denom);
        nabu_od_wait_write(text, length, sizeof(text));
        return 1;
    }
    return 0;
}

static inline void
nabu_od_wait_end(int result, unsigned continued)
{
    uint64_t tid = thread_tid(current_thread());
    for (unsigned i = 0; i < NABU_OD_WAIT_SLOTS; i++) {
        struct nabu_od_wait_slot *slot = &nabu_od_wait_slots[i];
        if (__atomic_load_n(&slot->tid, __ATOMIC_ACQUIRE) != tid) continue;
        uint64_t now = mach_absolute_time(), elapsed_ns = 0;
        absolutetime_to_nanoseconds(now - slot->started, &elapsed_ns);
        char text[256];
        int length = snprintf(text, sizeof(text),
            "NABU-OD-WAIT event=return pid=%d tid=%llu port=%x result=%d continued=%u abs=%llu elapsed_ns=%llu\n",
            slot->pid, (unsigned long long)tid, slot->name, result, continued,
            (unsigned long long)now, (unsigned long long)elapsed_ns);
        nabu_od_wait_write(text, length, sizeof(text));
        __atomic_store_n(&slot->tid, 0, __ATOMIC_RELEASE);
        return;
    }
}

/* A blocking semaphore discards its C stack and resumes through sth_continuation.
 * A normal post-call print misses precisely the timeout we need to observe. */
static void
nabu_od_wait_continue(kern_return_t result)
{
    nabu_od_wait_end(result, 1);
    thread_syscall_return(result);
}
#endif
