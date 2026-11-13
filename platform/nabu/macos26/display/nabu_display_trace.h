// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#ifndef NABU_DISPLAY_TRACE_HOST_TEST
#include <kern/clock.h>
#include <libkern/libkern.h>
#include <stdarg.h>
#include "framebuffer_owner.h"
#endif

extern "C" {
extern int disableConsoleOutput;
void nabu_capture_append(const char *, unsigned);
}

// A display gate must not wait on console draining or lose its diagnostics
// when WindowServer takes over the native console. One bounded RAM append,
// no IOLog, console rendering, allocation, storage I/O or waiting for a lock.
static inline void __attribute__((format(printf, 1, 2)))
nabuDisplayLog(const char *format, ...)
{
    char text[640];
    static uint32_t sequence;
    uint32_t seq = __atomic_fetch_add(&sequence, 1, __ATOMIC_RELAXED);
    va_list arguments;
    va_start(arguments, format);
    int length = vsnprintf(text, 512, format, arguments);
    va_end(arguments);
    if (length <= 0) return;
    unsigned used = (unsigned)length < 512 ? (unsigned)length : 511;
    while (used && (text[used - 1] == '\n' || text[used - 1] == '\r')) --used;
    uint64_t ns = 0;
    absolutetime_to_nanoseconds(mach_absolute_time(), &ns);
    int tail = snprintf(text + used, sizeof(text) - used,
        " seq=%u t_ns=%llu console_disabled=%d owner=%u\n", seq, (unsigned long long)ns,
        __atomic_load_n(&disableConsoleOutput, __ATOMIC_RELAXED),
        __atomic_load_n(&sheng_framebuffer.owner, __ATOMIC_RELAXED));
    if (tail <= 0) return;
    unsigned remaining = sizeof(text) - used;
    used += (unsigned)tail < remaining ? (unsigned)tail : remaining - 1;
    nabu_capture_append(text, used);
}
