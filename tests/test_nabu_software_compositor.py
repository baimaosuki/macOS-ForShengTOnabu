import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cpu', ROOT / 'scripts/prepare_nabu_software_compositor.py')
cpu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cpu)


class SoftwareCompositor(unittest.TestCase):
    def test_policy_keeps_original_dispatch_once_and_allocates_true_bgra(self):
        contract = json.loads(cpu.CONTRACT.read_text())
        self.assertEqual(len(contract['sites']), 3)
        metal, framebuffer, pool = contract['sites']
        self.assertEqual(struct.unpack('<2I', bytes.fromhex(metal['after'])),
                         (0xd50323ff, 0xd65f03c0))
        words = struct.unpack('<4I', bytes.fromhex(framebuffer['after']))
        self.assertEqual(((words[0] >> 5) & 0xffff) | (((words[1] >> 5) & 0xffff) << 16), 0x42475241)
        self.assertEqual(words[-2:], (0xd50323ff, 0xd65f03c0))
        self.assertEqual(pool['after'], '140a80d2')  # mov x20,#80 before pool lookup
        self.assertTrue(contract['default_off'])
        self.assertTrue(contract['shared_page_scope'])
        self.assertFalse(contract['runtime_qualified'])

    def test_real_cache_prepare_is_reproducible(self):
        cache = ROOT / 'out/nabu-pid1-cache-fault/cache/System/Library/dyld'
        if not (cache / 'dyld_shared_cache_arm64e.01').exists():
            self.skipTest('paired local original cache is unavailable')
        with tempfile.TemporaryDirectory() as folder:
            result = cpu.prepare(cache, Path(folder))
            generated = Path(folder) / 'nabu_cpu_compositor_pages.h'
            checked = ROOT / 'platform/nabu/macos26/software-compositor/nabu_cpu_compositor_pages.h'
            self.assertEqual(generated.read_bytes(), checked.read_bytes())
            self.assertTrue(result['original_cache_unchanged'])
