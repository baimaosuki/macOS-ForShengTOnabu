// SPDX-License-Identifier: MIT
#pragma once
#include "nabu_gop_scanout.h"
#define NABU_DISPLAY_UUID "4E414255-2D47-4F50-8000-000000000001"
// Fixed panel only. Never map arbitrary firmware-supplied physical memory.
static inline bool nabuDisplayConsoleValid(uint64_t base, uint64_t width,
                                          uint64_t height, uint64_t stride)
{
    return (base & ~3ULL) == NABU_GOP_PHYS_BASE &&
           width == NABU_GOP_PANEL_WIDTH && height == NABU_GOP_PANEL_HEIGHT &&
           stride == NABU_GOP_PANEL_STRIDE &&
           nabuGopFramebufferValid(base & ~3ULL, NABU_GOP_ACTIVE_BYTES,
               (uint32_t)width, (uint32_t)height, (uint32_t)stride);
}
