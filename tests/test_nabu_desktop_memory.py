# SPDX-License-Identifier: GPL-3.0-or-later
"""Desktop memory requests preserve the ADT and fail before writing bad bundles."""
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import plistlib
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from audit_adt import parse_adt
from assemble_nabu_desktop_candidate import assemble, validate_owned_memory_size
from native_devtree import make_nabu_adt, configure_native_text_state


class DesktopMemory(unittest.TestCase):
    def test_limits_alignment_and_actual_payload_fit(self):
        for size in (256 << 20, 512 << 20, 2 << 30, 5 << 30):
            self.assertEqual(validate_owned_memory_size(size, 128 << 20, 21 << 20), size)
        for size in (0, (256 << 20) - (2 << 20), (5 << 30) + (2 << 20)):
            with self.assertRaisesRegex(ValueError, 'between'):
                validate_owned_memory_size(size, 0, 0)
        for size in ((2 << 30) + 4096, (256 << 20) + 1):
            with self.assertRaisesRegex(ValueError, 'aligned'):
                validate_owned_memory_size(size, 0, 0)
        # The previous image fits 512 MiB, but that only establishes payload
        # fit, not adequate VM headroom or successful firmware allocation.
        validate_owned_memory_size(512 << 20, 308584448, 21110928)
        validate_owned_memory_size(512 << 20, (512 << 20) - (23 << 20), 21 << 20)
        with self.assertRaisesRegex(ValueError, 'too small'):
            validate_owned_memory_size(512 << 20, (512 << 20) - (23 << 20) + 1, 21 << 20)

    def test_only_requested_memory_field_changes_in_actual_adt(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder) / name for name in ('old.adt', 'new.adt')]
            for path, size in zip(paths, (512 << 20, 2 << 30)):
                make_nabu_adt(path, owned_memory_size=size, os_environment='main',
                              native_ufs=True, packed_ramdisk=True)
                configure_native_text_state(path, development_root=True)
            old, new = [p.read_bytes() for p in paths]
            before, used = parse_adt(old)
            after, used_after = parse_adt(new)
            self.assertEqual((used, used_after), (len(old), len(new)))
            chosen = '/device-tree/chosen'
            self.assertEqual(before[chosen]['darwinboot-memory-size'], struct.pack('<Q', 512 << 20))
            self.assertEqual(after[chosen]['darwinboot-memory-size'], struct.pack('<Q', 2 << 30))
            after[chosen]['darwinboot-memory-size'] = before[chosen]['darwinboot-memory-size']
            self.assertEqual(before, after)
            marker = b'darwinboot-memory-size' + bytes(32 - len('darwinboot-memory-size')) + struct.pack('<I', 8)
            self.assertEqual(old.count(marker), 1)
            at = old.index(marker) + len(marker)
            self.assertEqual(old[:at], new[:at])
            self.assertEqual(old[at + 8:], new[at + 8:])

    def test_assembly_default_override_and_request_metadata(self):
        # Exercise actual bundle generation with tiny stand-ins for the large
        # kernel/root inputs; cryptographic root validation is covered elsewhere.
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            kernel, report, data = [folder / n for n in ('kernel', 'report.json', 'data.dbr')]
            kernel.write_bytes(b'kernel fixture')
            storage = dict(kernel_integrated=True, build=dict(root_mode='desktop'),
                           pointer_formats=[], pointer_relocations=[], constructors=[])
            report.write_text(json.dumps(dict(output_sha256=hashlib.sha256(kernel.read_bytes()).hexdigest(),
                unresolved_sites=0, loadable=True, storage_driver=storage, branch_relocation_count=0)))
            data.write_bytes(b'DBRDISK1' + struct.pack('<IIQQ', 1, 4096, 3 << 30, 0))
            policy, root, loader = [folder / n for n in ('policy', 'root', 'loader')]
            for p in (policy, root, loader): p.mkdir()
            (policy / 'owner-public.pem').write_bytes(b'public fixture')
            (root / 'root_image.bin').write_bytes(bytes(4096))
            (root / 'build.json').write_text(json.dumps(dict(build='25F71', final_image_readback_verified=True,
                image_bytes=4096, manifest_sha256='fixture')))
            driver = loader / 'build/RELEASE_CLANGPDB/AARCH64/DarwinBoot.efi'
            driver.parent.mkdir(parents=True)
            driver.write_bytes(b'PackedRAMDisk DBRDISK1 loader fixture')
            (loader / 'build.json').write_text(json.dumps(dict(efi_sha256=hashlib.sha256(driver.read_bytes()).hexdigest())))
            image = dict(segments=[dict(va=0xfffffe0007000000, size=300 << 20)])
            with patch('assemble_nabu_desktop_candidate.macho', return_value=image), \
                 patch('assemble_nabu_desktop_candidate.validate', return_value=dict(manifest_sha256='fixture',
                     windows=[dict(name='OS', volume_uuid='47719f50-5527-44a3-9f59-8170528d8c14')])):
                for name, size in (('default', 2 << 30), ('override', 1 << 30)):
                    kwargs = {} if name == 'default' else dict(owned_memory_size=size)
                    out = folder / name
                    result = assemble(kernel, report, policy, data, root, loader, out, **kwargs)
                    tree, _ = parse_adt((out / 'EFI/UniXNU/Payloads/kernel.macho.adt').read_bytes())
                    self.assertEqual(tree['/device-tree/chosen']['darwinboot-memory-size'], struct.pack('<Q', size))
                    self.assertEqual(result['owned_memory_request_bytes'], size)
                    self.assertEqual(result['owned_memory_bytes'], size)
                    self.assertIn('request only', result['owned_memory_contract'])
                    self.assertFalse(result['hardware_executed'])
                    chosen = tree['/device-tree/chosen']
                    identity = '47719F50-5527-44A3-9F59-8170528D8C14'
                    self.assertEqual(plistlib.loads(chosen['root-matching'].rstrip(b'\0'), fmt=plistlib.FMT_XML),
                        dict(IOProviderClass='IOMedia', IOPropertyMatch=dict(UUID=identity)))
                    self.assertEqual(chosen['boot-uuid'], identity.encode() + b'\0')
                    self.assertEqual(chosen['apfs-preboot-uuid'], chosen['boot-uuid'])
                    self.assertEqual(result['root_uuid'], identity)
                    self.assertIn('boot-uuid=' + identity, result['boot_args'])
                out = folder / 'bad'
                with self.assertRaisesRegex(ValueError, 'too small'):
                    assemble(kernel, report, policy, data, root, loader, out, owned_memory_size=256 << 20)
                self.assertFalse(out.exists())


if __name__ == '__main__': unittest.main()
