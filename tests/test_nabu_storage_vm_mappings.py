#!/usr/bin/env python3
"""Regress the NativeUfs kalloc fault caused by unmapped ordinal segments."""
import copy
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from prelink_kcg_bind import Refused, storage_mapping_layout
from prelink_verify import storage_mapping_errors


class TransportMappings(unittest.TestCase):
    def setUp(self):
        self.base = 0xfffffe000ff30000
        self.kernel_base = 0xfffffe0007004000
        self.sources = [dict(name=n, va=i * 0x4000, size=0x4000, filesize=0x4000,
                             maxprot=p, initprot=p, fileoff=i * 0x4000, sections=[])
                        for i, (n, p) in enumerate((('__TEXT', 5), ('__DATA_CONST', 3),
                                                    ('__DATA', 3), ('__LINKEDIT', 1)))]
        self.sources[1]['sections'] = [dict(name='__kalloc_type', addr=0x7f00, size=0xc0)]
        self.parsed_sources = [dict(s, vmaddr=s['va'], vmsize=s['size']) for s in self.sources]
        self.output = [dict(name=name, vmaddr=self.base + source['va'],
                            vmsize=source['size'], filesize=source['filesize'],
                            maxprot=prot, initprot=prot)
                       for source, name, prot in storage_mapping_layout(self.sources, self.base)]
        self.output.append(dict(name='__PRELINK_INFO', vmaddr=self.base + 0x14000,
                                vmsize=0x4000, filesize=0x4000, maxprot=3, initprot=3))

    def errors(self):
        return storage_mapping_errors(self.output, self.parsed_sources, self.base, self.kernel_base)

    def test_semantic_names_match_actual_kernel_source_contract(self):
        patch = (ROOT / 'platform/sheng/macos26/patches/0047-native-storage-vm-mappings.patch').read_text()
        table = dict(re.findall(r'\{ "(__NVB_\w+)", (VM_PROT_[A-Z_ |]+), (?:true|false) \}', patch))
        for _, name, prot in storage_mapping_layout(self.sources, self.base):
            self.assertIn(name, table)
            bits = sum({'VM_PROT_READ': 1, 'VM_PROT_WRITE': 2, 'VM_PROT_EXECUTE': 4}[x.strip()]
                       for x in table[name].split('|'))
            self.assertEqual(bits, prot)
        self.assertIn('{ "__NVB_DATA_CONST", VM_PROT_READ | VM_PROT_WRITE, true }', patch)
        self.assertEqual(self.errors(), [])

    def test_old_ordinal_segments_are_rejected_despite_correct_ranges_and_bytes(self):
        for i, segment in enumerate(self.output[:-1]): segment['name'] = '__NUFS_' + str(i)
        self.assertTrue(any('ordinal NUFS' in e for e in self.errors()))
        self.assertTrue(any('__NVB_DATA_CONST' in e for e in self.errors()))

    def test_unknown_or_duplicate_source_segment_is_refused(self):
        for value in ('__UNSUPPORTED', '__TEXT'):
            altered = copy.deepcopy(self.sources)
            altered[-1]['name'] = value
            with self.assertRaises(Refused): storage_mapping_layout(altered, self.base)

    def test_each_source_max_and_initial_protection_must_match(self):
        for index in range(4):
            for field in ('maxprot', 'initprot'):
                altered = copy.deepcopy(self.sources)
                altered[index][field] ^= 2
                with self.assertRaises(Refused): storage_mapping_layout(altered, self.base)

    def test_misaligned_or_outside_end_kern_is_rejected(self):
        altered = copy.deepcopy(self.sources); altered[1]['size'] -= 1
        with self.assertRaises(Refused): storage_mapping_layout(altered, self.base)
        self.output[-1]['vmaddr'] = self.base + 0x4000
        self.assertTrue(self.errors())

    def test_kalloc_requires_bootstrap_writable_coverage(self):
        self.output[1]['maxprot'] = self.output[1]['initprot'] = 1
        self.assertTrue(self.errors())
        self.output[1]['maxprot'] = self.output[1]['initprot'] = 3
        self.parsed_sources[1]['sections'][0]['size'] = 0x200
        self.assertTrue(any('kalloc metadata' in e for e in self.errors()))

    def test_duplicate_output_mapping_is_rejected(self):
        self.output.append(dict(self.output[1]))
        self.assertTrue(any('missing unique mapped segment __NVB_DATA_CONST' in e for e in self.errors()))

    def test_semantic_rename_preserves_virtual_layout(self):
        for source, output in zip(self.sources, self.output):
            self.assertEqual(output['vmaddr'], self.base + source['va'])
            self.assertEqual(output['vmsize'], source['size'])
            self.assertEqual(output['filesize'], source['filesize'])


if __name__ == '__main__': unittest.main()
