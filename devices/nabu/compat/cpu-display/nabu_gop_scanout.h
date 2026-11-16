// SPDX-License-Identifier: MIT
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// Xiaomi Pad 5 (nabu / SM8150) UEFI GOP Framebuffer & Landscape/Portrait Scanout Translation Layer
// Hardware GOP carveout initialized by stock ABL:
//   Physical Base : 0x9c000000 (DRAM Bank 0, outside XNU managed DRAM 0xc0000000..0x200000000)
//   Carveout Size : 0x01000000 (16 MiB)
//   Panel Scanout : 1600 x 2560 (Portrait dual-DSI), 32bpp BGRA8888, rowBytes = 6400
//   Active Bytes  : 1600 * 2560 * 4 = 16,384,000 (0x00fa0000 <= 0x01000000)

#define NABU_GOP_PHYS_BASE        0x9c000000ULL
#define NABU_GOP_CARVEOUT_SIZE    0x01000000ULL
#define NABU_GOP_PANEL_WIDTH      1600U
#define NABU_GOP_PANEL_HEIGHT     2560U
#define NABU_GOP_PANEL_BPP        32U
#define NABU_GOP_PANEL_STRIDE     6400U
#define NABU_GOP_ACTIVE_BYTES     (NABU_GOP_PANEL_STRIDE * NABU_GOP_PANEL_HEIGHT)

#define NABU_BANK0_BASE           0x80000000ULL
#define NABU_TZ_HOLE_BASE         0xb9900000ULL
#define NABU_XNU_DRAM_BASE        0xc0000000ULL

enum NabuGopRotation {
    kNabuGopRotationPortrait0    = 0,   // 1600x2560 source -> 1600x2560 panel
    kNabuGopRotationLandscape90  = 90,  // 2560x1600 landscape source -> 1600x2560 panel (CW)
    kNabuGopRotationLandscape270 = 270, // 2560x1600 landscape source -> 1600x2560 panel (CCW)
};

struct NabuGopGeometry {
    uint64_t phys_base;
    uint64_t carveout_size;
    uint32_t panel_width;
    uint32_t panel_height;
    uint32_t panel_stride;
    uint32_t desktop_width;
    uint32_t desktop_height;
    uint32_t rotation_degrees;
};

static inline bool nabuGopFramebufferValid(uint64_t physBase, uint64_t byteCount,
                                           uint32_t width, uint32_t height, uint32_t stride)
{
    if (width != NABU_GOP_PANEL_WIDTH || height != NABU_GOP_PANEL_HEIGHT) {
        return false;
    }
    if (stride < width * 4U) {
        return false;
    }
    const uint64_t active = (uint64_t)stride * (uint64_t)height;
    if (byteCount < active || byteCount > NABU_GOP_CARVEOUT_SIZE) {
        return false;
    }
    if (physBase < NABU_BANK0_BASE || physBase + byteCount > NABU_TZ_HOLE_BASE) {
        return false;
    }
    if (physBase + byteCount > NABU_XNU_DRAM_BASE) {
        return false;
    }
    return true;
}

static inline struct NabuGopGeometry nabuDefaultGopGeometry(bool landscapeDesktop)
{
    struct NabuGopGeometry g;
    g.phys_base = NABU_GOP_PHYS_BASE;
    g.carveout_size = NABU_GOP_CARVEOUT_SIZE;
    g.panel_width = NABU_GOP_PANEL_WIDTH;
    g.panel_height = NABU_GOP_PANEL_HEIGHT;
    g.panel_stride = NABU_GOP_PANEL_STRIDE;
    if (landscapeDesktop) {
        g.desktop_width = NABU_GOP_PANEL_HEIGHT;  // 2560
        g.desktop_height = NABU_GOP_PANEL_WIDTH;  // 1600
        g.rotation_degrees = kNabuGopRotationLandscape90;
    } else {
        g.desktop_width = NABU_GOP_PANEL_WIDTH;   // 1600
        g.desktop_height = NABU_GOP_PANEL_HEIGHT; // 2560
        g.rotation_degrees = kNabuGopRotationPortrait0;
    }
    return g;
}

