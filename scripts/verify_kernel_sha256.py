#!/usr/bin/env python3
"""Execute the kernel image's own SHA-256 against known answers.

The packager used to rewrite every 4-byte word that looked like a B/BL opcode,
which turned SHA-256's K[31] = 0x14292967 into a branch and made every digest the
kernel computed wrong: correctly signed pages were rejected and pid 1 was killed
with `cs_validate_page ... actual != expected`. Comparing tables finds that, but
it does not prove the *implementation*, so this runs the image's machine code:

  * the initial state comes from the image (`_ccsha256_initial_state`);
  * the image's own `_ccsha256_ltc_compress` runs under Unicorn, one call per
    64-byte block (x0=state, x1=nblocks, x2=data - the ABI the disassembly
    shows, where the state words are read and written through x0);
  * the digest is compared with hashlib;
  * a second pass re-implements the schedule with the K table read out of the
    image, which isolates the constant table from the code.

Usage:
    verify_kernel_sha256.py IMAGE [--json OUT] [--page FILE:OFFSET:LENGTH]

Exit code 0 means every vector matched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys

import unicorn

LC_SEGMENT_64 = 0x19
LC_SYMTAB = 0x2
N_STAB = 0xE0

COMPRESS_SYMBOLS = ('_ccsha256_ltc_compress', '_ccsha256_compress', '_sha256_compress')
STATE_SYMBOLS = ('_ccsha256_initial_state', '_sha256_initial_state')
K_SYMBOLS = ('_ccsha256_K', '_sha256_K')

RETURN_MAGIC = 0x0000DEADBEEF0000
SCRATCH = 0x0000000100000000
SCRATCH_SIZE = 0x20000
STATE_AREA = SCRATCH + 0x8000
BLOCK_AREA = SCRATCH


def parse_image(data):
    magic, _cpu, _sub, _filetype, ncmds, _sizeofcmds, _flags, _res = struct.unpack_from(
        '<IiiIIIII', data, 0)
    if magic != 0xFEEDFACF:
        raise SystemExit('not a thin 64-bit Mach-O: magic %#x' % magic)
    segments, symtab = [], None
    cursor = 32
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from('<II', data, cursor)
        if cmd == LC_SEGMENT_64:
            (name, vmaddr, vmsize, fileoff, filesize, maxprot, initprot,
             nsects, flags) = struct.unpack_from('<16sQQQQiiII', data, cursor + 8)
            sections, at = [], cursor + 72
            for _ in range(nsects):
                sectname, segname, addr, size, offset = struct.unpack_from(
                    '<16s16sQQI', data, at)
                sections.append(dict(name=sectname.rstrip(b'\0').decode(),
                                     segment=segname.rstrip(b'\0').decode(),
                                     addr=addr, size=size, offset=offset,
                                     flags=struct.unpack_from('<I', data, at + 64)[0]))
                at += 80
            segments.append(dict(name=name.rstrip(b'\0').decode(), vmaddr=vmaddr,
                                 vmsize=vmsize, fileoff=fileoff, filesize=filesize,
                                 maxprot=maxprot, initprot=initprot, flags=flags,
                                 sections=sections))
        elif cmd == LC_SYMTAB:
            symoff, nsyms, stroff, strsize = struct.unpack_from('<IIII', data, cursor + 8)
            symtab = dict(symoff=symoff, nsyms=nsyms, stroff=stroff, strsize=strsize)
        cursor += cmdsize
    return segments, symtab


def symbols(data, symtab):
    table = {}
    if not symtab:
        return table
    for index in range(symtab['nsyms']):
        at = symtab['symoff'] + index * 16
        strx, ntype, _sect, _desc, value = struct.unpack_from('<IBBHQ', data, at)
        if ntype & N_STAB or not value:
            continue
        start = symtab['stroff'] + strx
        end = data.index(b'\0', start)
        table.setdefault(data[start:end].decode('utf-8', 'replace'), value)
    return table


def first_symbol(table, names):
    for name in names:
        if name in table:
            return name, table[name]
    return None, None


def read_va(data, segments, va, length):
    for segment in segments:
        if segment['vmaddr'] <= va < segment['vmaddr'] + segment['vmsize']:
            start = segment['fileoff'] + (va - segment['vmaddr'])
            return data[start:start + length]
    raise SystemExit('address %#x is not mapped by the image' % va)


def build_machine(data, segments):
    machine = unicorn.Uc(unicorn.UC_ARCH_ARM64, unicorn.UC_MODE_ARM)
    page = 0x4000

    def align_down(value):
        return value & ~(page - 1)

    def align_up(value):
        return (value + page - 1) & ~(page - 1)

    for segment in segments:
        if not segment['vmsize']:
            continue
        start = align_down(segment['vmaddr'])
        size = align_up(segment['vmsize'] + (segment['vmaddr'] - start))
        try:
            machine.mem_map(start, size, unicorn.UC_PROT_ALL)
        except unicorn.UcError:
            continue
        if segment['filesize']:
            machine.mem_write(segment['vmaddr'],
                              data[segment['fileoff']:segment['fileoff'] + segment['filesize']])
    machine.mem_map(SCRATCH, SCRATCH_SIZE, unicorn.UC_PROT_ALL)
    return machine


def digest_with_image(machine, compress_va, message, initial_state):
    """SHA-256 of `message`, computed by the image's own compress function."""
    machine.mem_write(STATE_AREA, struct.pack('<8I', *initial_state))
    padded = bytearray(message)
    bit_length = len(message) * 8
    padded.append(0x80)
    while len(padded) % 64 != 56:
        padded.append(0)
    padded += struct.pack('>Q', bit_length)

    words = list(initial_state)
    for offset in range(0, len(padded), 64):
        machine.mem_write(BLOCK_AREA, bytes(padded[offset:offset + 64]))
        machine.reg_write(unicorn.arm64_const.UC_ARM64_REG_X0, STATE_AREA)
        machine.reg_write(unicorn.arm64_const.UC_ARM64_REG_X1, 1)
        machine.reg_write(unicorn.arm64_const.UC_ARM64_REG_X2, BLOCK_AREA)
        machine.reg_write(unicorn.arm64_const.UC_ARM64_REG_LR, RETURN_MAGIC)
        machine.reg_write(unicorn.arm64_const.UC_ARM64_REG_SP,
                          SCRATCH + SCRATCH_SIZE - 0x1000)
        machine.emu_start(compress_va, RETURN_MAGIC, count=2000000)
        words = list(struct.unpack('<8I', machine.mem_read(STATE_AREA, 32)))
    return b''.join(struct.pack('>I', word) for word in words)


