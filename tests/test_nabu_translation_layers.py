#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for all Xiaomi Pad 5 (nabu / SM8150) macOS 26.5 translation layers."""
from __future__ import annotations

from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from nabu_pac_strip import (
    AARCH64_ERET,
    AARCH64_NOP,
    AARCH64_RET,
    strip_relocation_auth,
    translate_arm64e_insn,
    translate_macho_for_nabu,
)
from nabu_vm_bridge import nabu_vm_bridge_contract, split_16k_page_to_4k_smmu
from prepare_nabu_gop import nabu_gop_contract


def _build_synthetic_arm64e_macho() -> bytes:
    """Construct a minimal valid arm64e MH_EXECUTE with __TEXT_EXEC PAC instructions and format-7 chained fixups."""
    page_size = 4096
    text_va = 0xFFFFFF8000000000
    data_va = text_va + page_size

    # 2 LC_SEGMENT_64 commands (152 bytes for __TEXT with 1 section, 72 bytes for __DATA)
    # + 1 LC_DYLD_CHAINED_FIXUPS command (16 bytes) = 240 bytes of load commands
    sizeofcmds = 152 + 72 + 16
    header = struct.pack(
        '<8I',
        0xFEEDFACF,      # MH_MAGIC_64
        0x0100000C,      # CPU_TYPE_ARM64
        0x80000002,      # CPU_SUBTYPE_ARM64E
        0x2,             # MH_EXECUTE
        3,               # ncmds
        sizeofcmds,
        0,               # flags
        0,               # reserved
    )

    # Instructions placed at offset 0x400 inside __TEXT
    code_offset = 0x400
    insns = [
        0xD73F0908,  # BLRAA X8, X8   -> BLR X8 (0xD63F0100)
        0xD63F091F,  # BLRAAZ X8      -> BLR X8 (0xD63F0100)
        0xD71F0929,  # BRAA X9, X9    -> BR X9  (0xD61F0120)
        0xD65F0BFF,  # RETAA          -> RET    (0xD65F03C0)
        0xD69F0BFF,  # ERETAA         -> ERET   (0xD69F03E0)
        0xF820252A,  # LDRAA X10, [X9, #16] -> LDR X10, [X9, #16] (0xF940092A)
        0xDAC10128,  # PACIA X8, X9   -> NOP    (0xD503201F)
        0xDAC143E8,  # XPACI X8       -> NOP    (0xD503201F)
        0x9AC93107,  # PACGA X7, X8, X9 -> EOR X7, X8, X9 (0xCA090107)
    ]
    code_bytes = struct.pack(f'<{len(insns)}I', *insns)

    text_seg = struct.pack(
        '<II16s4Q4I',
        0x19, 152, b'__TEXT'.ljust(16, b'\0'),
        text_va, page_size, 0, page_size,
        5, 5, 1, 0,
    )
    text_sec = struct.pack(
        '<16s16sQQIIIIIIII',
        b'__text'.ljust(16, b'\0'),
        b'__TEXT'.ljust(16, b'\0'),
        text_va + code_offset, len(code_bytes), code_offset,
        2, 0, 0, 0x80000400, 0, 0, 0,
    )
    data_seg = struct.pack(
        '<II16s4Q4I',
        0x19, 72, b'__DATA'.ljust(16, b'\0'),
        data_va, page_size, page_size, page_size,
        3, 3, 0, 0,
    )

    # Format-7 chained fixup metadata placed right after __DATA (at file offset 2 * page_size)
    fixup_off = 2 * page_size
    # dyld_chained_fixups_header (28 bytes) + seg_count(1) + seg_info_offset[0, 1] (12 bytes) = 40 bytes
    # dyld_chained_starts_in_segment for __DATA (24 bytes, 1 page, page_start[0] = 0x10)
    starts_offset = 28
    seg1_rel = 12
    seg1_starts = struct.pack('<IHHQIH', 24, page_size, 7, page_size, 0, 1) + struct.pack('<H', 0x10)
    starts_table = struct.pack('<3I', 2, 0, seg1_rel) + seg1_starts
    fixup_blob = struct.pack('<7I', 0, starts_offset, 0, 0, 0, 0, 0) + starts_table

    fixup_cmd = struct.pack('<4I', 0x80000034, 16, fixup_off, len(fixup_blob))

    image = bytearray(fixup_off + len(fixup_blob))
    image[:len(header)] = header
    pos = len(header)
    for chunk in (text_seg, text_sec, data_seg, fixup_cmd):
        image[pos:pos + len(chunk)] = chunk
        pos += len(chunk)

    image[code_offset:code_offset + len(code_bytes)] = code_bytes

    # Write 2 chained pointers in __DATA at offset +0x10 and +0x18 (next=2 -> +8 bytes)
    # First pointer: auth=1, next=2, target=0x400 (__text start)
    auth_ptr1 = (1 << 63) | (2 << 51) | (0x1234 << 32) | 0x400
    # Second pointer: auth=1, next=0, target=0x404
    auth_ptr2 = (1 << 63) | (0 << 51) | (0x5678 << 32) | 0x404
    struct.pack_into('<Q', image, page_size + 0x10, auth_ptr1)
    struct.pack_into('<Q', image, page_size + 0x18, auth_ptr2)
    image[fixup_off:fixup_off + len(fixup_blob)] = fixup_blob
    return bytes(image)


