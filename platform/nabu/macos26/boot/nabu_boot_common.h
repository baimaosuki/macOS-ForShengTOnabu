// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "sources/unixnu-components/DarwinBoot/DarwinBootPkg/Driver/DiagFont.h"
#include "platform/nabu/macos26/display/nabu_gop_scanout.h"

// Darwin 23/25 pexpert/pexpert/arm64/boot.h ABI (matches UniXNU/DarwinBoot BootArgs.h)
struct DarwinBootArgs {
    uint16_t Revision;
    uint16_t Version;
    uint32_t Padding;
    uint64_t VirtualBase;
    uint64_t PhysicalBase;
    uint64_t MemorySize;
    uint64_t TopOfKernelData;
    uint64_t Video[6];
    uint32_t MachineType;
    uint32_t TreePadding;
    uint64_t DeviceTree;
    uint32_t DeviceTreeLength;
    char     CommandLine[1024];
    uint32_t FlagsPadding;
    uint64_t BootFlags;
    uint64_t MemorySizeActual;
};

_Static_assert(sizeof(struct DarwinBootArgs) == 0x480, "DarwinBootArgs size must be 0x480");

static const uint64_t kNabuGlyphFont[96] = GLYPH_FONT;

static inline void nabu_memzero(void *dst, size_t n)
{
    volatile uint8_t *p = (volatile uint8_t *)dst;
    while (n--) {
        *p++ = 0;
    }
}

static inline void nabu_memcpy(void *dst, const void *src, size_t n)
{
    volatile uint8_t *d = (volatile uint8_t *)dst;
    const uint8_t *s = (const uint8_t *)src;
    while (n--) {
        *d++ = *s++;
    }
}

static inline void nabu_fb_fill_rect(uint32_t x0, uint32_t y0, uint32_t w, uint32_t h, uint32_t bgra)
{
    volatile uint32_t *fb = (volatile uint32_t *)(uintptr_t)NABU_GOP_PHYS_BASE;
    const uint32_t stride_px = NABU_GOP_PANEL_STRIDE / 4U;
    for (uint32_t y = y0; y < y0 + h && y < NABU_GOP_PANEL_HEIGHT; y++) {
        volatile uint32_t *row = fb + (uint64_t)y * stride_px;
        for (uint32_t x = x0; x < x0 + w && x < NABU_GOP_PANEL_WIDTH; x++) {
            row[x] = bgra;
        }
    }
}

static inline void nabu_fb_draw_char(uint32_t x0, uint32_t y0, char c, uint32_t fg, uint32_t bg, uint32_t scale)
{
    if (c < 32 || c > 127) {
        c = '?';
    }
    const uint64_t glyph = kNabuGlyphFont[(unsigned char)c - 32U];
    volatile uint32_t *fb = (volatile uint32_t *)(uintptr_t)NABU_GOP_PHYS_BASE;
    const uint32_t stride_px = NABU_GOP_PANEL_STRIDE / 4U;

    for (uint32_t row = 0; row < 12U; row++) {
        const uint32_t shift_base = (row < 6U) ? (32U + row * 5U) : ((row - 6U) * 5U);
        for (uint32_t col = 0; col < 6U; col++) {
            const bool bit_on = (col < 5U) ? (((glyph >> (shift_base + col)) & 1ULL) != 0ULL) : false;
            const uint32_t color = bit_on ? fg : bg;
            for (uint32_t sy = 0; sy < scale; sy++) {
                const uint32_t py = y0 + row * scale + sy;
                if (py >= NABU_GOP_PANEL_HEIGHT) {
                    continue;
                }
                volatile uint32_t *dst_row = fb + (uint64_t)py * stride_px;
                for (uint32_t sx = 0; sx < scale; sx++) {
                    const uint32_t px = x0 + col * scale + sx;
                    if (px < NABU_GOP_PANEL_WIDTH) {
                        dst_row[px] = color;
                    }
                }
            }
        }
    }
}

static inline uint32_t nabu_fb_draw_string(uint32_t x, uint32_t y, const char *s, uint32_t fg, uint32_t bg, uint32_t scale)
{
    const uint32_t adv = 6U * scale;
    while (*s) {
        nabu_fb_draw_char(x, y, *s++, fg, bg, scale);
        x += adv;
    }
    return x;
}

static inline uint32_t nabu_fb_draw_hex64(uint32_t x, uint32_t y, uint64_t val, uint32_t fg, uint32_t bg, uint32_t scale)
{
    x = nabu_fb_draw_string(x, y, "0x", fg, bg, scale);
    const char *hex = "0123456789abcdef";
    const uint32_t adv = 6U * scale;
    for (int i = 60; i >= 0; i -= 4) {
        nabu_fb_draw_char(x, y, hex[(val >> i) & 0xFU], fg, bg, scale);
        x += adv;
    }
    return x;
}
