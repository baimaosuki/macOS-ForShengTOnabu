"""Independent relocation checks and execution of the actual OD worker bytes.

Unicorn models dispatch queues and stubs, not Darwin scheduling or PAC hardware.
The integration tests use the exact original local Apple input when available.
"""
from collections import deque
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import prepare_nabu_od_startup as patcher
from native_macho import macho

ORIGINAL = ROOT / 'out/nabu-dit-search-fix/opendirectoryd.arm64-slice'


class ODStartup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not ORIGINAL.exists():
            raise unittest.SkipTest('exact original 25F71 OD evidence not present')
        cls.original = ORIGINAL.read_bytes()
        cls.patched, cls.report = patcher.patch(cls.original)
        cls.image = macho(cls.original)

    def test_only_freshcreate_call_and_zero_rx_padding_change(self):
        call = patcher.offset(self.image, patcher.CREATE_CALL)
        island = patcher.offset(self.image, patcher.ISLAND)
        expected = set(range(call, call + 4)) | set(range(island, island + self.report['island_bytes']))
        changed = {i for i, (a, b) in enumerate(zip(self.original, self.patched)) if a != b}
        self.assertTrue(changed <= expected)
        self.assertEqual(len(self.original), len(self.patched))
        self.assertEqual(self.report['island_bytes'], 504)
        self.assertEqual(macho(self.patched), self.image)
        with self.assertRaisesRegex(ValueError, 'exact original'):
            patcher.patch(self.patched)

    def test_all_relocated_pc_references_with_independent_capstone_decoder(self):
        from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
        cs = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
        cs.detail = True
        for item in self.report['proof']['relocations']:
            pc = int(item['clone'], 16)
            at = patcher.offset(self.image, pc)
            insn = next(cs.disasm(self.patched[at:at + 4], pc))
            self.assertEqual(insn.operands[-1].imm, int(item['target'], 16), str(insn))

    def test_universal_wrapper_and_other_slice_preserved(self):
        start = 0x4000
        header = struct.pack('>7I', 0xCAFEBABE, 1, 0x100000C, 2, start, len(self.original), 14)
        fat = header + bytes(start - len(header)) + self.original
        output, report = patcher.patch(fat)
        self.assertEqual(output[:start], fat[:start])
        self.assertEqual(output[start:], self.patched)
        self.assertEqual(report['slice_offset'], start)

    def test_adhoc_signature_preserves_entitlements_and_binds_every_page(self):
        import tempfile
        from sign_nabu_od_startup import sign, signature_blobs
        from verify_page_hashes import verify
        signed, report = sign(self.original, self.patched)
        original_offset, _, before = signature_blobs(self.original)
        offset, _, after = signature_blobs(signed)
        self.assertEqual(offset, original_offset)
        self.assertEqual(signed[:offset], self.patched[:offset])
        self.assertEqual(after[5], before[5])
        self.assertEqual(after[7], before[7])
        self.assertEqual(set(after), {0, 5, 7})
        cd = after[0]
        hashes = struct.unpack_from('>I', cd, 16)[0]
        self.assertEqual(cd[hashes - 64:hashes - 32], bytes(32))
        self.assertTrue(struct.unpack_from('>I', cd, 12)[0] & 2)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'opendirectoryd'
            path.write_bytes(signed)
            verified = verify(str(path))
            self.assertTrue(verified['ok'])
            self.assertEqual(verified['cdhash'][:40], report['cdhash'])
            damaged = bytearray(signed)
            damaged[patcher.offset(self.image, patcher.ISLAND) + 4] ^= 1
            path.write_bytes(damaged)
            self.assertFalse(verify(str(path))['ok'])
        tampered = bytearray(self.patched)
        tampered[10] ^= 1
        with self.assertRaisesRegex(ValueError, 'exact OD'):
            sign(self.original, bytes(tampered))

    def test_machinecode_original_worker_signal_and_completion_replay(self):
        from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
        from unicorn.arm64_const import UC_ARM64_REG_PC, UC_ARM64_REG_SP, UC_ARM64_REG_LR, UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X8, UC_ARM64_REG_X9

        def run(candidate, entry, status_callback=0):
            u = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
            u.mem_map(0x100000000, 0xA0000)
            u.mem_write(0x100000000, candidate[:0x94000])
            u.mem_map(0x200000000, 0x40000)
            conn, block, sem = 0x200000000, 0x200001000, 0x200002000
            stateq, opsq, node, config = 11, 22, 0x200003000, 0x200004000
            stack, stop = 0x200030000, 0x20003F000
            def put(addr, value): u.mem_write(addr, struct.pack('<Q', value))
            def get(addr): return struct.unpack('<Q', u.mem_read(addr, 8))[0]
            put(conn + 0x28, stateq); put(conn + 0x138, opsq)
            put(conn + 0x78, node); put(conn + 0x80, config); put(config + 0x88, config)
            put(0x1000939F0, (1 << 64) - 1)
            put(0x10007D120, 0x200005000)
            put(conn + 0x118, status_callback)
            tasks, calls, frames = deque(), [], []
            sem_count = [0]
            active_state = [False]
            def ret(value=None):
                if value is not None: u.reg_write(UC_ARM64_REG_X0, value)
                u.reg_write(UC_ARM64_REG_PC, u.reg_read(UC_ARM64_REG_LR))
            def invoke(address, arg, finish):
                frames.append((u.reg_read(UC_ARM64_REG_LR), finish))
                u.reg_write(UC_ARM64_REG_LR, stop + 4)
                u.reg_write(UC_ARM64_REG_X0, arg)
                u.reg_write(UC_ARM64_REG_PC, address)
            def queue_block(addr):
                # Real libdispatch copies escaping stack blocks before return.
                dest = block + 0x100 + len(calls) * 0x80
                u.mem_write(dest, bytes(u.mem_read(addr, 64)))
                calls.append(('copy', get(addr + 16)))
                tasks.append((get(dest + 16), dest))
            def drain():
                if tasks:
                    fn, arg = tasks.popleft()
                    invoke(fn, arg, drain)
                else:
                    ret()
            def hook(uc, pc, size, _):
                if pc == stop:
                    uc.emu_stop(); return
                if pc == stop + 4:
                    lr, done = frames.pop(); uc.reg_write(UC_ARM64_REG_LR, lr)
                    done(); return
                x0, x1 = uc.reg_read(UC_ARM64_REG_X0), uc.reg_read(UC_ARM64_REG_X1)
                if pc == 0x10006406C:  # original timer group: nil timer, no effects
                    ret(); return
                if pc == 0x100040AD8: ret(5); return
                if pc == 0x10006414C: ret(sem); return
                if pc in (0x1000257C0, 0x1000257CC, 0x10006413C, 0x10006411C): ret(); return
                if pc == 0x100064ADC: ret(0); return
                if pc == 0x10006420C:
                    self.assertEqual(x0, stateq)
                    self.assertFalse(active_state[0]); active_state[0] = True
                    def done(): active_state[0] = False; ret()
                    invoke(get(x1 + 16), x1, done); return
                if pc in (0x10006400C, 0x100063FEC):
                    self.assertEqual(x0, opsq); queue_block(x1); ret(); return
                if pc == patcher.BARRIER_SYNC:
                    calls.append(('barrier', x0))
                    if x0 == opsq:
                        self.assertFalse(active_state[0])
                        # Noop fence is ordered after all original async blocks.
                        queue_block(x1); drain(); return
                    self.assertEqual(x0, stateq)
                    self.assertFalse(active_state[0])
                    invoke(get(x1 + 16), x1, lambda: ret()); return
                if pc == 0x10006415C: sem_count[0] += 1; calls.append(('signal', sem_count[0])); ret(0); return
                if pc == 0x10006423C: calls.append(('deadline', sem_count[0])); ret(0xFFFFFFFE00000000); return
                if pc == 0x10006416C:
                    calls.append(('wait', sem_count[0])); ret(0 if sem_count[0] else 49); return
                if pc == status_callback and pc:
                    calls.append(('callback', pc)); ret(0x99); return
                word = struct.unpack('<I', uc.mem_read(pc, 4))[0]
                if pc in (0x1000236A8, 0x100023968, 0x100023978):
                    address = uc.reg_read(UC_ARM64_REG_X8)
                    value = get(address)
                    mask = 2 if pc == 0x100023968 else 0x80
                    put(address, value & ~mask if pc == 0x100023978 else value | mask)
                    uc.reg_write(UC_ARM64_REG_X8 if pc == 0x1000236A8 else UC_ARM64_REG_X9, value)
                    uc.reg_write(UC_ARM64_REG_PC, pc + 4); return
                # PAC is deliberately excluded from emulation; independent
                # disassembly checks the injected signing instruction instead.
                if word in (0xD503237F, 0xD50323FF, 0xD50323BF) or word & 0xFFFF0000 == 0xDAC10000:
                    uc.reg_write(UC_ARM64_REG_PC, pc + 4); return
                if word == 0xD65F0FFF:  # retab
                    ret(); return
            u.hook_add(UC_HOOK_CODE, hook)
            u.reg_write(UC_ARM64_REG_SP, stack)
            u.reg_write(UC_ARM64_REG_LR, stop)
            u.reg_write(UC_ARM64_REG_X0, conn)
            u.reg_write(UC_ARM64_REG_X1, 0)
            u.emu_start(entry, stop + 8, count=2000)
            self.assertEqual(u.reg_read(UC_ARM64_REG_PC), stop)
            return u.reg_read(UC_ARM64_REG_X0), calls, get(conn + 0x70), get(conn + 0x174) & 0xFFFFFFFF

        old_status, old_calls, _, _ = run(self.original, patcher.CONNECT)
        new_status, new_calls, flags, status = run(self.patched, patcher.ISLAND)
        self.assertEqual(old_status, 0x834)
        self.assertIn(('wait', 0), old_calls)
        self.assertEqual(new_status, 0)
        self.assertEqual(status, 0)
        self.assertEqual(flags & 0x82, 2)  # original completion connected/!connecting
        self.assertIn(('barrier', 11), new_calls)  # actual original completion ran
        self.assertLess(new_calls.index(('signal', 1)), new_calls.index(('deadline', 1)))
        self.assertIn(('wait', 1), new_calls)
        # A non-null callback guard selects the unchanged routine, no fence.
        _, guarded_calls, _, _ = run(self.patched, patcher.ISLAND, 0x200010000)
        self.assertNotIn(('barrier', 22), guarded_calls)


if __name__ == '__main__':
    unittest.main()