class NabuTranslationLayersTest(unittest.TestCase):
    def test_arm64e_instruction_rewriter_covers_all_non_nop_pac_opcodes(self) -> None:
        self.assertEqual(translate_arm64e_insn(0xD65F0BFF), (AARCH64_RET, 'RETAA/RETAB->RET'))
        self.assertEqual(translate_arm64e_insn(0xD65F0FFF), (AARCH64_RET, 'RETAA/RETAB->RET'))
        self.assertEqual(translate_arm64e_insn(0xD69F0BFF), (AARCH64_ERET, 'ERETAA/ERETAB->ERET'))
        self.assertEqual(translate_arm64e_insn(0xD73F0908), (0xD63F0100, 'BLRAA/BLRAB->BLR'))
        self.assertEqual(translate_arm64e_insn(0xD63F091F), (0xD63F0100, 'BLRAAZ/BLRABZ->BLR'))
        self.assertEqual(translate_arm64e_insn(0xD71F0929), (0xD61F0120, 'BRAA/BRAB->BR'))
        self.assertEqual(translate_arm64e_insn(0xD61F093F), (0xD61F0120, 'BRAAZ/BRABZ->BR'))
        self.assertEqual(translate_arm64e_insn(0xF820252A), (0xF940092A, 'LDRAA/LDRAB->LDR'))
        self.assertEqual(translate_arm64e_insn(0xDAC10128), (AARCH64_NOP, 'PAC/AUT/XPAC->NOP'))
        self.assertEqual(translate_arm64e_insn(0xDAC143E8), (AARCH64_NOP, 'PAC/AUT/XPAC->NOP'))
        self.assertEqual(translate_arm64e_insn(0x9AC93107), (0xCA090107, 'PACGA->EOR'))

    def test_macho_pac_translator_rewrites_code_and_format7_auth_fixups(self) -> None:
        raw = _build_synthetic_arm64e_macho()
        rewritten, report = translate_macho_for_nabu(raw, convert_auth_fixups=True, downgrade_subtype=True)
        self.assertEqual(report['original_architecture'], 'arm64e')
        self.assertEqual(report['final_architecture'], 'arm64')
        self.assertEqual(report['instructions_rewritten'], 9)
        self.assertEqual(report['chained_fixups']['auth_converted'], 2)

        # Verify the rewritten instructions at 0x400
        words = struct.unpack_from('<9I', rewritten, 0x400)
        self.assertEqual(
            words,
            (
                0xD63F0100,  # BLR X8
                0xD63F0100,  # BLR X8
                0xD61F0120,  # BR X9
                AARCH64_RET,
                AARCH64_ERET,
                0xF940092A,  # LDR X10, [X9, #16]
                AARCH64_NOP,
                AARCH64_NOP,
                0xCA090107,  # EOR X7, X8, X9
            ),
        )

        # Verify both format-7 pointers in __DATA now have auth=0 and preserve next + target
        ptr1, ptr2 = struct.unpack_from('<2Q', rewritten, 4096 + 0x10)
        self.assertEqual(ptr1 >> 63, 0)
        self.assertEqual((ptr1 >> 51) & 0x7FF, 2)
        self.assertEqual(ptr1 & 0xFFFFFFFF, 0x400)
        self.assertEqual(ptr2 >> 63, 0)
        self.assertEqual((ptr2 >> 51) & 0x7FF, 0)
        self.assertEqual(ptr2 & 0xFFFFFFFF, 0x404)

    def test_strip_relocation_auth_helper(self) -> None:
        relocs = [
            {'site': 0x1000, 'target': 0x2000, 'authenticated': True, 'key': 0, 'diversity': 12},
            {'site': 0x1008, 'target': 0x2008, 'authenticated': False},
        ]
        stripped = strip_relocation_auth(relocs)
        self.assertFalse(stripped[0]['authenticated'])
        self.assertTrue(stripped[0]['nabu_stripped_auth'])
        self.assertEqual(stripped[0]['high8'], 0xFF)

    def test_nabu_soft_pac_c_header_and_patch(self) -> None:
        patch_path = ROOT / 'platform' / 'nabu' / 'macos26' / 'pac' / '0001-nabu-armv82-soft-pac.patch'
        patch_text = patch_path.read_text(encoding='utf-8')
        self.assertIn('-march=armv8.2-a', patch_text)
        self.assertIn('-DHAS_NABU_SOFT_PAC=1', patch_text)
        self.assertIn('SHENG_DERIVE_USER_KEY', patch_text)

        harness = r'''
        #include <assert.h>
        #include "platform/nabu/macos26/pac/nabu_soft_pac.h"
        int main(void) {
            struct NabuPacKeyBank bank = {0};
            nabu_derive_user_keys(&bank, 0x1122334455667788ULL);
            assert(bank.active_user_jop_key == 0x1122334455667788ULL);
            assert(bank.soft_signs == 1);

            uintptr_t tagged_kptr = (uintptr_t)0xAB80FFFFFF801234ULL;
            assert(nabu_strip_ptr(tagged_kptr) == (uintptr_t)0xFFFFFFFFFF801234ULL);

            uintptr_t tagged_uptr = (uintptr_t)0x4500000100203040ULL;
            assert(nabu_strip_ptr(tagged_uptr) == (uintptr_t)0x0000000100203040ULL);

            uint64_t ga = nabu_pacga_soft(0x1000ULL, 0x2000ULL, bank.apga_lo, bank.apga_hi);
            assert((ga & 0xFFFFFFFFULL) == 0ULL);
            assert((ga >> 32) != 0ULL);

            uintptr_t base_va = (uintptr_t)0xFFFFFF8000200000ULL;
            uint64_t auth_raw = (1ULL << 63) | (4ULL << 51) | 0x1400ULL;
            uintptr_t fixed_auth = nabu_fixup_chained_ptr(auth_raw, base_va, &bank);
            assert(fixed_auth == base_va + 0x1400ULL);

            uint64_t non_auth_raw = (4ULL << 51) | (0xFFULL << 43) | 0x2800ULL;
            uintptr_t fixed_non_auth = nabu_fixup_chained_ptr(non_auth_raw, base_va, &bank);
            assert(fixed_non_auth == base_va + 0x2800ULL);
            return 0;
        }
        '''
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'test_soft_pac.c'
            exe = Path(tmp) / 'test_soft_pac'
            src.write_text(harness, encoding='utf-8')
            subprocess.run(['gcc', '-std=c11', '-O2', '-I', str(ROOT), str(src), '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True)

    def test_nabu_gop_scanout_rotation_and_contract(self) -> None:
        landscape = nabu_gop_contract(orientation='landscape')
        self.assertEqual(landscape['framebuffer_phys_base'], '0x9c000000')
        self.assertEqual(landscape['panel_width'], 1600)
        self.assertEqual(landscape['panel_height'], 2560)
        self.assertEqual(landscape['desktop_width'], 2560)
        self.assertEqual(landscape['desktop_height'], 1600)
        self.assertEqual(landscape['rotation_degrees'], 90)

        harness = r'''
        #include <assert.h>
        #include "platform/nabu/macos26/display/nabu_gop_scanout.h"
        int main(void) {
            assert(nabuGopFramebufferValid(NABU_GOP_PHYS_BASE, NABU_GOP_CARVEOUT_SIZE, 1600, 2560, 6400));
            assert(!nabuGopFramebufferValid(0xb9900000ULL, NABU_GOP_CARVEOUT_SIZE, 1600, 2560, 6400));
            assert(!nabuGopFramebufferValid(0xc0000000ULL, NABU_GOP_CARVEOUT_SIZE, 1600, 2560, 6400));

            // 4x2 landscape source rotated 90 deg CW into 2x4 portrait panel
            const uint32_t src[2 * 4] = {
                0xFFAA0001U, 0xFFAA0002U, 0xFFAA0003U, 0xFFAA0004U,
                0xFFBB0001U, 0xFFBB0002U, 0xFFBB0003U, 0xFFBB0004U
            };
            uint32_t dst[4 * 2] = {0};
            nabuGopScanoutFrameRotated(dst, 2, 4, 2 * 4, src, 4, 2, 4, NULL, kNabuGopRotationLandscape90);
            // At py=0, px=1 (top-right of portrait panel), lx=0, ly=0 -> src[0]
            assert(dst[0 * 2 + 1] == 0xFFAA0001U);
            // At py=0, px=0 (top-left of portrait panel), lx=0, ly=1 -> src[4]
            assert(dst[0 * 2 + 0] == 0xFFBB0001U);
            return 0;
        }
        '''
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'test_gop.c'
            exe = Path(tmp) / 'test_gop'
            src.write_text(harness, encoding='utf-8')
            subprocess.run(['gcc', '-std=c11', '-O2', '-I', str(ROOT), str(src), '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True)

    def test_nabu_4k_smmu_16k_xnu_page_bridge(self) -> None:
        contract = nabu_vm_bridge_contract()
        self.assertEqual(contract['xnu_page_size'], 16384)
        self.assertEqual(contract['smmu_page_size'], 4096)
        self.assertEqual(contract['subpages_per_xnu_page'], 4)

        subpages = split_16k_page_to_4k_smmu(0xC0040000, 0xD0040000, stream_id=0x300)
        self.assertEqual(len(subpages), 4)
        self.assertEqual(subpages[0]['phys'], 0xC0040000)
        self.assertEqual(subpages[3]['phys'], 0xC0043000)
        self.assertEqual(subpages[3]['iova'], 0xD0043000)

        with self.assertRaises(ValueError):
            split_16k_page_to_4k_smmu(0xB9900000)  # Inside SM8150 TrustZone hole

        harness = r'''
        #include <assert.h>
        #include "platform/nabu/macos26/vm/nabu_page_bridge.h"
        int main(void) {
            struct NabuSmmuSubpage subs[4];
            assert(nabuSplit16kTo4kSubpages(0xc0004000ULL, 0xc0004000ULL, 0x300, subs));
            assert(subs[0].phys == 0xc0004000ULL && subs[0].bytes == 4096U);
            assert(subs[3].phys == 0xc0007000ULL && subs[3].subpage_index == 3U);
            // Reject TrustZone hole [0xb9900000, 0xc0000000)
            assert(!nabuSplit16kTo4kSubpages(0xb9900000ULL, 0xb9900000ULL, 0x300, subs));
            // Allow Bank 0 GOP carveout when allowBank0Carveout == true
            assert(nabuDmaRangeSafeForSmmu(0x9c000000ULL, 0x01000000ULL, true));
            return 0;
        }
        '''
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'test_vm_bridge.c'
            exe = Path(tmp) / 'test_vm_bridge'
            src.write_text(harness, encoding='utf-8')
            subprocess.run(['gcc', '-std=c11', '-O2', '-I', str(ROOT), str(src), '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True)

    def test_build_nabu_fastboot_boot_image_and_unixnu_integrations(self) -> None:
        from build_nabu_fastboot_image import build_nabu_fastboot_boot_suite

        patches_dir = ROOT / 'platform' / 'nabu' / 'macos26' / 'patches'
        for patch_name in (
            'nabu-armcpu-timer-intid19.patch',
            'nabu-managed-rt-normal.patch',
            'nabu-vc-text-scale.patch',
            'nabu-native-console.patch',
            'nabu-union-lower-directory.patch',
            'nabu-union-getattrlistbulk.patch',
            'nabu-master-second-trust-cache.patch',
        ):
            self.assertTrue((patches_dir / patch_name).exists(), f'Missing patch {patch_name}')

        with tempfile.TemporaryDirectory() as tmp:
            summary = build_nabu_fastboot_boot_suite(out_dir=Path(tmp) / 'nabu-fastboot')
            self.assertEqual(summary['board'], 'nabu')
            self.assertEqual(summary['soc'], 'SM8150')
            self.assertTrue(summary['temporary_ram_boot'])
            self.assertFalse(summary['partition_images_written'])
            ver = summary['verification']
            self.assertEqual(ver['header_magic'], 'ANDROID!')
            self.assertEqual(ver['darwinboot_arm64_magic'], 'ARM\\x64')
            self.assertTrue(ver['fdt_has_qcom_board_id'])
            self.assertTrue(ver['fdt_has_qcom_msm_id'])
            self.assertEqual(ver['nram_magic'], 'NRAM')
            self.assertEqual(ver['kernel_macho_magic'], '0xfeedfacf')


if __name__ == '__main__':
    unittest.main()

