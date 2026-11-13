#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only admission of the exact 25F71 native VSync clock and sender ABI.

No code, exports, ports, power state, timing fields or notifications are patched.
Hashes cover the reviewed original instructions, not a host reimplementation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from inspect_macos26_security import macho, exports, chains, backed_offset

FAMILY = 'com.apple.iokit.IOMobileGraphicsFamily'
UUID = 'EA3273B7-AF0F-3E06-B0F5-072DFCDD5395'
LEGACY = '__ZN25IOMobileFramebufferLegacy'
REVIEWED = {
    'note': (LEGACY + '12note_vbl_ISREj', 0xfffffe000a89bc5c, 0xd8,
             'c07fddb729f46e7963bdf25c328b3376e31bdf921773c468fb9c0be0e98e345b'),
    'sender': (LEGACY + '17swap_notify_gatedEyyy', 0xfffffe000a894cc4, 8,
               'fa260dbb4feb7accf6f0415cb6b5445a6404edb17962d8c29589e90da65176e1'),
    'dispatch': (LEGACY + '26swap_notify_dispatch_gatedEyyy', 0xfffffe000a89bd34, 0x104,
                 'ef5df723425d8980c1793832aa624dc9f8c145e6aead31a62c25f5d502b3e842'),
    'reset': (LEGACY + '23reset_vbl_timings_gatedEv', 0xfffffe000a893170, 12,
              'aba611ab882e9b3b253d06093a96af156f67bd834410da130b0eac1703bed43d'),
    'clock': ('__ZNK25IOMobileFramebufferLegacy25get_current_absolute_timeEv',
              0xfffffe000a8a0f48, 8,
              '9720eaa891d28ad37570def004fe937aa383bfd42e487cc5e8e5346fcad6d99c'),
    'convert': ('__ZNK25IOMobileFramebufferLegacy19ns_to_absolute_timeEyPy',
                0xfffffe000a8a116c, 16,
                '22d11de0be063d324aceb749427fb8447e18583e1ce5a38afc9d9738136450ed'),
}
# Exact delivery postimage: only the listed PAC operations differ from the
# reviewed original bodies. Reversing these exact sites must recover the source
# hash. This does not admit arbitrary NOPs or relaxed instruction matching.
PAC_POSTIMAGE = {
    'note': ('5bfc870c54b4990c2e24148a2d62b59a77934895841ab077d6425bf56c0146fe', {
        0x2c: ('301ac1da', '1f2003d5'), 0x40: ('10093fd7', '00013fd6'),
        0x88: ('1019c1da', '1f2003d5'), 0xa4: ('10093fd7', '00013fd6'),
        0xd4: ('ff0f5fd6', 'c0035fd6')}),
    'dispatch': ('8299e0bc48a0c20fc5d59dbac4a9407ed5c2a86a0f2c53c2355c3d9e360a8019', {
        0x8c: ('ff0f5fd6', 'c0035fd6'), 0x9c: ('301ac1da', '1f2003d5'),
        0xb4: ('10093fd7', '00013fd6'), 0xd4: ('301ac1da', '1f2003d5'),
        0xf8: ('10093fd7', '00013fd6')}),
}


