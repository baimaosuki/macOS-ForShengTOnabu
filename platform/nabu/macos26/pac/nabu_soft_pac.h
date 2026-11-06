// SPDX-License-Identifier: MIT
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// SM8150 (Kryo 485 / Cortex-A76 + Cortex-A55) implements ARMv8.2-A without FEAT_PAuth.
// Non-NOP-space PAC instructions (BLRAA, RETAA, LDRAA, PACGA) and AP*Key*_EL1 system
// registers trap with EC=0x0 (Undefined Instruction) at EL1.
// This header provides the deterministic software PAC & format-7 chained fixup translation
// layer used by Nabu's XNU kernel and host verification tools.

#define NABU_KERNEL_VA_SIGN_BIT   (1ULL << 47)
#define NABU_CANONICAL_VA_MASK    ((1ULL << 48) - 1ULL)
#define NABU_HIGH8_KERNEL_MASK    (0xFFFF000000000000ULL)

struct NabuPacKeyBank {
    uint64_t apia_lo;
    uint64_t apia_hi;
    uint64_t apib_lo;
    uint64_t apib_hi;
    uint64_t apda_lo;
    uint64_t apda_hi;
    uint64_t apdb_lo;
    uint64_t apdb_hi;
    uint64_t apga_lo;
    uint64_t apga_hi;
    uint64_t active_user_jop_key;
    uint64_t active_kernel_rop_key;
    uint64_t el0_entries;
    uint64_t el0_returns;
    uint64_t soft_signs;
};

static inline uintptr_t nabu_strip_ptr(uintptr_t ptr)
{
    if (ptr & NABU_KERNEL_VA_SIGN_BIT) {
        return (uintptr_t)((uint64_t)ptr | NABU_HIGH8_KERNEL_MASK);
    }
    return (uintptr_t)((uint64_t)ptr & NABU_CANONICAL_VA_MASK);
}

static inline uint64_t nabu_mix64(uint64_t x)
{
    x ^= x >> 30;
    x *= 0xbf58476d1ce4e5b9ULL;
    x ^= x >> 27;
    x *= 0x94d049bb133111ebULL;
    x ^= x >> 31;
    return x;
}

// Software replacement for AArch64 `PACGA Xd, Xn, Xm` which writes the 32-bit
// authentication code into bits [63:32] of Xd and zeroes bits [31:0].
static inline uint64_t nabu_pacga_soft(uint64_t ptr, uint64_t modifier, uint64_t key_lo, uint64_t key_hi)
{
    uint64_t h = nabu_mix64(ptr ^ key_lo) ^ nabu_mix64(modifier ^ key_hi);
    return h & 0xFFFFFFFF00000000ULL;
}

static inline void nabu_derive_user_keys(struct NabuPacKeyBank *bank, uint64_t base_key)
{
    if (!bank) {
        return;
    }
    bank->apia_lo = base_key ^ 0x4E41425550414331ULL;
    bank->apia_hi = (base_key >> 7) ^ 0x534D383135304941ULL;
    bank->apib_lo = base_key ^ 0x4E41425550414332ULL;
    bank->apib_hi = (base_key >> 11) ^ 0x534D383135304942ULL;
    bank->apda_lo = base_key ^ 0x4E41425550414333ULL;
    bank->apda_hi = (base_key >> 13) ^ 0x534D383135304441ULL;
    bank->apdb_lo = base_key ^ 0x4E41425550414334ULL;
    bank->apdb_hi = (base_key >> 17) ^ 0x534D383135304442ULL;
    bank->apga_lo = base_key ^ 0x4E41425550414335ULL;
    bank->apga_hi = (base_key >> 19) ^ 0x534D383135304741ULL;
    bank->active_user_jop_key = base_key;
    bank->soft_signs++;
}

// On SM8150 (ARMv8.2-A), pointers dereferenced by standard LDR/BLR/RET must remain
// canonical because the CPU has no hardware address-tag stripping on branch/load.
static inline uintptr_t nabu_sign_ptr_soft(uintptr_t ptr, uint64_t diversity, struct NabuPacKeyBank *bank)
{
    (void)diversity;
    if (bank) {
        bank->soft_signs++;
    }
    return nabu_strip_ptr(ptr);
}

static inline uintptr_t nabu_auth_ptr_soft(uintptr_t ptr, uint64_t diversity, struct NabuPacKeyBank *bank)
{
    (void)diversity;
    (void)bank;
    return nabu_strip_ptr(ptr);
}

// Resolve DYLD_CHAINED_PTR_ARM64E_KERNEL (format 7) fixup entry on SM8150 without PAC instructions.
// Layout when auth == 1 (dyld_chained_ptr_arm64e_auth_rebase):
//   bits [31:0]  = target (32-bit VM offset from __TEXT base)
//   bits [47:32] = diversity
//   bit  [48]    = addrDiv
//   bits [50:49] = key (IA=0, IB=1, DA=2, DB=3)
//   bits [61:51] = next (4-byte stride)
//   bit  [62]    = bind (0)
//   bit  [63]    = auth (1)
// Layout when auth == 0 (dyld_chained_ptr_arm64e_rebase):
//   bits [42:0]  = target (43-bit VM offset from __TEXT base)
//   bits [50:43] = high8
//   bits [61:51] = next (4-byte stride)
//   bit  [62]    = bind (0)
//   bit  [63]    = auth (0)
static inline uintptr_t nabu_fixup_chained_ptr(uint64_t raw64, uintptr_t base_va, struct NabuPacKeyBank *bank)
{
    const bool is_auth = (raw64 >> 63) != 0;
    uintptr_t target = 0;
    if (is_auth) {
        target = (uintptr_t)(raw64 & 0xFFFFFFFFULL) + base_va;
        return nabu_sign_ptr_soft(target, (raw64 >> 32) & 0x7FFFFULL, bank);
    }
    const uint64_t low43 = raw64 & ((1ULL << 43) - 1ULL);
    const uint64_t high8 = (raw64 >> 43) & 0xFFULL;
    target = (uintptr_t)(low43 | (high8 << 56)) + base_va;
    return nabu_strip_ptr(target);
}

#ifdef __cplusplus
}
#endif
