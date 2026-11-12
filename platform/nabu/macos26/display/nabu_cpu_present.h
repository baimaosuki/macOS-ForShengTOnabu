// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <stdint.h>

// 25F71 IOMFBRect: signed x, y, width, height (writeLayerDebugInfo).
struct NabuGopRect { int32_t x, y, width, height; };
struct NabuGopClip { int64_t x0, y0, x1, y1; };

static inline bool nabuGopClip(const NabuGopRect &src, const NabuGopRect &dst,
                              uint64_t width, uint64_t height,
                              uint64_t screenWidth, uint64_t screenHeight, NabuGopClip &clip)
{
    if (src.x < 0 || src.y < 0 || src.width <= 0 || src.height <= 0 ||
        dst.width <= 0 || dst.height <= 0 ||
        uint64_t(src.x) + uint64_t(src.width) > width ||
        uint64_t(src.y) + uint64_t(src.height) > height)
        return false;
    clip = {dst.x > 0 ? dst.x : 0, dst.y > 0 ? dst.y : 0,
            int64_t(dst.x) + dst.width, int64_t(dst.y) + dst.height};
    if (clip.x1 > int64_t(screenWidth)) clip.x1 = screenWidth;
    if (clip.y1 > int64_t(screenHeight)) clip.y1 = screenHeight;
    return true; // an entirely offscreen rectangle is valid and draws no pixels
}

// Only the native cursor plane uses premultiplied alpha here. Scene planes
// are opaque copies: a BGRA format alone does not imply alpha composition.
static inline uint32_t nabuGopCursorOver(uint32_t src, uint32_t dst)
{
    uint32_t alpha = src >> 24;
    if (!alpha) return dst;
    if (alpha == 255) return src;
    uint32_t out = 0xff000000;
    for (unsigned shift = 0; shift < 24; shift += 8) {
        uint32_t channel = ((src >> shift) & 255) + ((((dst >> shift) & 255) * (255 - alpha) + 127) / 255);
        out |= (channel > 255 ? 255 : channel) << shift;
    }
    return out;
}

static inline void nabuGopLayerRow(uint32_t *destination, const void *row,
                                   const NabuGopRect &src, const NabuGopRect &dst,
                                   const NabuGopClip &clip, const uint8_t *halfToByte, bool cursor)
{
    if (!halfToByte && !cursor && src.width == dst.width) {
        const uint32_t *pixels = static_cast<const uint32_t *>(row) + src.x;
        for (int64_t x = clip.x0; x < clip.x1; x++) destination[x] = pixels[x - dst.x] | 0xff000000;
        return;
    }
    for (int64_t x = clip.x0; x < clip.x1; x++) {
        uint64_t sx = src.x + uint64_t(x - dst.x) * uint64_t(src.width) / uint64_t(dst.width);
        uint32_t pixel;
        if (halfToByte) {
            const uint16_t *rgba = static_cast<const uint16_t *>(row) + sx * 4;
            pixel = uint32_t(halfToByte[rgba[3]]) << 24 | uint32_t(halfToByte[rgba[0]]) << 16 |
                    uint32_t(halfToByte[rgba[1]]) << 8 | halfToByte[rgba[2]];
        } else pixel = static_cast<const uint32_t *>(row)[sx];
        destination[x] = cursor ? nabuGopCursorOver(pixel, destination[x]) : (pixel | 0xff000000);
    }
}
