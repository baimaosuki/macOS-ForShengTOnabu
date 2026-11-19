/* SPDX-License-Identifier: MIT */
#pragma once
#include <string.h>

/* Both OS render servers own the primary window and shared diagnostics. */
static inline bool nabuGpuRenderServer(const char *name)
{
    return name && (!strcmp(name,"WindowServer") || !strcmp(name,"backboardd"));
}
