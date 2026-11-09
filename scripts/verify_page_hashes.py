#!/usr/bin/env python3
"""Recompute the CodeDirectory page hashes of a signed Mach-O and compare them
with the bytes actually present in the file.

Offline half of the "CS_KILLED ... rejecting invalid page" triage: it answers
whether the file content still matches its own embedded signature, without
needing the kernel that will consume it.

Mach-O headers and load commands are little endian; code-signing blobs are big
endian (as in Apple's own structs, OS_SwapBigToHostInt32).

Usage:
    verify_page_hashes.py FILE [FILE ...] [--json] [--chunk-pages 4]

Exit status is 0 unless --expect-clean is given and something does not match.
"""
import argparse
import hashlib
import json
import struct
import sys

MH_MAGIC_64 = 0xfeedfacf
LC_CODE_SIGNATURE = 0x1d

CSMAGIC_EMBEDDED_SIGNATURE = 0xfade0cc0
CSMAGIC_CODEDIRECTORY = 0xfade0c02

HASH_TYPES = {
    1: ('sha1', hashlib.sha1, 20),
    2: ('sha256', hashlib.sha256, 32),
    3: ('sha256-truncated', hashlib.sha256, 20),
    4: ('sha384', hashlib.sha384, 48),
    5: ('sha512', hashlib.sha512, 64),
}


def parse_macho(data):
    if len(data) < 32:
        raise SystemExit('file is shorter than a Mach-O header')
    magic, = struct.unpack_from('<I', data, 0)
    if magic != MH_MAGIC_64:
        raise SystemExit('not a thin little-endian 64-bit Mach-O (magic 0x%08x)' % magic)
    ncmds, sizeofcmds = struct.unpack_from('<II', data, 16)
    at = 32
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from('<II', data, at)
        if cmdsize < 8:
            raise SystemExit('load command with bogus size %d at offset %d' % (cmdsize, at))
        if cmd == LC_CODE_SIGNATURE:
            dataoff, datasize = struct.unpack_from('<II', data, at + 8)
            return dataoff, datasize
        at += cmdsize
    return None, None


def parse_codedirectory(data, dataoff, datasize):
    magic, length, count = struct.unpack_from('>III', data, dataoff)
    if magic != CSMAGIC_EMBEDDED_SIGNATURE:
        raise SystemExit('no embedded signature superblob (magic 0x%08x)' % magic)
    cd_at = None
    blobs = []
    for index in range(count):
        s_type, s_offset = struct.unpack_from('>II', data, dataoff + 12 + index * 8)
        blobs.append(s_type)
        if s_type == 0:
            cd_at = dataoff + s_offset
    if cd_at is None:
        raise SystemExit('superblob has no primary CodeDirectory (blobs %s)'
                         % [hex(b) for b in blobs])
    (cd_magic, cd_length, version, flags, hash_offset, ident_offset,
     n_special, n_code, code_limit) = struct.unpack_from('>IIIIIIIII', data, cd_at)
    if cd_magic != CSMAGIC_CODEDIRECTORY:
        raise SystemExit('CodeDirectory magic mismatch (0x%08x)' % cd_magic)
    hash_size, hash_type, platform, page_log2, spare2 = struct.unpack_from(
        '>BBBBI', data, cd_at + 36)
    if hash_type not in HASH_TYPES:
        raise SystemExit('unsupported hash type %d' % hash_type)
    return {
        'cd_offset': cd_at,
        'cd_length': cd_length,
        'version': version,
        'flags': flags,
        'hash_offset': hash_offset,
        'ident_offset': ident_offset,
        'n_special': n_special,
        'n_code': n_code,
        'code_limit': code_limit,
        'hash_size': hash_size,
        'hash_type': hash_type,
        'page_log2': page_log2,
        'blob_types': [hex(b) for b in blobs],
    }