def inspect(path):
    data = path.read_bytes()
    kc = macho(data)
    family = macho(data, kc['entries'][FAMILY][1])
    if family['uuid'] != UUID:
        raise ValueError('VSync candidate requires the reviewed 25F71 family UUID')
    names = {n: a for a, ns in exports(data, family).items() for n in ns}
    # Clock/converter are private inherited vtable entries. They must match
    # original symbol+slot+PAC, but unlike note/sender need no new direct bind.
    command = next(p for c, p, _ in family['commands'] if c == 2)
    off, count, strings, size = struct.unpack_from('<4I', data, command + 8)
    symbols = {}
    for index in range(count):
        name, kind, section, _, address = struct.unpack_from('<IBBHQ', data, off + index * 16)
        if not name or not address or kind & 0x0e != 0x0e or not section:
            continue
        if name >= size:
            raise ValueError('VSync original symbol outside string table')
        end = data.find(b'\0', strings + name, strings + size)
        if end < 0:
            raise ValueError('VSync original symbol not terminated')
        symbols[data[strings + name:end].decode()] = address
    kernel = macho(data, kc['entries']['com.apple.kernel'][1])
    kernel_names = {n: a for a, ns in exports(data, kernel).items() for n in ns}

    def read(address, size):
        at = backed_offset(family['segments'], address, size, len(data))
        return data[at:at + size]

    def branch(address, target, link=False):
        word, = struct.unpack('<I', read(address, 4))
        if word >> 26 != (0b100101 if link else 0b000101):
            raise ValueError('VSync original branch opcode differs')
        imm = word & 0x03ffffff
        if imm & 0x02000000:
            imm -= 0x04000000
        if address + imm * 4 != target:
            raise ValueError('VSync original branch target differs')

    note_address, note_size = REVIEWED['note'][1:3]
    note_hash = hashlib.sha256(read(note_address, note_size)).hexdigest()
    if note_hash == REVIEWED['note'][3]:
        profile = 'original_arm64e'
    elif note_hash == PAC_POSTIMAGE['note'][0]:
        profile = 'reviewed_pac_postimage'
    else:
        raise ValueError('VSync original reviewed instructions differ: native note')
    functions = {}
    for label, (symbol, address, size, expected_hash) in REVIEWED.items():
        if symbols.get(symbol) != address or (label not in ('clock', 'convert')
                                             and names.get(symbol) != address):
            raise ValueError(f'VSync original exact symbol/export differs: {symbol}')
        digest = hashlib.sha256(read(address, size)).hexdigest()
        actual_expected = (PAC_POSTIMAGE[label][0]
                           if profile == 'reviewed_pac_postimage' and label in PAC_POSTIMAGE
                           else expected_hash)
        if digest != actual_expected:
            raise ValueError(f'VSync original reviewed instructions differ: {symbol}')
        if profile == 'reviewed_pac_postimage' and label in PAC_POSTIMAGE:
            restored = bytearray(read(address, size))
            for offset, (source, postimage) in PAC_POSTIMAGE[label][1].items():
                if restored[offset:offset + 4] != bytes.fromhex(postimage):
                    raise ValueError('VSync PAC postimage exact site differs')
                restored[offset:offset + 4] = bytes.fromhex(source)
            if hashlib.sha256(restored).hexdigest() != expected_hash:
                raise ValueError('VSync PAC postimage does not recover original body')
        functions[label] = dict(symbol=symbol, address=hex(address), size=size,
                                sha256=digest, exported=names.get(symbol) == address)

    table = names['__ZTV25IOMobileFramebufferLegacy']
    wanted = {table + 16 + slot * 8: slot for slot in (349, 398)}
    pointers = {p['site']: p for p in chains(data, kc) if p['site'] in wanted}
    slots = []
    for site, slot in wanted.items():
        pointer = pointers[site]
        label, diversity = ('clock', 0x9562) if slot == 349 else ('convert', 0x4f41)
        metadata = {key: pointer[key] for key in
                    ('authenticated', 'key', 'address_diversity', 'diversity')}
        expected_metadata = (dict(authenticated=True, key=0,
                                  address_diversity=True, diversity=diversity)
                             if profile == 'original_arm64e' else
                             dict(authenticated=False, key=None,
                                  address_diversity=None, diversity=None))
        if pointer['target'] != REVIEWED[label][1] or metadata != expected_metadata:
            raise ValueError(f'VSync original virtual slot/PAC differs: {slot}')
        slots.append(dict(slot=slot, **pointer))
    branch(REVIEWED['clock'][1] + 4, kernel_names['_mach_continuous_time'])
    branch(REVIEWED['convert'][1] + 12, kernel_names['_nanoseconds_to_absolutetime'])
    branch(REVIEWED['sender'][1] + 4, REVIEWED['dispatch'][1])
    # Original completion path: if type5 head != NULL, load Count/time/interval
    # from family state and call the original dispatcher. This qualifies their
    # semantics independently of either port's documentation and host mocks.
    address = 0xfffffe000a89479c
    expected = bytes.fromhex('68064cf9a80000b4610a50a9638a40f9e00313aa611d0094')
    if read(address, len(expected)) != expected:
        raise ValueError('VSync original Count/time/interval call-site loads differ')
    branch(address + 20, REVIEWED['dispatch'][1], link=True)
    return dict(kernelcache_sha256=hashlib.sha256(data).hexdigest(), family=FAMILY,
                family_uuid=family['uuid'], instruction_profile=profile,
                functions=functions, virtual_slots=slots,
                clock='_mach_continuous_time', first_period_units='nanoseconds',
                native_state={'count': '0x100', 'last_native_time': '0x108',
                              'interval': '0x110', 'type5_head': '0x1808'},
                sender_words=['count', 'last_native_time', 'interval'],
                sender_owns_encoding=True, timer_reuses_existing_workloop=True,
                native_callsite=dict(address=hex(address), bytes=expected.hex()),
                qualification='exact original ABI; host tests cover adapter; device delivery unverified')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernelcache', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = json.dumps(inspect(args.kernelcache), indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report)
    else:
        print(report, end='')


if __name__ == '__main__':
    main()
