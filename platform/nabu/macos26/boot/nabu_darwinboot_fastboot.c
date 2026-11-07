// SPDX-License-Identifier: BSD-2-Clause-Patent
// Xiaomi Pad 5 (nabu / SM8150) Bare-Metal DarwinBoot Loader for `fastboot boot`
// Unpacks NRAM (Mach-O kernel + ADT + unixnu.toml), stages XNU at 0xc0000000,
// populates DarwinBootArgs (0x480 bytes), and hands off via DarwinEnter.

#include "platform/nabu/macos26/boot/nabu_boot_common.h"

extern void DarwinEnter(uint64_t args_pa, uint64_t entry_pa, uint64_t owned_pa,
                        uint64_t owned_size, uint64_t video_base, uint64_t video_bytes);

#define NABU_OWNED_WINDOW_PA    0xc0000000ULL
// Keep this equal to /chosen/darwinboot-memory-size; the ADT is the single
// source of truth and the strict contract caps it at 8 GiB.
#define NABU_OWNED_WINDOW_SIZE  0x40000000ULL

static inline uint32_t read_be32(const void *p)
{
    const uint8_t *b = (const uint8_t *)p;
    return ((uint32_t)b[0] << 24) | ((uint32_t)b[1] << 16) | ((uint32_t)b[2] << 8) | (uint32_t)b[3];
}

static inline uint32_t read_le32(const void *p)
{
    const uint8_t *b = (const uint8_t *)p;
    return (uint32_t)b[0] | ((uint32_t)b[1] << 8) | ((uint32_t)b[2] << 16) | ((uint32_t)b[3] << 24);
}

static inline uint64_t read_le64(const void *p)
{
    return (uint64_t)read_le32(p) | ((uint64_t)read_le32((const uint8_t *)p + 4) << 32);
}

static bool streq(const char *a, const char *b)
{
    while (*a && *b) {
        if (*a++ != *b++) {
            return false;
        }
    }
    return *a == *b;
}

// Parse Flattened Device Tree (FDT) /chosen/linux,initrd-start
static uint64_t find_initrd_in_fdt(uint64_t fdt_pa)
{
    if (fdt_pa < 0x80000000ULL || fdt_pa >= 0xb9900000ULL) {
        return 0;
    }
    const uint8_t *fdt = (const uint8_t *)(uintptr_t)fdt_pa;
    if (read_be32(fdt) != 0xd00dfeedU) {
        return 0;
    }
    const uint32_t total_size = read_be32(fdt + 4);
    const uint32_t off_struct = read_be32(fdt + 8);
    const uint32_t off_strings = read_be32(fdt + 12);
    if (total_size > (16U << 20) || off_struct >= total_size || off_strings >= total_size) {
        return 0;
    }

    uint32_t pos = off_struct;
    while (pos + 4 <= total_size) {
        const uint32_t token = read_be32(fdt + pos);
        pos += 4;
        if (token == 1U) { // FDT_BEGIN_NODE
            while (pos < total_size && fdt[pos] != 0) {
                pos++;
            }
            pos = (pos + 4U) & ~3U;
        } else if (token == 2U || token == 4U) { // FDT_END_NODE / FDT_NOP
            continue;
        } else if (token == 3U) { // FDT_PROP
            if (pos + 8 > total_size) {
                break;
            }
            const uint32_t len = read_be32(fdt + pos);
            const uint32_t nameoff = read_be32(fdt + pos + 4);
            pos += 8;
            if (off_strings + nameoff < total_size) {
                const char *prop_name = (const char *)(fdt + off_strings + nameoff);
                if (streq(prop_name, "linux,initrd-start")) {
                    if (len == 4U) {
                        return (uint64_t)read_be32(fdt + pos);
                    }
                    if (len == 8U) {
                        return ((uint64_t)read_be32(fdt + pos) << 32) | (uint64_t)read_be32(fdt + pos + 4);
                    }
                }
            }
            pos = (pos + len + 3U) & ~3U;
        } else if (token == 9U) { // FDT_END
            break;
        } else {
            break;
        }
    }
    return 0;
}

static int valid_nram(const uint8_t *p)
{
    if (read_le32(p) != 0x4d41524eU) {
        return 0;
    }
    const uint32_t klen = read_le32(p + 4);
    const uint32_t alen = read_le32(p + 8);
    if (klen < 4096U || klen > (128U << 20) || alen < 64U || alen > (16U << 20)) {
        return 0;
    }
    return read_le32(p + 12) == 0xfeedfacfU;
}

