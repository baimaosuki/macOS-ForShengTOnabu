// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <stdint.h>

struct NabuCpuSourceSample {
    uint64_t count = 0, rgb = 0, alpha = 0, rawRgb = 0;
    uint64_t firstRaw = 0;
};
// A bounded 16x16 grid over the source rectangle. Counts are samples, not a
// proof that the entire allocation is black. Read bytes to allow padded rows.
static inline NabuCpuSourceSample nabuCpuSampleSource(const uint8_t *pixels,
    uint64_t bytes, uint64_t stride, uint64_t width, uint64_t height,
    const NabuGopRect &source, const uint8_t *half)
{
    NabuCpuSourceSample out{};
    const uint64_t element = half ? 8 : 4;
    if (!pixels || !width || !height || width > 16384 || height > 16384 ||
        stride < width * element || stride > UINT64_MAX / height || stride * height > bytes ||
        source.x < 0 || source.y < 0 || source.width <= 0 || source.height <= 0 ||
        uint64_t(source.x) + source.width > width || uint64_t(source.y) + source.height > height)
        return out;
    const unsigned nx = source.width < 16 ? source.width : 16;
    const unsigned ny = source.height < 16 ? source.height : 16;
    for (unsigned y = 0; y < ny; ++y) for (unsigned x = 0; x < nx; ++x) {
        const uint64_t sx = source.x + (nx > 1 ? uint64_t(x) * (source.width - 1) / (nx - 1) : 0);
        const uint64_t sy = source.y + (ny > 1 ? uint64_t(y) * (source.height - 1) / (ny - 1) : 0);
        const uint8_t *p = pixels + sy * stride + sx * element;
        uint64_t raw = 0;
        for (unsigned b = 0; b < element; ++b) raw |= uint64_t(p[b]) << (8 * b);
        uint32_t pixel;
        if (half) {
            const unsigned r = raw & 65535, g = (raw >> 16) & 65535;
            const unsigned b = (raw >> 32) & 65535, a = raw >> 48;
            out.rawRgb += ((r | g | b) & 0x7fff) != 0;
            pixel = uint32_t(half[a]) << 24 | uint32_t(half[r]) << 16 |
                    uint32_t(half[g]) << 8 | half[b];
        } else {
            pixel = uint32_t(raw); out.rawRgb += (pixel & 0xffffff) != 0;
        }
        if (!out.count) out.firstRaw = raw;
        ++out.count; out.rgb += (pixel & 0xffffff) != 0; out.alpha += (pixel >> 24) != 0;
    }
    return out;
}
static inline uint64_t nabuCpuTraceMix(uint64_t state, uint64_t value)
{
    return (state ^ value) * UINT64_C(1099511628211);
}
static inline bool nabuCpuTraceDue(uint64_t frame, bool changed, bool firstRgb, bool blackChanged)
{
    (void)changed; (void)firstRgb; (void)blackChanged;
    return frame <= 10 || frame % 300 == 0;
}
