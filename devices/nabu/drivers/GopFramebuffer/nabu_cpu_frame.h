// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "nabu_cpu_present.h"

struct NabuCpuPlaneView {
    const uint8_t *pixels = nullptr;
    uint64_t bytes = 0, stride = 0, width = 0, height = 0;
    NabuGopRect source{}, destination{};
    const uint8_t *halfToByte = nullptr;
    bool active = false;
};

// A source-backed complete scene is built in ordinary RAM before scanout.
// Plane 1 is the native premultiplied cursor, after opaque planes 0,2,3.
static inline bool nabuCpuCompose(uint32_t *output, uint64_t capacity,
                                  uint32_t width, uint32_t height,
                                  uint32_t background,
                                  const NabuCpuPlaneView (&planes)[4])
{
    if (!output || !width || !height || width > 16384 || height > 16384 ||
        capacity < uint64_t(width) * height * 4) return false;
    NabuGopClip clips[4]{};
    for (unsigned i = 0; i != 4; ++i) {
        const auto &p = planes[i];
        if (!p.active) continue;
        const uint64_t element = p.halfToByte ? 8 : 4;
        if (!p.pixels || !p.width || !p.height || p.width > 16384 || p.height > 16384 ||
            p.stride < p.width * element || p.stride > UINT64_MAX / p.height ||
            p.stride * p.height > p.bytes ||
            !nabuGopClip(p.source, p.destination, p.width, p.height, width, height, clips[i]))
            return false;
    }
    for (uint64_t p = 0; p != uint64_t(width) * height; ++p) output[p] = background | 0xff000000U;
    const unsigned order[] = {0, 2, 3, 1};
    for (unsigned i : order) {
        const auto &p = planes[i];
        const auto &clip = clips[i];
        if (!p.active || clip.x1 <= clip.x0 || clip.y1 <= clip.y0) continue;
        for (int64_t y = clip.y0; y < clip.y1; ++y) {
            const uint64_t sy = p.source.y +
                uint64_t(y - p.destination.y) * uint64_t(p.source.height) / uint64_t(p.destination.height);
            nabuGopLayerRow(output + y * width, p.pixels + sy * p.stride,
                            p.source, p.destination, clip, p.halfToByte, i == 1);
        }
    }
    return true;
}

static inline bool nabuCpuScanout(volatile uint8_t *output, uint64_t capacity,
                                 uint64_t stride, const uint32_t *scene,
                                 uint32_t width, uint32_t height)
{
    if (!output || !scene || !width || !height || (uintptr_t(output) & 3) || (stride & 3) ||
        stride < uint64_t(width) * 4 || stride > UINT64_MAX / height || stride * height > capacity)
        return false;
    for (uint32_t y = 0; y != height; ++y) {
        auto row = reinterpret_cast<volatile uint32_t *>(output + y * stride);
        for (uint32_t x = 0; x != width; ++x) row[x] = scene[uint64_t(y) * width + x];
    }
    return true;
}
