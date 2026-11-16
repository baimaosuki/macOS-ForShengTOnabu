// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <stdint.h>

// One independent software display clock. Submission must never change its
// deadline: a stream of swaps can otherwise postpone the refresh indefinitely.
// All operations run under the original framebuffer work-loop gate.
struct NabuRefreshClock {
    static constexpr unsigned periodNanoseconds = 16666667u; // declared 60 Hz
    uint64_t interval = 0, deadline = 0;

    void reset() { deadline = 0; }
    uint64_t next(uint64_t now) {
        if (!interval || now > UINT64_MAX - interval) return 0;
        if (!deadline) deadline = now + interval;
        else {
            // Skip expired ticks without sending a burst of fictitious VBLs.
            uint64_t steps = deadline <= now ? (now - deadline) / interval + 1 : 1;
            if (steps > (UINT64_MAX - deadline) / interval) return 0;
            deadline += steps * interval;
        }
        return deadline;
    }
};
