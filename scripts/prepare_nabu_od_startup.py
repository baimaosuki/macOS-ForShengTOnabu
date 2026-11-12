#!/usr/bin/env python3
"""Prepare the exact 25F71 callback-free OD startup queue-fence candidate.

Only create_ext's first connection call selects a private relocated copy of
_odconnection_connect. The original general connection routine is unchanged.
The copy drains the original serial operations queue after the original state
queue submission returns, before constructing the original five-second deadline.

Output retains a stale Apple signature: the packager MUST re-sign it and add
the resulting CodeDirectory hash to its paired trust cache before deployment.
This tool never installs, signs, packages, or writes a device.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from native_macho import macho

INPUT_SHA256 = '5e1abdd7d2fd30ee95c8bb07fee1259d5bc7f7cc2b047c1aa415dc0b539a1452'
CONNECT = 0x1000218F8
CONNECT_END = 0x100021AAC
CREATE_CALL = 0x1000215E0
INSERT_BEFORE = 0x100021A10
ISLAND = 0x10007BD50
ISLAND_END = 0x10007C000
BARRIER_SYNC = 0x10006401C
GUARD_BYTES = 12
FENCE_BYTES = 36


def digest(data):
    return hashlib.sha256(data).hexdigest()


def signed(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def immediate(value, bits):
    if not -(1 << (bits - 1)) <= value < (1 << (bits - 1)):
        raise ValueError('AArch64 immediate out of range')
    return value & ((1 << bits) - 1)


def branch(site, target, link=False):
    if (site | target) & 3:
        raise ValueError('unaligned AArch64 branch')
    return (0x94000000 if link else 0x14000000) | immediate((target - site) // 4, 26)


def reference(word, pc):
    if word & 0x7C000000 == 0x14000000:
        return 'branch26', pc + 4 * signed(word & 0x3FFFFFF, 26)
    if word & 0xFF000010 == 0x54000000 or word & 0x7E000000 == 0x34000000:
        return 'branch19', pc + 4 * signed((word >> 5) & 0x7FFFF, 19)
    if word & 0x7E000000 == 0x36000000:
        return 'branch14', pc + 4 * signed((word >> 5) & 0x3FFF, 14)
    if word & 0x1F000000 == 0x10000000:
        delta = signed((((word >> 5) & 0x7FFFF) << 2) | ((word >> 29) & 3), 21)
        return ('adrp', (pc & ~0xFFF) + (delta << 12)) if word & 0x80000000 else ('adr', pc + delta)
    if word & 0x3B000000 == 0x18000000:
        return 'literal19', pc + 4 * signed((word >> 5) & 0x7FFFF, 19)
    return None


def relocate(word, old_pc, new_pc, remap=lambda target: target):
    ref = reference(word, old_pc)
    if ref is None:
        return word
    kind, target = ref
    # ADRP addresses pages in the original image. Those pages do not move.
    target = target if kind == 'adrp' else remap(target)
    if kind == 'branch26':
        return (word & ~0x3FFFFFF) | immediate((target - new_pc) // 4, 26)
    if kind in ('branch19', 'literal19', 'branch14'):
        bits = 14 if kind == 'branch14' else 19
        if (target - new_pc) & 3:
            raise ValueError('unaligned relative instruction target')
        mask = ((1 << bits) - 1) << 5
        return (word & ~mask) | (immediate((target - new_pc) // 4, bits) << 5)
    delta = ((target - (new_pc & ~0xFFF)) >> 12) if kind == 'adrp' else target - new_pc
    encoded = immediate(delta, 21)
    return (word & ~((3 << 29) | (0x7FFFF << 5))) | ((encoded & 3) << 29) | ((encoded >> 2) << 5)


def offset(image, address, size=4):
    carriers = [s for s in image['segments'] if s['va'] <= address and
                address + size <= s['va'] + s['filesize']]
    if len(carriers) != 1:
        raise ValueError('address is not uniquely backed by the Mach-O')
    return carriers[0]['fileoff'] + address - carriers[0]['va']


def thin_slice(data):
    """Preserve all fat slices, headers and extents; patch only exact ARM64E."""
    if data[:4] == b'\xcf\xfa\xed\xfe':
        return 0, len(data)
    if data[:4] not in (b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'):
        raise ValueError('requires a thin Mach-O or big-endian universal Mach-O')
    count = struct.unpack_from('>I', data, 4)[0]
    wide = data[:4] == b'\xca\xfe\xba\xbf'
    stride = 32 if wide else 20
    if not 0 < count <= 32 or 8 + count * stride > len(data):
        raise ValueError('invalid universal architecture table')
    found, ranges = [], []
    for index in range(count):
        values = struct.unpack_from('>IIQQII' if wide else '>IIIII', data, 8 + index * stride)
        cpu, subtype, start, size = values[:4]
        if start < 8 + count * stride or size < 32 or start + size > len(data):
            raise ValueError('invalid universal slice extent')
        if any(start < b and a < start + size for a, b in ranges):
            raise ValueError('overlapping universal slices')
        ranges.append((start, start + size))
        if cpu == 0x100000C and subtype & 0xFF == 2:
            found.append((start, size))
    if len(found) != 1:
        raise ValueError('requires exactly one ARM64E slice')
    return found[0]


def make_island(original, image):
    def mapped(pc):
        if CONNECT <= pc < CONNECT_END:
            return ISLAND + GUARD_BYTES + pc - CONNECT + (FENCE_BYTES if pc >= INSERT_BEFORE else 0)
        return pc

    noop = ISLAND + GUARD_BYTES + CONNECT_END - CONNECT + FENCE_BYTES
    descriptor = (noop + 4 + 7) & ~7
    # x0 is checked before dereferencing; non-null callback falls back to the
    # unchanged general routine. Only the create_ext call targets this guard.
    words = [relocate(0xB4000000, 0, ISLAND, lambda _: CONNECT),
             0xF9408C08,  # ldr x8, [x0, #0x118]
             relocate(0xB5000008, 0, ISLAND + 8, lambda _: CONNECT)]
    proof = []
    for pc in range(CONNECT, CONNECT_END, 4):
        if pc == INSERT_BEFORE:
            site = ISLAND + len(words) * 4
            # The original stack block's signed ISA stays at sp+8. Replace its
            # flags, invoke and descriptor with a capture-free synchronous fence.
            fence = [0xF9000BFF,  # str xzr, [sp, #0x10]
                     0xF9409E60,  # ldr x0, [x19, #0x138]
                     0x910063E8,  # add x8, sp, #0x18
                     relocate(0x10000010, 0, site + 12, lambda _: noop),
                     0xDAC10110,  # pacia x16, x8
                     relocate(0x10000008, 0, site + 20, lambda _: descriptor),
                     0xA901A3F0,  # stp x16, x8, [sp, #0x18]
                     0x910023E1,  # add x1, sp, #8
                     branch(site + 32, BARRIER_SYNC, link=True)]
            words.extend(fence)
        word = struct.unpack_from('<I', original, offset(image, pc))[0]
        new_pc = ISLAND + len(words) * 4
        if new_pc != mapped(pc):
            raise ValueError('clone instruction layout disagrees')
        rewritten = relocate(word, pc, new_pc, mapped)
        words.append(rewritten)
        old_ref, new_ref = reference(word, pc), reference(rewritten, new_pc)
        if old_ref:
            expected = old_ref[1] if old_ref[0] == 'adrp' else mapped(old_ref[1])
            if new_ref != (old_ref[0], expected):
                raise ValueError('relocation does not preserve its reference')
            proof.append(dict(source=hex(pc), clone=hex(new_pc), kind=old_ref[0],
                              original_target=hex(old_ref[1]), target=hex(expected)))
    blob = struct.pack('<' + 'I' * len(words), *words)
    if ISLAND + len(blob) != noop:
        raise ValueError('no-op address differs')
    blob += struct.pack('<I', 0xD65F03C0)  # ret: no PAC prologue for a leaf
    blob += bytes(descriptor - ISLAND - len(blob))
    blob += struct.pack('<QQ', 0, 32)  # Block descriptor: reserved, literal size
    return blob, dict(entry=hex(ISLAND), clone=hex(mapped(CONNECT)),
                      fence=hex(mapped(INSERT_BEFORE) - FENCE_BYTES), noop=hex(noop),
                      descriptor=hex(descriptor), relocations=proof)


def patch(data):
    start, size = thin_slice(data)
    source = data[start:start + size]
    if digest(source) != INPUT_SHA256:
        raise ValueError('requires the exact original 25F71 opendirectoryd ARM64E slice')
    image = macho(source, 'arm64e')
    text = next(s for s in image['segments'] if s['name'] == '__TEXT')
    if text['initprot'] != 5 or not text['va'] <= ISLAND < ISLAND_END <= text['va'] + text['filesize']:
        raise ValueError('island is not inside the original RX segment')
    if any(sec['va'] + sec['size'] > ISLAND for sec in text['sections']):
        raise ValueError('island overlaps an original section')
    cave = offset(image, ISLAND, ISLAND_END - ISLAND)
    if any(source[cave:cave + ISLAND_END - ISLAND]):
        raise ValueError('original RX padding is not zero')
    call = offset(image, CREATE_CALL)
    if struct.unpack_from('<I', source, call)[0] != branch(CREATE_CALL, CONNECT, link=True):
        raise ValueError('create_ext first-connect call differs')
    blob, proof = make_island(source, image)
    if len(blob) > ISLAND_END - ISLAND:
        raise ValueError('private clone exceeds original RX padding')
    output = bytearray(data)
    output[start + cave:start + cave + len(blob)] = blob
    struct.pack_into('<I', output, start + call, branch(CREATE_CALL, ISLAND, link=True))
    modified = bytes(output)
    return modified, dict(input_sha256=digest(data), output_sha256=digest(modified),
        slice_offset=start, slice_size=size, slice_input_sha256=INPUT_SHA256,
        slice_output_sha256=digest(modified[start:start + size]), island_bytes=len(blob),
        create_call=hex(CREATE_CALL), general_connect_unchanged=True,
        original_worker_unchanged=True, original_status_and_signal_unchanged=True,
        original_semaphore_deadline_seconds=5, callback_free_freshcreate_only=True,
        deadline_begins_after_operations_fence=True, synchronous_fence_has_timeout=False,
        freshcreate_end_to_end_time_bounded=False, clone_has_compact_unwind_entry=False,
        limitations=['Fresh callback-free initialization can wait indefinitely for the original operations queue.',
                     'The original five-second semaphore deadline remains after the fence; it no longer bounds total fresh initialization.',
                     'The clone preserves frame pointers and PAC returns but has no compact-unwind entry; exceptional unwinding may be limited.'],
        device_tested=False,
        signature_valid=False, requires_resign_and_paired_trustcache=True, proof=proof)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if args.input.resolve() in (args.output.resolve(), args.report.resolve()) or args.output.resolve() == args.report.resolve():
        raise ValueError('input, output and report must be different files')
    if args.output.exists() or args.report.exists():
        raise ValueError('refusing to overwrite candidate or evidence')
    output, report = patch(args.input.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(output)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
