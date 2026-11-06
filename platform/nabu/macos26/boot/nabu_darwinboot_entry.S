// SPDX-License-Identifier: BSD-2-Clause-Patent
// Xiaomi Pad 5 (nabu / SM8150) ARM64 Linux Image Header + DarwinBoot EL1 Handoff
// Booted directly in RAM by stock Xiaomi ABL via `fastboot boot nabu-macos26-kernel.img`.

    .section .head.text, "ax"
    .globl _start
_start:
    // Standard 64-byte ARM64 Linux Kernel Image Header
    b       nabu_el1_entry              // 0x00: code0
    .long   0                           // 0x04: code1
    .quad   0x00008000                  // 0x08: text_offset (32 KiB)
    .quad   0x00080000                  // 0x10: image_size  (512 KiB)
    .quad   0x00000002                  // 0x18: flags (LE, 4K page size)
    .quad   0                           // 0x20: res2
    .quad   0                           // 0x28: res3
    .quad   0                           // 0x30: res4
    .long   0x644d5241                  // 0x38: magic ("ARM\x64")
    .long   0                           // 0x3c: pe_offset

nabu_el1_entry:
    msr     daifset, #15                // Mask D, A, I, F interrupts
    mov     x19, x0                     // Preserve FDT physical address from ABL in x19
    adrp    x1, __stack_top
    add     x1, x1, :lo12:__stack_top
    bic     sp, x1, #15
    mov     x0, x19
    bl      nabu_darwinboot_main
1:
    wfe
    b       1b

// Adapted from UniXNU/DarwinBoot/DarwinBootPkg/Driver/Entry.S:
// x0 = boot_args PA, x1 = entry PA, x2 = owned PA, x3 = owned size,
// x4 = framebuffer PA (0 for none), x5 = framebuffer size in bytes.
    .globl DarwinEnter
    .type DarwinEnter, %function
DarwinEnter:
    msr     daifset, #15
    // Breadcrumb: draw a green bar (BGRA 0xFF00FF00) over the top 1/32 of the
    // 1600x2560 framebuffer to prove DarwinEnter executed before jumping to XNU.
    cbz     x4, 2f
    lsr     x9, x5, #5
    bic     x9, x9, #7
    mov     w10, #0xFF00
    movk    w10, #0xFF00, lsl #16
    mov     x11, x4
3:
    stp     w10, w10, [x11], #8
    subs    x9, x9, #8
    b.hi    3b
    dsb     sy
2:
    add     x5, x2, x3
    mrs     x6, ctr_el0
    ubfx    x6, x6, #16, #4
    mov     x7, #4
    lsl     x6, x7, x6
    sub     x7, x6, #1
    bic     x4, x2, x7
    dsb     sy
1:
    dc      civac, x4
    add     x4, x4, x6
    cmp     x4, x5
    b.lo    1b
    dsb     sy
    ic      iallu
    dsb     sy
    isb
    mrs     x4, sctlr_el1
    bic     x4, x4, #1
    bic     x4, x4, #4
    bic     x4, x4, #0x1000
    msr     sctlr_el1, x4
    isb
    tlbi    vmalle1
    dsb     sy
    isb
    br      x1

    .section .bss, "aw", %nobits
    .balign 16
    .space 65536
    .globl __stack_top
__stack_top:
