# SPDX-License-Identifier: GPL-3.0-or-later
"""Describe a split ordinary ARM64 kext header with separately placed code."""
import struct


def write(driver, output, at):
    data, image, base = driver['data'], driver['image'], driver['base']
    header_va = driver['header_va']
    commands = []

    def translated(offset):
        found = [s for s in image['segments'] if s['fileoff'] <= offset < s['fileoff'] + s['filesize']]
        if len(found) != 1:
            raise ValueError('UFS linkedit offset is not backed by one segment')
        return at(base + found[0]['va'] + offset - found[0]['fileoff'], 1)

    # The sparse kalloc index must point inside __PRELINK_TEXT. Only this header
    # is placed there; the original source module's code remains contiguous at
    # its linked location, preserving its PC-relative instructions.
    constructors = []
    # lld's modern chained output stores 32-bit initializer offsets. XNU
    # OSRuntime consumes S_MOD_INIT_FUNC_POINTERS, so publish actual pointers
    # in this reserved header page, outside all source code and load commands.
    for segment in image['segments']:
        for section in segment['sections']:
            if section['name'] == '__init_offsets':
                if section['size'] % 4: raise ValueError('misaligned initializer offsets')
                for offset in range(0, section['size'], 4):
                    target = struct.unpack_from('<I', data, section['fileoff'] + offset)[0]
                    if not any(s['initprot'] & 4 and s['va'] <= target < s['va'] + s['filesize']
                               for s in image['segments']):
                        raise ValueError('initializer offset is not actual source code')
                    constructors.append(base + target)
    if len(constructors) != 2:
        raise ValueError('requires the two real UFS and RAM Data translation-unit constructors')
    init_va = header_va + 0x3000
    commands.append(struct.pack('<II16s4Q4I', 0x19, 152, b'__TEXT', header_va, 0x4000,
                                at(header_va, 1), 0x4000, 1, 1, 1, 0) +
                    struct.pack('<16s16sQQ8I', b'__mod_init_func', b'__TEXT', init_va,
                                8 * len(constructors), at(init_va, 1), 3, 0, 0, 9, 0, 0, 0))
    struct.pack_into('<%dQ' % len(constructors), output, at(init_va, 8 * len(constructors)), *constructors)
    driver['constructors'] = dict(section=hex(init_va), targets=[hex(v) for v in constructors],
                                  source='actual __init_offsets converted for XNU OSRuntime')
    for cmd, pos, length in image['commands']:
        raw = bytearray(data[pos:pos + length])
        if cmd == 0x19:
            name = data[pos + 8:pos + 24].rstrip(b'\0').decode()
            s = next(s for s in image['segments'] if s['name'] == name)
            struct.pack_into('<QQQ', raw, 24, base + s['va'], s['size'], at(base + s['va'], 1))
            if name == '__TEXT':
                raw[8:24] = b'__TEXT_EXEC'.ljust(16, b'\0')
            for i, section in enumerate(s['sections']):
                sec = 72 + i * 80
                if name == '__TEXT':
                    raw[sec + 16:sec + 32] = b'__TEXT_EXEC'.ljust(16, b'\0')
                struct.pack_into('<Q', raw, sec + 32, base + section['va'])
                if section['fileoff'] and section['size']:
                    struct.pack_into('<I', raw, sec + 48, at(base + section['va'], 1))
        elif cmd == 2:
            symoff, count, strings, _ = struct.unpack_from('<4I', raw, 8)
            struct.pack_into('<I', raw, 8, translated(symoff))
            struct.pack_into('<I', raw, 16, translated(strings))
            for i in range(count):
                row = symoff + i * 16
                _, kind, _, _, value = struct.unpack_from('<IBBHQ', data, row)
                if not kind & 0xe0 and kind & 0xe == 0xe:
                    struct.pack_into('<Q', output, translated(row) + 8, base + value)
        elif cmd == 0xb:
            for field in (32, 40, 48, 56, 64, 72):
                old, = struct.unpack_from('<I', raw, field)
                if old:
                    struct.pack_into('<I', raw, field, translated(old))
        elif cmd in (0x1d, 0x1e, 0x26, 0x29, 0x2b, 0x80000033, 0x80000034):
            old, size = struct.unpack_from('<II', raw, 8)
            if size:
                struct.pack_into('<I', raw, 8, translated(old))
        commands.append(raw)
    if not any(c == 0x1e for c, _, _ in image['commands']):
        commands.append(struct.pack('<4I', 0x1e, 16, 0, 0))
    rebuilt = b''.join(commands)
    if len(rebuilt) + 32 > 0x3000:
        raise ValueError('UFS Mach-O header exceeds its reserved text page')
    header = bytearray(data[:32])
    flags, = struct.unpack_from('<I', header, 24)
    struct.pack_into('<III', header, 12, 11, len(commands), len(rebuilt))
    struct.pack_into('<I', header, 24, flags | 0x80000000)
    p = at(header_va, 32 + len(rebuilt))
    output[p:p + 32 + len(rebuilt)] = header + rebuilt
    extent = max(base + s['va'] + s['size'] for s in image['segments']) - header_va
    struct.pack_into('<QQQ', output, at(driver['kmod'] + 156, 24), header_va, extent, 0x4000)
