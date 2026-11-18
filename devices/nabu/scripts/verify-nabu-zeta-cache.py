#!/usr/bin/env python3
"""Independently diff every cache code edit and rehash its new ad-hoc signature."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import unittest

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--report', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
os.environ['ZETA_DYLD_CACHE_REWRITE_REPORT'] = str(a.report)
path = Path(__file__).with_name('zeta_pac') / 'tests/test_zeta_dyld_cache_rewrite.py'
spec = importlib.util.spec_from_file_location('independent_zeta_cache_tests', path)
independent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(independent)


class MacCacheVerification(independent.RewrittenCacheTests):
    def check_signature(self, old, new, offset, entry, page_shift):
        before = dict(independent.blobs(old, offset))
        after = dict(independent.blobs(new, offset))
        self.assertNotIn(0x10000, after)
        old_directories = [b for b in before.values() if b[:4] == bytes.fromhex('fade0c02') and b[36:38] == b'\x20\x02']
        new_directories = [b for b in after.values() if b[:4] == bytes.fromhex('fade0c02')]
        self.assertEqual(len(old_directories), 1)
        self.assertEqual(len(new_directories), 1)
        original, cd = old_directories[0], new_directories[0]
        for slot, blob in before.items():
            if slot != 0x10000 and blob[:4] != bytes.fromhex('fade0c02'):
                self.assertEqual(after[slot], blob, f'special blob {slot} changed')
        hashes, special, slots, limit = (struct.unpack_from('>I', cd, n)[0] for n in (16, 24, 28, 32))
        self.assertEqual(cd[36:38], b'\x20\x02')
        self.assertEqual(cd[38], original[38])
        self.assertEqual(cd[39], 12)
        self.assertEqual(slots, (limit + 4095) >> 12)
        self.assertEqual(limit, offset)
        self.assertEqual(len(cd), hashes + slots * 32)
        expected = bytearray(original[:hashes])
        struct.pack_into('>I', expected, 4, len(cd))
        struct.pack_into('>I', expected, 12, struct.unpack_from('>I', original, 12)[0] | 2)
        struct.pack_into('>I', expected, 28, slots)
        expected[39] = 12
        if struct.unpack_from('>I', expected, 8)[0] >= 0x20300:
            struct.pack_into('>Q', expected, 56, 0)
        self.assertEqual(bytes(expected), cd[:hashes], 'CodeDirectory metadata changed outside the ad-hoc/page conversion')
        for index in range(slots):
            self.assertEqual(hashlib.sha256(new[index << 12:min((index + 1) << 12, limit)]).digest(),
                             cd[hashes + 32 * index:hashes + 32 * (index + 1)], f'page {index}')
        cdhash = hashlib.sha256(cd).hexdigest()[:40]
        self.assertEqual(cdhash, entry['cdhash'])
        if entry.get('trust_cache_entry'):
            self.assertEqual(cdhash, entry['trust_cache_entry']['cdhash'])


suite = unittest.TestSuite([MacCacheVerification('test_rewritten_cache')])
r = unittest.TextTestRunner(verbosity=2).run(suite)
report = json.loads(a.report.read_text())
a.output.write_text(json.dumps(dict(passed=r.wasSuccessful(), files=len(report['files']),
                                    edits=report['edits'], all_changed_words_decoded=True,
                                    all_code_pages_hashed=True, hardware_tested=False), indent=2) + '\n')
raise SystemExit(not r.wasSuccessful())
