#!/usr/bin/env python3
"""Exercise macOS FAT/CMS adaptation, entitlement preservation and page hashes."""
import hashlib
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import nabu_zeta_pac as pac


def fixture():
    identifier = b'org.unixnu.pac-fixture\0'
    entitlement = struct.pack('>II', 0xfade7171, 8 + len(b'<plist/>')) + b'<plist/>'
    hashes = 88 + len(identifier) + 5 * 32
    cd = bytearray(hashes + 32)
    struct.pack_into('>9I', cd, 0, 0xfade0c02, len(cd), 0x20400, 0, hashes, 88, 5, 1, 4096)
    cd[36:40] = bytes((32, 2, 27, 12))
    cd[88:88 + len(identifier)] = identifier
    cd[hashes - 5 * 32:hashes - 4 * 32] = hashlib.sha256(entitlement).digest()
    cms = struct.pack('>II', 0xfade0b01, 11) + b'cms'
    siglen = 12 + 3 * 8 + len(cd) + len(entitlement) + len(cms)
    code = bytearray(4096)
    struct.pack_into('<8I', code, 0, 0xfeedfacf, 0x100000c, 0x80000002, 2, 3, 160, 0, 0)
    for at, name, va, fileoff, size in ((32, b'__TEXT', 0x100000000, 0, 4096),
                                      (104, b'__LINKEDIT', 0x100001000, 4096, siglen)):
        struct.pack_into('<II16s4Q4I', code, at, 0x19, 72, name, va, 4096, fileoff, size, 7, 5, 0, 0)
    struct.pack_into('<4I', code, 176, 0x1d, 16, 4096, siglen)
    cd[hashes:hashes + 32] = hashlib.sha256(code).digest()
    blobs = [(0, cd), (5, entitlement), (0x10000, cms)]
    cursor = 12 + 8 * len(blobs)
    indices, body = [], bytearray()
    for slot, blob in blobs:
        indices.append(struct.pack('>II', slot, cursor))
        body += blob
        cursor += len(blob)
    sig = struct.pack('>III', 0xfade0cc0, cursor, len(blobs)) + b''.join(indices) + body
    return bytes(code) + sig, hashlib.sha256(cd).digest()[:20], entitlement


class SignTests(unittest.TestCase):
    def test_real_rewrite_path_preserves_entitlement_and_platform(self):
        original, oldhash, entitlement = fixture()
        output, report = pac.rewrite(original, {oldhash: b'\x02\x01\0\0'})
        self.assertEqual(struct.unpack_from('<I', output, 8)[0], 0)
        self.assertEqual(report['original_cdhash'], oldhash.hex())
        self.assertEqual(report['platform_identifier'], 27)
        self.assertEqual(report['signature_page_size'], 4096)
        cd, blobs = pac.selected_directory(output[4096:])
        self.assertEqual(blobs[5], entitlement)
        self.assertNotIn(0x10000, blobs)
        self.assertTrue(struct.unpack_from('>I', cd, 12)[0] & 2)
        hashes, slots = struct.unpack_from('>I', cd, 16)[0], struct.unpack_from('>I', cd, 28)[0]
        self.assertEqual(slots, 1)
        self.assertEqual(cd[hashes:hashes + 32], hashlib.sha256(output[:4096]).digest())

    def test_original_code_and_special_slot_tampering_is_rejected(self):
        original, oldhash, _ = fixture()
        changed = bytearray(original)
        changed[512] ^= 1
        with self.assertRaisesRegex(ValueError, 'original code page'):
            pac.rewrite(changed, {oldhash: b'\x02\x01\0\0'})
        changed = bytearray(original)
        changed[-12] ^= 1
        with self.assertRaisesRegex(ValueError, 'special slot'):
            pac.rewrite(changed, {oldhash: b'\x02\x01\0\0'})
        with self.assertRaisesRegex(ValueError, 'absent'):
            pac.rewrite(original, {})

    def test_universal_slices_are_selected_and_overlaps_rejected(self):
        original, _, _ = fixture()
        fat = bytearray(4096) + original
        struct.pack_into('>7I', fat, 0, 0xcafebabe, 1, 0x100000c, 0x80000002, 4096, len(original), 12)
        thin, report = pac.arm64e_slice(fat)
        self.assertEqual(thin, original)
        self.assertTrue(report['universal'])
        fat64 = bytearray(4096) + original
        struct.pack_into('>4I2Q2I', fat64, 0, 0xcafebabf, 1, 0x100000c, 0x80000002, 4096, len(original), 12, 0)
        self.assertEqual(pac.arm64e_slice(fat64)[0], original)
        struct.pack_into('>I', fat, 4, 2)
        struct.pack_into('>5I', fat, 28, 0x1000007, 3, 4096, len(original), 12)
        with self.assertRaisesRegex(ValueError, 'overlapping'):
            pac.arm64e_slice(fat)


if __name__ == '__main__':
    unittest.main()
