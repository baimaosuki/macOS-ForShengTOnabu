"""Execute actual paired cache instructions with bounded original-state models.

PAC is skipped explicitly: this verifies compositor decisions, not authentication.
The original dispatch_once implementation is modeled at its call boundary.
"""
import hashlib
import json
import mmap
from pathlib import Path
import struct
import unittest

try:
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
    from unicorn.arm64_const import UC_ARM64_REG_PC, UC_ARM64_REG_LR, UC_ARM64_REG_SP, UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X20
except ImportError:
    Uc = None

ROOT = Path(__file__).resolve().parents[1]
BASE = 0x180088000
DATA = 0x1e9d30000
STOP = 0x100000
DISPATCH = 0x1871a23f0


@unittest.skipIf(Uc is None, 'Unicorn is not installed')
class OriginalCompositorReplay(unittest.TestCase):
    def setUp(self):
        path = ROOT / 'out/nabu-pid1-cache-fault/cache/System/Library/dyld/dyld_shared_cache_arm64e.01'
        if not path.exists():
            self.skipTest('paired local cache unavailable')
        self.handle = path.open('rb')
        self.cache = mmap.mmap(self.handle.fileno(), 0, access=mmap.ACCESS_READ)
        self.contract = json.loads((ROOT / 'platform/nabu/macos26/software-compositor/contract.json').read_text())
        for site in self.contract['sites']:
            page = self.cache[site['file_offset']:site['file_offset'] + 16384]
            self.assertEqual(hashlib.sha256(page).hexdigest(), site['page_sha256'])
        self.events = []

    def tearDown(self):
        if hasattr(self, 'cache'):
            self.cache.close()
            self.handle.close()

    def machine(self, addresses, patched=True):
        machine = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        mapped = set()
        for address in addresses:
            page = address & ~0x3fff
            if page in mapped:
                continue
            mapped.add(page)
            machine.mem_map(page, 16384)
            raw = bytearray(self.cache[page - BASE:page - BASE + 16384])
            if patched:
                for site in self.contract['sites']:
                    if site['file_offset'] == page - BASE:
                        start = site['edit_offset']
                        raw[start:start + len(bytes.fromhex(site['after']))] = bytes.fromhex(site['after'])
            machine.mem_write(page, bytes(raw))
        for address, size in [(DATA, 0x10000), (0x1ec710000, 0x10000), (0x200000, 0x10000), (STOP, 0x1000), (DISPATCH & ~0xfff, 0x1000)]:
            machine.mem_map(address, size)
        machine.reg_write(UC_ARM64_REG_SP, 0x208000)
        machine.reg_write(UC_ARM64_REG_LR, STOP)
        def hook(uc, pc, size, unused):
            word = struct.unpack('<I', uc.mem_read(pc, 4))[0]
            if word in (0xd503237f, 0xd50323ff):  # PACIBSP/AUTIBSP omitted, no PAC claim
                uc.reg_write(UC_ARM64_REG_PC, pc + 4)
            elif word == 0xd65f0fff:  # RETAB: ordinary return for this model
                uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))
            elif pc == DISPATCH:
                token = uc.reg_read(UC_ARM64_REG_X0)
                self.assertEqual(token, 0x1e9d333b8)
                self.events.append('original-dispatch-once-boundary')
                # Run actual patched callback independently, then original once
                # completion publishes -1. No successful Metal result is invented.
                callback = self.machine([0x186f298c8])
                callback.emu_start(0x186f298c8, STOP, count=32)
                self.assertEqual(bytes(callback.mem_read(0x1e9d333b4, 1)), b'\0')
                uc.mem_write(token, struct.pack('<Q', 0xffffffffffffffff))
                uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))
        machine.hook_add(UC_HOOK_CODE, hook)
        return machine

    def test_default_and_shadow_use_completed_no_device_once(self):
        default = self.machine([0x186e7d374])
        default.emu_start(0x186e7d374, STOP, count=100)
        self.assertEqual(default.mem_read(0x1e9d333b4, 1), b'\0')
        self.assertEqual(default.mem_read(0x1e9d32d18, 1), b'\1')
        self.assertEqual(self.events, ['original-dispatch-once-boundary'])
        # Original WSCompositorActivateDefault branches to activate_compositor_sw
        # when this byte is one. Its first initialization call is modeled complete.
        default.mem_write(0x1e9d32d00, struct.pack('<Q', 0xffffffffffffffff))
        default.mem_write(0x1e9d32d10, struct.pack('<Q', 0xffffffffffffffff))
        reached = []
        def stop_at_sw(uc, pc, size, unused):
            if pc in (0x186e7ccb8, 0x186e7cbfc):
                reached.append(pc)
                uc.emu_stop()
        default.hook_add(UC_HOOK_CODE, stop_at_sw)
        default.reg_write(UC_ARM64_REG_LR, STOP)
        default.emu_start(0x186e7d2d4, STOP, count=100)
        self.assertEqual(reached, [0x186e7ccb8])
        # Original shadow callback sees completed once + zero device and stores0.
        shadow = self.machine([0x186e7d134])
        shadow.mem_write(0x1e9d333b8, struct.pack('<Q', 0xffffffffffffffff))
        # This global is on a separate page.
        shadow.mem_map(0x1e9d2c000, 0x4000)
        shadow.mem_write(0x1e9d2e0c0, b'\1')
        shadow.emu_start(0x186e7d134, STOP, count=100)
        self.assertEqual(shadow.mem_read(0x1e9d2e0c0, 1), b'\0')

    def test_actual_format_getter_changes_rgha_to_real_bgra_before_allocation(self):
        results = []
        for patched in (False, True):
            machine = self.machine([0x18a92eae0], patched=patched)
            machine.reg_write(UC_ARM64_REG_X0, 0x200000)
            machine.mem_write(0x1ec7147f8, struct.pack('<Q', 0xffffffffffffffff))
            machine.mem_write(0x1ec714a18, b'\1')  # original RGhA branch selected
            # vfunc brightness value0 lets original code reach this format flag.
            brightness = 0x18a76635c
            machine.mem_map(brightness & ~0x3fff, 0x4000)
            machine.mem_write(brightness, bytes.fromhex('00e4002fc0035fd6'))  # movi d0,#0;ret
            machine.emu_start(0x18a92eae0, STOP, count=100)
            results.append(machine.reg_read(UC_ARM64_REG_X0))
        self.assertEqual(results, [0x52476841, 0x42475241])

    def test_intermediate_pool_uses_bgra80_after_original_texture_query(self):
        for patched, expected in ((False, 115), (True, 80)):
            machine = self.machine([0x1870f0844], patched=patched)
            machine.reg_write(UC_ARM64_REG_X0, 115)  # original RGBA16Float texture result
            machine.emu_start(0x1870f0844, 0x1870f0848, count=1)
            self.assertEqual(machine.reg_read(UC_ARM64_REG_X20), expected)