static inline uint32_t nabuConvertPixel(const void *sourcePixels, uint64_t pixelIndex, const uint8_t *halfToByte)
{
    if (halfToByte) {
        const uint16_t *rgba = (const uint16_t *)sourcePixels + pixelIndex * 4ULL;
        return 0xff000000U | ((uint32_t)halfToByte[rgba[0]] << 16) |
               ((uint32_t)halfToByte[rgba[1]] << 8) | (uint32_t)halfToByte[rgba[2]];
    }
    return ((const uint32_t *)sourcePixels)[pixelIndex];
}

// Portrait 1:1 or scaled row scanout (matches Sheng's nativeGopScanoutRow contract)
static inline void nabuGopScanoutRow(volatile uint32_t *destination, uint64_t destinationWidth,
                                     const uint16_t *source, uint64_t sourceWidth,
                                     const uint8_t *halfToByte)
{
    uint64_t sourceX = 0, remainder = 0;
    const uint64_t step = sourceWidth / destinationWidth;
    const uint64_t fraction = sourceWidth % destinationWidth;
    for (uint64_t x = 0; x < destinationWidth; x++) {
        destination[x] = nabuConvertPixel(source, sourceX, halfToByte);
        sourceX += step;
        remainder += fraction;
        if (remainder >= destinationWidth) {
            sourceX++;
            remainder -= destinationWidth;
        }
    }
}

// Landscape (e.g. 2560x1600) IOSurface -> Portrait (1600x2560) Nabu DSI GOP scanout translator.
// Maps logical landscape coordinate (lx, ly) in [0..srcWidth) x [0..srcHeight) onto physical
// portrait scanout coordinate (px, py) in [0..dstWidth) x [0..dstHeight):
//   - 90 deg CW  : px = dstWidth - 1 - scaled(ly), py = scaled(lx)
//   - 270 deg CCW: px = scaled(ly),                py = dstHeight - 1 - scaled(lx)
static inline void nabuGopScanoutFrameRotated(volatile uint32_t *dstFramebuffer,
                                              uint32_t dstWidth, uint32_t dstHeight, uint32_t dstStrideBytes,
                                              const void *srcSurface,
                                              uint32_t srcWidth, uint32_t srcHeight, uint32_t srcStridePixels,
                                              const uint8_t *halfToByte,
                                              uint32_t rotationDegrees)
{
    if (!dstFramebuffer || !srcSurface || !dstWidth || !dstHeight || !srcWidth || !srcHeight) {
        return;
    }
    const uint32_t dstStridePixels = dstStrideBytes / 4U;
    if (rotationDegrees == kNabuGopRotationPortrait0) {
        for (uint32_t py = 0; py < dstHeight; py++) {
            const uint32_t sy = (uint32_t)(((uint64_t)py * srcHeight) / dstHeight);
            const uint16_t *rowPtr = halfToByte
                ? ((const uint16_t *)srcSurface + (uint64_t)sy * srcStridePixels * 4ULL)
                : (const uint16_t *)((const uint32_t *)srcSurface + (uint64_t)sy * srcStridePixels);
            nabuGopScanoutRow(dstFramebuffer + (uint64_t)py * dstStridePixels,
                              dstWidth, rowPtr, srcWidth, halfToByte);
        }
        return;
    }

    for (uint32_t py = 0; py < dstHeight; py++) {
        volatile uint32_t *dstRow = dstFramebuffer + (uint64_t)py * dstStridePixels;
        for (uint32_t px = 0; px < dstWidth; px++) {
            uint32_t lx = 0;
            uint32_t ly = 0;
            if (rotationDegrees == kNabuGopRotationLandscape270) {
                lx = (uint32_t)(((uint64_t)(dstHeight - 1U - py) * srcWidth) / dstHeight);
                ly = (uint32_t)(((uint64_t)px * srcHeight) / dstWidth);
            } else {
                // Default 90 deg CW: top-left of landscape is top-right of portrait panel
                lx = (uint32_t)(((uint64_t)py * srcWidth) / dstHeight);
                ly = (uint32_t)(((uint64_t)(dstWidth - 1U - px) * srcHeight) / dstWidth);
            }
            const uint64_t srcIdx = (uint64_t)ly * srcStridePixels + lx;
            dstRow[px] = nabuConvertPixel(srcSurface, srcIdx, halfToByte);
        }
    }
}
