/* SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef NABU_WINDOWSERVER_TRACE_H
#define NABU_WINDOWSERVER_TRACE_H

#ifndef NABU_WS_TRACE_HOST_TEST
#include <arm/thread.h>
#include <kern/clock.h>
#include <kern/task.h>
#include <kern/thread.h>
#include <libkern/copyio.h>
#include <pexpert/pexpert.h>
#include <sys/proc.h>
#include <vm/vm_shared_region_xnu.h>
#include <stdarg.h>
extern void nabu_capture_append(const char *, unsigned);
#endif

/* No console recursion, disk I/O, panic or changes to the observed result.
 * Each translation unit admits at most 32 events for the whole boot. */
static unsigned nabu_ws_trace_count;

static inline int
nabu_ws_trace_enabled(void)
{
    unsigned enabled = 0;
    return PE_parse_boot_argn("nabu-ws-log", &enabled, sizeof(enabled)) && enabled == 1;
}

static inline int
nabu_ws_trace_selected(int pid)
{
    char name[32] = {0};
    proc_name(pid, name, sizeof(name));
    return !strcmp(name, "WindowServer");
}

static inline void __attribute__((format(printf, 1, 2)))
nabu_ws_trace_emit(const char *format, ...)
{
    char text[512];
    va_list args;
    va_start(args, format);
    int length = vsnprintf(text, sizeof(text), format, args);
    va_end(args);
    if (length <= 0) return;
    unsigned count = (unsigned)length < sizeof(text) ? (unsigned)length : sizeof(text) - 1;
    nabu_capture_append(text, count);
}

static inline uint64_t
nabu_ws_trace_user_address(uint64_t raw)
{
    return raw & 0x00007fffffffffffULL;
}

/* The caller must be in its own still-live user map and hold no proc/task lock.
 * This runs at exit syscall/exception entry, not final process destruction. */
static inline void __attribute__((unused))
nabu_ws_trace_context(const char *event, uint64_t code, uint64_t subcode, uint64_t detail)
{
    if (!nabu_ws_trace_enabled()) return;
    int pid = proc_selfpid();
    if (!nabu_ws_trace_selected(pid)) return;
    unsigned sequence = __atomic_fetch_add(&nabu_ws_trace_count, 1, __ATOMIC_RELAXED);
    if (sequence >= 32) return;
    uint64_t ns = 0;
    absolutetime_to_nanoseconds(mach_absolute_time(), &ns);
    arm_saved_state_t *state = get_user_regs(current_thread());
    nabu_ws_trace_emit("NABU-WS event=%s pid=%d seq=%u t_ns=%llu code=%llx sub=%llx detail=%llx state64=%d\n",
        event, pid, sequence, (unsigned long long)ns,
        (unsigned long long)code, (unsigned long long)subcode, (unsigned long long)detail,
        state && is_saved_state64(state));
    if (!state || !is_saved_state64(state)) return;
    uint64_t pc = get_saved_state_pc(state), lr = get_saved_state_lr(state);
    uint64_t sp = get_saved_state_sp(state), fp = get_saved_state_fp(state);
    uint64_t slide = UINT64_MAX;
    vm_shared_region_t region = vm_shared_region_get(current_task());
    if (region) {
        slide = region->sr_slide;
        vm_shared_region_deallocate(region);
    }
    uint32_t instruction = 0;
    int fetch = copyin((user_addr_t)nabu_ws_trace_user_address(pc), &instruction, sizeof(instruction));
    nabu_ws_trace_emit("NABU-WS pid=%d seq=%u pc=%llx lr=%llx sp=%llx fp=%llx esr=%llx far=%llx insn=%08x fetch=%d slide=%llx\n",
        pid, sequence, (unsigned long long)pc, (unsigned long long)lr,
        (unsigned long long)sp, (unsigned long long)fp,
        (unsigned long long)get_saved_state_esr(state),
        (unsigned long long)get_saved_state_far(state), instruction, fetch,
        (unsigned long long)slide);
    const arm_saved_state64_t *ss = const_saved_state64(state);
    for (unsigned i = 0; i < 28; i += 4)
        nabu_ws_trace_emit("NABU-WS pid=%d seq=%u regs=%u %llx %llx %llx %llx\n",
            pid, sequence, i, (unsigned long long)ss->x[i], (unsigned long long)ss->x[i + 1],
            (unsigned long long)ss->x[i + 2], (unsigned long long)ss->x[i + 3]);
    nabu_ws_trace_emit("NABU-WS pid=%d seq=%u x28=%llx\n", pid, sequence, (unsigned long long)ss->x[28]);
    uint64_t bottom = nabu_ws_trace_user_address(sp), frame = nabu_ws_trace_user_address(fp);
    for (unsigned i = 0; i < 12; i++) {
        if (!frame || (frame & 15) || frame < bottom || frame - bottom > 0x100000 ||
            frame > 0x00007ffffdfffff0ULL) break;
        uint64_t pair[2] = {0};
        int error = copyin((user_addr_t)frame, pair, sizeof(pair));
        nabu_ws_trace_emit("NABU-WS pid=%d seq=%u frame=%u fp=%llx next=%llx lr=%llx caller=%llx copy=%d\n",
            pid, sequence, i, (unsigned long long)frame, (unsigned long long)pair[0],
            (unsigned long long)pair[1], (unsigned long long)nabu_ws_trace_user_address(pair[1]), error);
        if (error) break;
        uint64_t next = nabu_ws_trace_user_address(pair[0]);
        if (next <= frame) break;
        frame = next;
    }
}

/* Metadata only: callers of task termination may already hold other locks.
 * Never read a foreign task's user map or dereference its BSD proc pointer. */
static inline void __attribute__((unused))
nabu_ws_trace_terminate(task_t target, uintptr_t caller)
{
    if (!nabu_ws_trace_enabled()) return;
    int pid = task_pid(target);
    if (!nabu_ws_trace_selected(pid)) return;
    unsigned sequence = __atomic_fetch_add(&nabu_ws_trace_count, 1, __ATOMIC_RELAXED);
    if (sequence >= 32) return;
    nabu_ws_trace_emit("NABU-WS event=task-terminate target=%d caller_pid=%d self=%d seq=%u kernel_caller=%llx\n",
        pid, proc_selfpid(), target == current_task(), sequence, (unsigned long long)caller);
}

#endif
