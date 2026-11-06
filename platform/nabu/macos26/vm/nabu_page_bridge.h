// SPDX-License-Identifier: MIT
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// Xiaomi Pad 5 (nabu / SM8150) 16 KiB XNU VM <-> 4 KiB SMMUv2 / UFS / Adreno DMA Bridge
// Apple macOS 26.5 arm64e kernel & userspace binaries use a 16 KiB (0x4000) VM page granule
// (__ARM_16K_PG__ = 1), whereas SM8150's Qualcomm SMMUv2 (0x15000000, stream 0x300 for UFS)
// and UFS UFSHCI PRDT descriptors operate on 4 KiB (0x1000) hardware granules.

#define NABU_XNU_PAGE_SHIFT       14U
#define NABU_XNU_PAGE_SIZE        (1ULL << NABU_XNU_PAGE_SHIFT) // 16384 (0x4000)
#define NABU_XNU_PAGE_MASK        (NABU_XNU_PAGE_SIZE - 1ULL)

#define NABU_HW_PAGE_SHIFT        12U
#define NABU_HW_PAGE_SIZE         (1ULL << NABU_HW_PAGE_SHIFT)  // 4096  (0x1000)
#define NABU_HW_PAGE_MASK         (NABU_HW_PAGE_SIZE - 1ULL)
#define NABU_SUBPAGES_PER_XNU_PG  (NABU_XNU_PAGE_SIZE / NABU_HW_PAGE_SIZE) // 4

#define NABU_DRAM_BANK0_START     0x80000000ULL
#define NABU_DRAM_BANK0_SAFE_END  0xb9900000ULL
#define NABU_TZ_HOLE_START        0xb9900000ULL
#define NABU_TZ_HOLE_END          0xc0000000ULL
#define NABU_XNU_DRAM_START       0xc0000000ULL
#define NABU_XNU_DRAM_END         0x200000000ULL

struct NabuSmmuSubpage {
    uint64_t iova;
    uint64_t phys;
    uint32_t bytes;
    uint16_t stream_id;
    uint16_t subpage_index;
};

static inline uint64_t nabuAlignUp16k(uint64_t value)
{
    return (value + NABU_XNU_PAGE_MASK) & ~NABU_XNU_PAGE_MASK;
}

static inline uint64_t nabuAlignDown16k(uint64_t value)
{
    return value & ~NABU_XNU_PAGE_MASK;
}

static inline bool nabuPhysRangeTouchesTrustZoneHole(uint64_t physAddr, uint64_t byteLength)
{
    if (!byteLength) {
        return false;
    }
    const uint64_t end = physAddr + byteLength;
    if (end < physAddr) {
        return true;
    }
    return (physAddr < NABU_TZ_HOLE_END) && (end > NABU_TZ_HOLE_START);
}

static inline bool nabuDmaRangeSafeForSmmu(uint64_t physAddr, uint64_t byteLength, bool allowBank0Carveout)
{
    if (!byteLength || (physAddr & NABU_HW_PAGE_MASK) != 0 || (byteLength & NABU_HW_PAGE_MASK) != 0) {
        return false;
    }
    const uint64_t end = physAddr + byteLength;
    if (end < physAddr || nabuPhysRangeTouchesTrustZoneHole(physAddr, byteLength)) {
        return false;
    }
    if (physAddr >= NABU_XNU_DRAM_START && end <= NABU_XNU_DRAM_END) {
        return true;
    }
    if (allowBank0Carveout && physAddr >= NABU_DRAM_BANK0_START && end <= NABU_DRAM_BANK0_SAFE_END) {
        return true;
    }
    return false;
}

// Split a single 16 KiB XNU VM page into 4 contiguous 4 KiB SMMUv2 / UFS DMA subpages.
static inline bool nabuSplit16kTo4kSubpages(uint64_t xnuPagePhys, uint64_t xnuPageIova,
                                            uint16_t streamId,
                                            struct NabuSmmuSubpage outSubpages[NABU_SUBPAGES_PER_XNU_PG])
{
    if (!outSubpages || (xnuPagePhys & NABU_XNU_PAGE_MASK) != 0 || (xnuPageIova & NABU_XNU_PAGE_MASK) != 0) {
        return false;
    }
    if (!nabuDmaRangeSafeForSmmu(xnuPagePhys, NABU_XNU_PAGE_SIZE, false)) {
        return false;
    }
    for (uint16_t i = 0; i < NABU_SUBPAGES_PER_XNU_PG; i++) {
        const uint64_t offset = (uint64_t)i * NABU_HW_PAGE_SIZE;
        outSubpages[i].iova = xnuPageIova + offset;
        outSubpages[i].phys = xnuPagePhys + offset;
        outSubpages[i].bytes = (uint32_t)NABU_HW_PAGE_SIZE;
        outSubpages[i].stream_id = streamId;
        outSubpages[i].subpage_index = i;
    }
    return true;
}
