#!/usr/bin/env python3
"""Replay the captured AppKit managed-Mac disclosure feature gate in 26C5035r.

The original method, branch and epilogue run. Feature-provider, ObjC dispatch
and TCC IPC boundaries are fixtures. This is not a privacy authorization test.
PAC is skipped here; platform PAC tests are separate.
"""
import argparse
import hashlib
import json
import mmap
from pathlib import Path
import struct

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_INVALID
from unicorn.arm64_const import *

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('cache', type=Path)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
device = Path(__file__).resolve().parents[1]
fixture = json.loads((device / 'compat/cpu-display/managed-tcc-26C5035r.json').read_text())
site = fixture['patches'][0]
maps, handles = [], []
for path in args.cache.parent.glob('dyld_shared_cache_arm64e*'):
    if not path.is_file() or ':' in path.name or path.suffix in ('.a2s', '.map', '.symbols'):
        continue
    stream = path.open('rb')
    header = stream.read(4096)
    if not header.startswith(b'dyld_v1'):
        stream.close()
        continue
    data = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
    handles.append((stream, data))
    offset, count = struct.unpack_from('<II', data, 16)
    for index in range(count):
        va, size, fileoff, _, _ = struct.unpack_from('<QQQII', data, offset + index * 32)
        maps.append((va, va + size, data, fileoff))


def read(address, size):
    for lo, hi, data, offset in maps:
        if lo <= address and address + size <= hi:
            return bytes(data[offset + address - lo:offset + address - lo + size])
    raise ValueError(hex(address))


METHOD = 0x18a79d0d0
PATCH = 0x18a79d0ec
CALLER = 0x18a3d3b04
CONTINUE = 0x18a3d3b0c
STOP = 0x100000
STACK = 0x200000
MEM = 0x300000


def replay(patched, feature, bundle, identifier, through_caller=False):
    cpu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    cpu.ctl_set_cpu_model(UC_CPU_ARM64_MAX)
    pages, events = set(), []

    def mapping(address, original=True):
        page = address & ~4095
        if page in pages:
            return
        data = bytearray(read(page, 4096)) if original else bytearray(4096)
        if page == PATCH & ~4095:
            assert hashlib.sha256(data).hexdigest() == site['page_sha256']
            if patched:
                at = PATCH & 4095
                assert data[at:at + 4] == bytes.fromhex(site['original'])
                data[at:at + 4] = bytes.fromhex(site['replacement'])
                assert hashlib.sha256(data).hexdigest() == site['patched_page_sha256']
        cpu.mem_map(page, 4096)
        cpu.mem_write(page, bytes(data))
        pages.add(page)

    def fault(emu, access, address, size, value, unused):
        mapping(address)
        return True

    def ret(value=None):
        if value is not None:
            cpu.reg_write(UC_ARM64_REG_X0, value)
        cpu.reg_write(UC_ARM64_REG_PC, cpu.reg_read(UC_ARM64_REG_LR))

    def hook(emu, pc, size, unused):
        if pc == 0x1900327c0:
            events.append('AppKit-captured-method-dispatch')
            assert emu.reg_read(UC_ARM64_REG_X0) == MEM + 0x400
            emu.reg_write(UC_ARM64_REG_PC, METHOD)
            return
        if pc == 0x190107f70:
            mapping(emu.reg_read(UC_ARM64_REG_X0))
            mapping(emu.reg_read(UC_ARM64_REG_X1))
            assert bytes(emu.mem_read(emu.reg_read(UC_ARM64_REG_X0), 4)) == b'TCC\0'
            assert bytes(emu.mem_read(emu.reg_read(UC_ARM64_REG_X1), 11)) == b'managedTCC\0'
            events.append('TCC/managedTCC-feature-query')
            ret(int(feature))
            return
        if pc == 0x190062e20:
            events.append('mainBundle')
            ret(MEM + 0x100 if bundle else 0)
            return
        if pc == 0x190045ce0:
            events.append('bundleIdentifier')
            ret(MEM + 0x200 if bundle and identifier else 0)
            return
        if pc == 0x1901068e0:
            events.append('TCCAccessLaunchDisclosurePromptIfNeeded-IPC')
            assert emu.reg_read(UC_ARM64_REG_X0) == 0
            assert emu.reg_read(UC_ARM64_REG_X1) == MEM + 0x200
            emu.emu_stop()  # Original captured synchronous edge, never a fake success.
            return
        word = struct.unpack('<I', emu.mem_read(pc, 4))[0]
        if word in (0xd503237f, 0xd50323ff):
            emu.reg_write(UC_ARM64_REG_PC, pc + 4)
        elif word == 0xd65f0fff:
            ret()

    cpu.hook_add(UC_HOOK_MEM_INVALID, fault)
    cpu.hook_add(UC_HOOK_CODE, hook)
    for address in (STOP, STACK, STACK + 4096, MEM):
        mapping(address, False)
    mapping(0x1e0171a20)
    cpu.mem_write(0x1e0171a20, struct.pack('<Q', MEM + 0x800))
    mapping(METHOD)
    if through_caller:
        mapping(CALLER)
    initial_sp = STACK + 0x1800
    cpu.reg_write(UC_ARM64_REG_SP, initial_sp)
    cpu.reg_write(UC_ARM64_REG_LR, STOP)
    cpu.reg_write(UC_ARM64_REG_X0, MEM + 0x400)
    sentinels = {}
    for index in range(19, 30):
        value = MEM + 0x400 if index == 19 else 0x123000 + index * 16
        register = globals()[f'UC_ARM64_REG_X{index}']
        cpu.reg_write(register, value)
        sentinels[register] = value
    end = CONTINUE if through_caller else STOP
    cpu.emu_start(CALLER if through_caller else METHOD, end, count=1000)
    pc = cpu.reg_read(UC_ARM64_REG_PC)
    if pc == end:
        assert cpu.reg_read(UC_ARM64_REG_SP) == initial_sp
        assert all(cpu.reg_read(register) == value for register, value in sentinels.items())
    return pc, events


rows = []
for through_caller in (False, True):
    end = CONTINUE if through_caller else STOP
    for feature in (False, True):
        for bundle in (False, True):
            for identifier in (False, True):
                pc, events = replay(False, feature, bundle, identifier, through_caller)
                assert (pc == 0x1901068e0) == (feature and bundle and identifier)
                if not (feature and bundle and identifier):
                    assert pc == end
                rows.append(dict(test='original-native-gate-control', feature=feature,
                                 bundle=bundle, identifier=identifier, caller=through_caller, passed=True))
                patched_pc, patched_events = replay(True, feature, bundle, identifier, through_caller)
                native_pc, native_events = replay(False, False, bundle, identifier, through_caller)
                assert patched_pc == native_pc == end
                assert patched_events == [e for e in native_events if e != 'TCC/managedTCC-feature-query']
                assert not any('IPC' in e for e in patched_events)
                rows.append(dict(test='patched-matches-native-disabled-path', feature=feature,
                                 bundle=bundle, identifier=identifier, caller=through_caller, passed=True))

report = dict(passed=True, build='26C5035r', cases=len(rows),
              native_AppKit_method_and_caller_executed=True,
              matches_original_feature_disabled_branch=True, stack_and_callee_saved_registers_preserved=True,
              modeled_boundaries=['feature provider', 'ObjC dispatch', 'TCC IPC'],
              TCC_authorization_decisions_modified=False, PAC_emulated=False, device_tested=False, rows=rows)
args.output.write_text(json.dumps(report, indent=2) + '\n')
print('AppKit managedTCC native feature gate:', len(rows), 'cases passed')
