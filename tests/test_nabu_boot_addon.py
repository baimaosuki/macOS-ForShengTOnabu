"""The shipped addon must carry an intact signed tool and matching owner trust."""
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from prepare_nabu_boot_addon import prepare, validate_addon, check_trust_membership
from native_devtree import make_nabu_adt, configure_native_text_state
from audit_adt import parse_adt


def signed_fixture():
    # A minimal signed Mach-O with one real CodeDirectory page hash. The
    # fixture is never run or shipped; it tests the byte/signature contract.
    limit = 4096
    ident = b'org.nabu.host.signature-fixture\0'
    offset = 44 + len(ident)
    cdlen = offset + 32
    signature_size = 20 + cdlen
    raw = bytearray(limit)
    struct.pack_into('<8I', raw, 0, 0xfeedfacf, 0x100000c, 0, 2, 1, 16, 0, 0)
    struct.pack_into('<4I', raw, 32, 0x1d, 16, limit, signature_size)
    cd = struct.pack('>9I4BI', 0xfade0c02, cdlen, 0x20100, 2, offset, 44,
                     0, 1, limit, 32, 2, 0, 12, 0) + ident + hashlib.sha256(raw).digest()
    signature = struct.pack('>5I', 0xfade0cc0, signature_size, 1, 0, 20) + cd
    return bytes(raw) + signature


class BootAddon(unittest.TestCase):
    def test_original_modules_unchanged_signature_membership_and_tamper_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shim = root / 'shim'; shim.mkdir()
            binary = signed_fixture()
            (shim / 'apfs_boot_util').write_bytes(binary)
            (shim / 'build.json').write_text(json.dumps(dict(sha256=hashlib.sha256(binary).hexdigest())))
            modules = [struct.pack('<I16sI', 2, uuid.UUID(int=i).bytes, 1) +
                       bytes([i]) * 20 + bytes((2, 0, 0, 0)) for i in (1, 2, 3)]
            original = struct.pack('<4I', 3, 16, 16 + len(modules[0]), 16 + len(modules[0]) + len(modules[1])) + b''.join(modules)
            trust = root / 'original.trustcache'; trust.write_bytes(original)
            output = root / 'out'
            report = prepare(shim, trust, output)
            self.assertEqual(report['trust']['module_count'], 4)
            bundled = (output / 'kernel.macho.trustcache').read_bytes()
            offsets = struct.unpack_from('<4I', bundled, 4)
            for index, expected in enumerate(modules):
                self.assertEqual(bundled[offsets[index]:offsets[index + 1]], expected)
            raw = (output / 'kernel.macho.gpuaddon').read_bytes()
            checked = validate_addon(raw)
            check_trust_membership(checked, bundled)
            with self.assertRaisesRegex(ValueError, 'absent'):
                check_trust_membership(checked, original)
            corrupted = bytearray(raw); corrupted[-8] ^= 1
            with self.assertRaises((ValueError, zlib.error)):
                validate_addon(bytes(corrupted))
            with self.assertRaises(ValueError): validate_addon(raw[:-1])
            self.assertFalse(report['gpu_driver_included'])

    def test_only_addon_slot_added_to_working_adt(self):
        with tempfile.TemporaryDirectory() as directory:
            trees = []
            for enabled in (False, True):
                p = Path(directory) / str(enabled)
                make_nabu_adt(p, owned_memory_size=2 << 30, os_environment='main', native_ufs=True,
                              packed_ramdisk=True, trust_cache=True, boot_addon=enabled)
                configure_native_text_state(p, development_root=True,
                    root_match_volume_uuid='47719F50-5527-44A3-9F59-8170528D8C14')
                tree, end = parse_adt(p.read_bytes()); self.assertEqual(end, p.stat().st_size)
                trees.append(tree)
            self.assertEqual(trees[1]['/device-tree/chosen/memory-map'].pop('GpuAddon'), bytes(16))
            self.assertEqual(trees[0], trees[1])


if __name__ == '__main__': unittest.main()