def sha256_with_image_k_table(k, message):
    """SHA-256 with the image's K table but a clean software schedule."""
    h0 = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
          0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]
    bit_length = len(message) * 8
    padded = bytearray(message)
    padded.append(0x80)
    while len(padded) % 64 != 56:
        padded.append(0)
    padded += struct.pack('>Q', bit_length)
    mask = 0xFFFFFFFF

    def rotr(value, bits):
        return ((value >> bits) | (value << (32 - bits))) & mask

    state = list(h0)
    for offset in range(0, len(padded), 64):
        w = list(struct.unpack('>16I', padded[offset:offset + 64]))
        for i in range(16, 64):
            s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3)
            s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10)
            w.append((w[i - 16] + s0 + w[i - 7] + s1) & mask)
        a, b, c, d, e, f, g, h = state
        for i in range(64):
            s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)
            ch = (e & f) ^ (~e & g)
            temp1 = (h + s1 + ch + k[i] + w[i]) & mask
            s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)
            maj = (a & b) ^ (a & c) ^ (b & c)
            temp2 = (s0 + maj) & mask
            h, g, f, e, d, c, b, a = (g, f, e, (d + temp1) & mask, c, b, a,
                                      (temp1 + temp2) & mask)
        state = [(x + y) & mask for x, y in zip(state, (a, b, c, d, e, f, g, h))]
    return b''.join(struct.pack('>I', word) for word in state)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('image')
    parser.add_argument('--json', dest='json_path')
    parser.add_argument('--page', action='append', default=[],
                        help='FILE:OFFSET:LENGTH hashed through the image code')
    parser.add_argument('--k-table-offset', type=lambda v: int(v, 0), default=None,
                        help='file offset of a placed K table (for example the copy a'
                             ' loaded kext uses); it is written over the native table'
                             ' in emulated memory so the digest reflects what runs')
    args = parser.parse_args()

    data = open(args.image, 'rb').read()
    segments, symtab = parse_image(data)
    table = symbols(data, symtab)
    compress_name, compress_va = first_symbol(table, COMPRESS_SYMBOLS)
    state_name, state_va = first_symbol(table, STATE_SYMBOLS)
    k_name, k_va = first_symbol(table, K_SYMBOLS)
    if not compress_va or not state_va:
        raise SystemExit('image has no compress/initial-state symbol')

    initial_state = list(struct.unpack('<8I', read_va(data, segments, state_va, 32)))
    machine = build_machine(data, segments)
    placed_table = None
    if args.k_table_offset is not None and k_va:
        placed_table = struct.unpack_from('<64I', data, args.k_table_offset)
        machine.mem_write(k_va, struct.pack('<64I', *placed_table))
    failures, vectors = [], []

    messages = [b'', b'abc', b'a' * 55, b'a' * 56, b'a' * 64, bytes(range(256)),
                b'The quick brown fox jumps over the lazy dog']
    if b'\x00' * 0 == b'':
        messages.append(bytes(1024))

    for message in messages:
        try:
            digest = digest_with_image(machine, compress_va, message, initial_state)
        except unicorn.UcError as error:
            failures.append('emulation fault on %d-byte vector: %s' % (len(message), error))
            continue
        expected = hashlib.sha256(message).digest()
        ok = digest == expected
        vectors.append(dict(kind='message', length=len(message), sha256=digest.hex(),
                            expected=expected.hex(), ok=ok))
        if not ok:
            failures.append('SHA256(%d bytes) mismatch' % len(message))

    for spec in args.page:
        path, offset, length = spec.rsplit(':', 2)
        blob = open(path, 'rb').read()[int(offset, 0):][:int(length, 0)]
        try:
            digest = digest_with_image(machine, compress_va, blob, initial_state)
        except unicorn.UcError as error:
            failures.append('emulation fault on page %s: %s' % (spec, error))
            continue
        expected = hashlib.sha256(blob).digest()
        ok = digest == expected
        vectors.append(dict(kind='page', page=spec, length=len(blob),
                            sha256=digest.hex(), expected=expected.hex(), ok=ok))
        if not ok:
            failures.append('page %s mismatch' % spec)

    k_table = list(struct.unpack('<64I', read_va(data, segments, k_va, 256))) if k_va else []
    k_digest = sha256_with_image_k_table(k_table, b'abc') if k_table else None
    k_ok = (k_digest == hashlib.sha256(b'abc').digest()) if k_digest else None
    if k_ok is False:
        failures.append('K table inside the image does not produce SHA256("abc")')

    report = dict(image=args.image, compress=compress_name,
                  compress_va=None if not compress_va else '%#x' % compress_va,
                  state=state_name, k_table=k_name,
                  k_table_sha256_of_abc=None if not k_digest else k_digest.hex(),
                  k_table_ok=k_ok,
                  k_table_element_31=None if not k_table else '%#010x' % k_table[31],
                  placed_table_offset=None if args.k_table_offset is None
                  else '%#x' % args.k_table_offset,
                  placed_table_element_31=None if not placed_table
                  else '%#010x' % placed_table[31],
                  vectors=vectors, failures=failures,
                  verdict='PASS' if not failures else 'FAIL')
    print(json.dumps(report, indent=2))
    if args.json_path:
        open(args.json_path, 'w').write(json.dumps(report, indent=2) + '\n')
    return 0 if not failures else 1


if __name__ == '__main__':
    sys.exit(main())
