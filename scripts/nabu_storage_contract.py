#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reject mixed early/late NABU desktop storage artifacts."""
import struct
from native_macho import macho

KERNEL_SYMBOLS = ('_nabu_source_storage_start', '_nabu_register_storage_start')
MODULE_SYMBOL = '_nabu_storage_module_start'


def validate_module(evidence, info, own, native, imports):
    if (evidence.get('late_storage_start') is not True or
            evidence.get('automatic_storage_personalities') is not False):
        raise ValueError('NABU desktop module lacks the paired late storage contract')
    if any(p.get('IOClass') in ('NativeUfs', 'NativeRamBlock')
           for p in info.get('IOKitPersonalities', {}).values()):
        raise ValueError('NABU desktop module retains an automatic storage personality')
    if MODULE_SYMBOL not in own or any(name not in native for name in KERNEL_SYMBOLS):
        raise ValueError('late storage requires both compiled kernel bridge and module callback')
    if imports.get('_nabu_register_storage_start') != native['_nabu_register_storage_start']:
        raise ValueError('module callback registration is not bound to the compiled kernel bridge')


def audit_kernel(data, symbols):
    image = macho(data)
    def function(name):
        start = symbols[name]
        end = min(v for v in symbols.values() if v > start)
        for segment in image['segments']:
            if segment['va'] <= start < end <= segment['va'] + segment['filesize']:
                at = segment['fileoff'] + start - segment['va']
                return start, data[at:at + end - start]
        raise ValueError('late storage function has no bounded executable backing: ' + name)

    def calls(name):
        start, code = function(name)
        found = []
        for offset in range(0, len(code) - 3, 4):
            word, = struct.unpack_from('<I', code, offset)
            if word >> 26 != 0b100101:
                continue
            immediate = word & 0x3ffffff
            if immediate & 0x2000000:
                immediate -= 0x4000000
            found.append((start + offset, start + offset + immediate * 4))
        return found

    required = KERNEL_SYMBOLS + ('_bsd_init', '_vfsinit', '_OSKextLoadKextWithIdentifier')
    if any(name not in symbols for name in required):
        raise ValueError('linked kernel omitted late storage bridge or call targets')
    bsd_calls = calls('_bsd_init')
    vfs = [site for site, target in bsd_calls if target == symbols['_vfsinit']]
    late = [site for site, target in bsd_calls if target == symbols['_nabu_source_storage_start']]
    if len(vfs) != 1 or len(late) != 1 or late[0] <= vfs[0]:
        raise ValueError('compiled BSD must call late storage exactly once after vfsinit')
    names = {value: name for name, value in symbols.items()}
    pending, seen, loads = ['_nabu_source_storage_start'], set(), False
    while pending and len(seen) < 128:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        for _, target in calls(name):
            if target == symbols['_OSKextLoadKextWithIdentifier']:
                loads = True
            elif target in names and ('nabu' in names[target].lower() or 'Operations' in names[target]):
                pending.append(names[target])
    if not loads:
        raise ValueError('compiled late storage entry does not load its modules')
    return dict(vfsinit_call=hex(vfs[0]), storage_start_call=hex(late[0]),
                native_entry=hex(symbols['_nabu_source_storage_start']),
                callback_registration=hex(symbols['_nabu_register_storage_start']),
                compiled_calls_verified=True)
