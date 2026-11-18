// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "nabu_cpu_present.h"
#include <string.h>

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
                                 uint32_t width, uint32_t height,
                                 uint32_t *previous = nullptr, bool previousValid = false,
                                 uint64_t *writtenBytes = nullptr, bool *seenRgb = nullptr)
{
    if (writtenBytes) *writtenBytes = 0;
    if (!output || !scene || !width || !height || (uintptr_t(output) & 3) || (stride & 3) ||
        stride < uint64_t(width) * 4 || stride > UINT64_MAX / height || stride * height > capacity)
        return false;
    uint32_t anyRgb = 0;
    for (uint32_t y = 0; y != height; ++y) {
        const uint32_t *source = scene + uint64_t(y) * width;
        uint32_t *old = previous ? previous + uint64_t(y) * width : nullptr;
        if (old && previousValid && !memcmp(old, source, size_t(width) * 4)) continue;
        auto row = reinterpret_cast<volatile uint32_t *>(output + y * stride);
        for (uint32_t x = 0; x != width; ++x) { const uint32_t pixel = source[x]; row[x] = pixel; anyRgb |= pixel; }
        if (old) memcpy(old, source, size_t(width) * 4);
        if (writtenBytes) *writtenBytes += uint64_t(width) * 4;
    }
    if (seenRgb && (anyRgb & 0xffffffU)) *seenRgb = true;
    return true;
}

static inline bool nabuCpuStageBars(volatile uint8_t *destination, uint64_t bytes,
    uint64_t stride, uint64_t width, uint64_t height, unsigned stages) {
    if (!destination || !width || height<8 || width>UINT64_MAX/4 || stride<width*4 ||
        stride>UINT64_MAX/height || bytes<stride*height) return false;
    const uint32_t colors[3]={0xffff0000U,0xffffff00U,0xff00ffffU};
    for (uint64_t y=0;y<8;++y) for (unsigned stage=0;stage<3;++stage) {
        if (!(stages & (1U<<stage))) continue;
        const uint64_t lo=width*stage/4, hi=width*(stage+1)/4;
        for (uint64_t x=lo;x<hi;++x)
            reinterpret_cast<volatile uint32_t *>(destination+y*stride)[x]=colors[stage];
    }
    return true;
}
