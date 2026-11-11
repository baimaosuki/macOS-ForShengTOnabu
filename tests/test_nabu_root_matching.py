# SPDX-License-Identifier: GPL-3.0-or-later
import plistlib
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from audit_adt import parse_adt
from native_devtree import _adt_node, configure_native_text_state, make_nabu_adt


class RootMatching(unittest.TestCase):
    identity = uuid.UUID('47719F50-5527-44A3-9F59-8170528D8C14')

    def test_mainline_xml_identity_idempotence_and_existing_properties(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'kernel.adt'
            make_nabu_adt(path, os_environment='main', native_ufs=True, packed_ramdisk=True)
            configure_native_text_state(path, development_root=True)
            original, _ = parse_adt(path.read_bytes())
            configure_native_text_state(path, development_root=True,
                root_match_volume_uuid=str(self.identity))
            content = path.read_bytes()
            tree, end = parse_adt(content)
            self.assertEqual(end, len(content))
            chosen = tree['/device-tree/chosen']
            xml = chosen.pop('root-matching')
            self.assertTrue(xml.endswith(b'\0'))
            self.assertEqual(plistlib.loads(xml[:-1], fmt=plistlib.FMT_XML), dict(IOProviderClass='IOMedia',
                IOPropertyMatch=dict(UUID=str(self.identity).upper())))
            for key in ('boot-uuid', 'apfs-preboot-uuid'):
                self.assertEqual(chosen.pop(key), str(self.identity).upper().encode() + b'\0')
            self.assertEqual(tree, original)
            configure_native_text_state(path, development_root=True, root_match_volume_uuid=self.identity)
            self.assertEqual(path.read_bytes(), content)
            # Raw property headers occur once each: idempotence must not hide
            # duplicate keys behind a dictionary parser.
            for key in ('root-matching', 'boot-uuid', 'apfs-preboot-uuid'):
                self.assertEqual(content.count(key.encode() + bytes(32 - len(key))), 1)

    def test_invalid_uuid_conflicts_and_recovery_gate_leave_source_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'kernel.adt'
            make_nabu_adt(path, os_environment='main')
            before = path.read_bytes()
            for invalid in ('not-a-uuid', '<dict/>', 7, b'bad', uuid.UUID(int=0)):
                with self.assertRaises(ValueError):
                    configure_native_text_state(path, root_match_volume_uuid=invalid)
                self.assertEqual(path.read_bytes(), before)
            other = uuid.UUID('808eac5e-8275-478d-814c-2229d8eb8d07')
            for kwargs in (dict(boot_volume_uuid=self.identity),
                           dict(boot_volume_uuid=self.identity, root_match_volume_uuid=self.identity),
                           dict(development_root=True, boot_volume_uuid=other, root_match_volume_uuid=self.identity)):
                with self.assertRaises(ValueError): configure_native_text_state(path, **kwargs)
                self.assertEqual(path.read_bytes(), before)
            for key, value in (('boot-uuid', str(other).upper().encode() + b'\0'),
                               ('apfs-preboot-uuid', str(other).upper().encode() + b'\0'),
                               ('root-matching', b'<dict/>\0')):
                path.write_bytes(_adt_node('device-tree', children=(
                    _adt_node('chosen', ((key, value),)),)))
                before = path.read_bytes()
                with self.assertRaisesRegex(ValueError, key):
                    configure_native_text_state(path, development_root=True, root_match_volume_uuid=self.identity)
                self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__': unittest.main()