def verify(path, chunk_pages=4):
    data = open(path, 'rb').read()
    dataoff, datasize = parse_macho(data)
    if dataoff is None:
        return {'path': path, 'signed': False, 'ok': False,
                'reason': 'unsigned: no LC_CODE_SIGNATURE'}
    cd = parse_codedirectory(data, dataoff, datasize)
    name, fn, digest_size = HASH_TYPES[cd['hash_type']]
    if cd['hash_size'] != digest_size:
        raise SystemExit('%s: hashSize %d does not match %s digest size %d'
                         % (path, cd['hash_size'], name, digest_size))
    page = 1 << cd['page_log2']
    limit = cd['code_limit']
    if limit > len(data):
        raise SystemExit('%s: codeLimit %d exceeds file size %d' % (path, limit, len(data)))

    mismatches = []
    for slot in range(cd['n_code']):
        start = slot * page
        end = min(start + page, limit)
        actual = fn(data[start:end]).digest()
        stored_at = cd['cd_offset'] + cd['hash_offset'] + slot * cd['hash_size']
        expected = data[stored_at:stored_at + cd['hash_size']]
        if actual != expected:
            mismatches.append({'slot': slot, 'offset': start, 'length': end - start,
                               'actual': actual.hex(), 'expected': expected.hex()})

    chunks = {}
    for bad in mismatches:
        chunks.setdefault(bad['slot'] // chunk_pages, []).append(bad['slot'])

    cd_bytes = data[cd['cd_offset']:cd['cd_offset'] + cd['cd_length']]
    cd_actual = fn(cd_bytes).digest()
    cd_stored_at = cd['cd_offset'] + cd['hash_offset'] - cd['hash_size']
    cd_expected = data[cd_stored_at:cd_stored_at + cd['hash_size']]

    return {
        'path': path,
        'signed': True,
        'ok': not mismatches,
        'hash': name,
        'page_size': page,
        'code_limit': limit,
        'code_slots': cd['n_code'],
        'special_slots': cd['n_special'],
        'cd_version': cd['version'],
        'cd_flags': cd['flags'],
        'blob_types': cd['blob_types'],
        'file_size': len(data),
        'mismatch_count': len(mismatches),
        'mismatches': mismatches[:16],
        'bad_chunks': {('0x%x' % (k * page * chunk_pages)): v for k, v in sorted(chunks.items())},
        'cdhash_ok': cd_actual == cd_expected,
        'cdhash': cd_actual.hex(),
        'cdhash_stored': cd_expected.hex(),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='+')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--chunk-pages', type=int, default=4)
    ap.add_argument('--expect-clean', action='store_true',
                    help='exit 1 when a signed file does not hash to its CodeDirectory')
    args = ap.parse_args(argv)

    results = [verify(f, args.chunk_pages) for f in args.files]
    if args.json:
        print(json.dumps(results, indent=1))
    else:
        for r in results:
            if not r['signed']:
                print('%-22s UNSIGNED (%s)' % (r['path'], r['reason']))
                continue
            print('%-22s %s  hash=%s page=%d slots=%d limit=%d file=%d'
                  % (r['path'], 'CLEAN' if r['ok'] else 'MISMATCH', r['hash'],
                     r['page_size'], r['code_slots'], r['code_limit'], r['file_size']))
            print('   cdhash %s (stored %s) %s'
                  % (r['cdhash'][:32], r['cdhash_stored'][:32],
                     'ok' if r['cdhash_ok'] else 'MISMATCH'))
            for bad in r['mismatches']:
                print('   slot %-5d file 0x%06x+0x%04x actual %s expected %s'
                      % (bad['slot'], bad['offset'], bad['length'],
                         bad['actual'][:32], bad['expected'][:32]))
            if r['bad_chunks']:
                print('   bad 16 KiB chunk(s): %s'
                      % ', '.join('%s (slots %s)' % (k, v) for k, v in r['bad_chunks'].items()))
    dirty = [r for r in results if r['signed'] and not r['ok']]
    if args.expect_clean and dirty:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