// v3 boot headers carry no load address, so ABL picks where the ramdisk lands.
// Try the FDT hint, then the bytes right behind this loader, then sweep Bank 0
// at 4 KiB granularity (the container can sit anywhere in DDR).
static const uint8_t *locate_nram_container(uint64_t fdt_pa)
{
    const uint64_t initrd_pa = find_initrd_in_fdt(fdt_pa);
    if (initrd_pa >= 0x80000000ULL && initrd_pa < 0xb9900000ULL) {
        const uint8_t *p = (const uint8_t *)(uintptr_t)initrd_pa;
        if (valid_nram(p)) {
            return p;
        }
    }
    const uint64_t self_pa = (uint64_t)(uintptr_t)locate_nram_container;
    for (uint64_t addr = self_pa; addr < self_pa + 0x2000000ULL; addr += 0x1000ULL) {
        const uint8_t *p = (const uint8_t *)(uintptr_t)addr;
        if (valid_nram(p)) {
            return p;
        }
    }
    for (uint64_t addr = 0x80000000ULL; addr < 0xb9900000ULL; addr += 0x1000ULL) {
        const uint8_t *p = (const uint8_t *)(uintptr_t)addr;
        if (valid_nram(p)) {
            return p;
        }
    }
    return NULL;
}

void nabu_darwinboot_main(uint64_t fdt_pa)
{
    const uint32_t bg = 0xFF10141CU;
    const uint32_t fg = 0xFFE8EEF5U;
    const uint32_t accent = 0xFF00D4FFU;
    const uint32_t ok_color = 0xFF00E676U;
    const uint32_t err_color = 0xFFFF5252U;

    nabu_fb_fill_rect(0, 0, NABU_GOP_PANEL_WIDTH, NABU_GOP_PANEL_HEIGHT, bg);
    nabu_fb_fill_rect(0, 0, NABU_GOP_PANEL_WIDTH, 72, 0xFF1E293BU);

    uint32_t y = 20;
    nabu_fb_draw_string(32, y, "DARWINBOOT NABU SM8150 - FASTBOOT RAM LOADER (macOS 26.5 / 25F71)", accent, 0xFF1E293BU, 3);
    y = 100;

    uint64_t cur_el = 0, midr = 0, mpidr = 0, sctlr = 0;
    __asm__ volatile("mrs %0, CurrentEL" : "=r"(cur_el));
    __asm__ volatile("mrs %0, midr_el1" : "=r"(midr));
    __asm__ volatile("mrs %0, mpidr_el1" : "=r"(mpidr));
    __asm__ volatile("mrs %0, sctlr_el1" : "=r"(sctlr));

    uint32_t x = nabu_fb_draw_string(32, y, "EL: ", fg, bg, 3);
    x = nabu_fb_draw_hex64(x, y, cur_el >> 2, ok_color, bg, 3);
    x = nabu_fb_draw_string(x + 24, y, "MIDR: ", fg, bg, 3);
    nabu_fb_draw_hex64(x, y, midr, fg, bg, 3);
    y += 44;

    x = nabu_fb_draw_string(32, y, "MPIDR: ", fg, bg, 3);
    x = nabu_fb_draw_hex64(x, y, mpidr, fg, bg, 3);
    x = nabu_fb_draw_string(x + 24, y, "SCTLR: ", fg, bg, 3);
    nabu_fb_draw_hex64(x, y, sctlr, fg, bg, 3);
    y += 44;

    x = nabu_fb_draw_string(32, y, "FDT PA: ", fg, bg, 3);
    nabu_fb_draw_hex64(x, y, fdt_pa, fg, bg, 3);
    y += 44;

    const uint64_t initrd_hint = find_initrd_in_fdt(fdt_pa);
    x = nabu_fb_draw_string(32, y, "INITRD PA: ", fg, bg, 3);
    nabu_fb_draw_hex64(x, y, initrd_hint, initrd_hint ? ok_color : err_color, bg, 3);
    y += 44;

    const uint8_t *nram = locate_nram_container(fdt_pa);
    if (!nram) {
        nabu_fb_draw_string(32, y, "ERROR: NRAM ramdisk container not found in Bank 0!", err_color, bg, 3);
        return;
    }

    const uint32_t kernel_len = read_le32(nram + 4);
    const uint32_t adt_len = read_le32(nram + 8);
    const uint8_t *kernel_img = nram + 12;
    const uint8_t *adt_img = kernel_img + kernel_len;

    x = nabu_fb_draw_string(32, y, "NRAM PA: ", fg, bg, 3);
    x = nabu_fb_draw_hex64(x, y, (uint64_t)(uintptr_t)nram, ok_color, bg, 3);
    x = nabu_fb_draw_string(x + 24, y, "KERNEL BYTES: ", fg, bg, 3);
    nabu_fb_draw_hex64(x, y, kernel_len, fg, bg, 3);
    y += 44;

    // Parse Mach-O 64-bit header (matches DarwinBoot.c ParseKernel)
    if (read_le32(kernel_img) != 0xfeedfacfU || read_le32(kernel_img + 4) != 0x0100000cU) {
        nabu_fb_draw_string(32, y, "ERROR: Invalid Mach-O 64-bit header in NRAM!", err_color, bg, 3);
        return;
    }

    const uint32_t ncmds = read_le32(kernel_img + 16);
    uint64_t min_va = ~0ULL;
    uint64_t max_va = 0ULL;
    uint64_t entry_va = 0ULL;
    uint32_t cmd_off = 32U;

    for (uint32_t i = 0; i < ncmds; i++) {
        const uint32_t cmd = read_le32(kernel_img + cmd_off);
        const uint32_t cmdsize = read_le32(kernel_img + cmd_off + 4);
        if (cmd == 0x19U && cmdsize >= 72U) { // LC_SEGMENT_64
            const uint64_t vmaddr = read_le64(kernel_img + cmd_off + 24);
            const uint64_t vmsize = read_le64(kernel_img + cmd_off + 32);
            if (vmsize > 0) {
                if (vmaddr < min_va) {
                    min_va = vmaddr;
                }
                if (vmaddr + vmsize > max_va) {
                    max_va = vmaddr + vmsize;
                }
            }
        } else if (cmd == 0x5U && cmdsize >= 288U) { // LC_UNIXTHREAD
            entry_va = read_le64(kernel_img + cmd_off + 272);
        }
        cmd_off += cmdsize;
    }

    const uint64_t kernel_span = (max_va > min_va) ? (max_va - min_va) : 0x40000ULL;
    const uint64_t args_pa = (NABU_OWNED_WINDOW_PA + kernel_span + 0x3FFFULL) & ~0x3FFFULL;
    const uint64_t tree_pa = args_pa + 0x1000ULL;
    const uint64_t used_bytes = ((tree_pa + adt_len + 0x3FFFULL) & ~0x3FFFULL) - NABU_OWNED_WINDOW_PA;

    nabu_memzero((void *)(uintptr_t)NABU_OWNED_WINDOW_PA, (size_t)used_bytes);

    // Copy Mach-O segments into Nabu's 0xc0000000 window
    cmd_off = 32U;
    for (uint32_t i = 0; i < ncmds; i++) {
        const uint32_t cmd = read_le32(kernel_img + cmd_off);
        const uint32_t cmdsize = read_le32(kernel_img + cmd_off + 4);
        if (cmd == 0x19U && cmdsize >= 72U) {
            const uint64_t vmaddr = read_le64(kernel_img + cmd_off + 24);
            const uint64_t fileoff = read_le64(kernel_img + cmd_off + 40);
            const uint64_t filesize = read_le64(kernel_img + cmd_off + 48);
            if (filesize > 0 && fileoff + filesize <= kernel_len) {
                nabu_memcpy((void *)(uintptr_t)(NABU_OWNED_WINDOW_PA + (vmaddr - min_va)),
                            kernel_img + fileoff, (size_t)filesize);
            }
        }
        cmd_off += cmdsize;
    }

    // Populate DarwinBootArgs (0x480 bytes) at args_pa
    struct DarwinBootArgs *args = (struct DarwinBootArgs *)(uintptr_t)args_pa;
    nabu_memzero(args, sizeof(*args));
    args->Revision = 2;
    args->Version = 2;
    args->VirtualBase = min_va;
    args->PhysicalBase = NABU_OWNED_WINDOW_PA;
    args->MemorySize = NABU_OWNED_WINDOW_SIZE;
    args->MemorySizeActual = 0x140000000ULL;
    args->TopOfKernelData = NABU_OWNED_WINDOW_PA + used_bytes;
    args->Video[0] = NABU_GOP_PHYS_BASE;
    args->Video[1] = 1;
    args->Video[2] = NABU_GOP_PANEL_STRIDE;
    args->Video[3] = NABU_GOP_PANEL_WIDTH;
    args->Video[4] = NABU_GOP_PANEL_HEIGHT;
    args->Video[5] = NABU_GOP_PANEL_BPP;
    args->DeviceTree = min_va + (tree_pa - NABU_OWNED_WINDOW_PA);
    args->DeviceTreeLength = adt_len;
    // Single-core controlled boot: the generated ADT advertises exactly one
    // cpu@0, so cpus=8 would wait forever for secondaries that are absent.
    // Storage stays off until the UFS driver is qualified for this board.
    const char *cmdline = "-v debug=0x14e keepsyms=1 cpus=1 armcpu-self-ipi=1 nabu-vc-scale=2";
    for (size_t i = 0; cmdline[i] && i < sizeof(args->CommandLine) - 1; i++) {
        args->CommandLine[i] = cmdline[i];
    }

    nabu_memcpy((void *)(uintptr_t)tree_pa, adt_img, adt_len);

    const uint64_t entry_pa = NABU_OWNED_WINDOW_PA + (entry_va - min_va);
    x = nabu_fb_draw_string(32, y, "HANDOFF -> ENTRY PA: ", fg, bg, 3);
    x = nabu_fb_draw_hex64(x, y, entry_pa, ok_color, bg, 3);
    x = nabu_fb_draw_string(x + 24, y, "ARGS PA: ", fg, bg, 3);
    nabu_fb_draw_hex64(x, y, args_pa, ok_color, bg, 3);

    DarwinEnter(args_pa, entry_pa, NABU_OWNED_WINDOW_PA, used_bytes,
                NABU_GOP_PHYS_BASE, (uint64_t)NABU_GOP_PANEL_STRIDE * NABU_GOP_PANEL_HEIGHT);
}
