/* SPDX-License-Identifier: MIT */
#ifndef NABU_CLEAN_SHUTDOWN_H
#define NABU_CLEAN_SHUTDOWN_H
#include <stddef.h>
#include <string.h>

/* An explicit diagnostic menu entry only. Invalid/duplicate values disable it. */
static unsigned
nabu_clean_shutdown_seconds(const char *args)
{
    static const char key[] = "nabu-clean-shutdown=";
    unsigned selected = 0, seen = 0;
    if (!args) return 0;
    while (*args) {
        while (*args == ' ' || *args == '\t') ++args;
        const char *start = args;
        while (*args && *args != ' ' && *args != '\t') ++args;
        size_t length = (size_t)(args - start);
        if (length < sizeof(key) - 1 || memcmp(start, key, sizeof(key) - 1)) continue;
        if (++seen != 1) return 0;
        unsigned value = 0;
        for (size_t i = sizeof(key) - 1; i < length; ++i) {
            if (start[i] < '0' || start[i] > '9' || value > 1800) return 0;
            value = value * 10 + (unsigned)(start[i] - '0');
        }
        if (value < 600 || value > 1800) return 0;
        selected = value;
    }
    return selected;
}

/* The caller supplies RB_HALT only: never RB_NOSYNC or an immediate power cut. */
static int
nabu_clean_shutdown_request(unsigned seconds, void (*flush)(void),
    int (*halt)(int), int halt_flags)
{
    if (seconds < 600 || seconds > 1800) return 0;
    flush();
    return halt(halt_flags);
}
#endif
