#!/usr/bin/env python3
"""Fetch a single entry out of a remote (HTTP) zip archive with range requests.

The macOS restore IPSW is ~20 GB and the ramdisk inside it is a plain stored
zip entry, so pulling the whole file to read one member is wasteful.  This
reader walks the end-of-central-directory record, the ZIP64 records when the
archive needs them, and then fetches only the local header plus the member's
bytes.  Only stored (method 0) and deflate members are accepted; anything else
is reported instead of being guessed at.
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
import urllib.request
import zlib

EOCD = 0x06054B50
ZIP64_LOCATOR = 0x07064B50
ZIP64_EOCD = 0x06064B50
CENTRAL = 0x02014B50
LOCAL = 0x04034B50


def fetch(url: str, start: int, length: int) -> bytes:
    request = urllib.request.Request(url, headers={'Range': f'bytes={start}-{start + length - 1}'})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
    if len(data) != length:
        raise SystemExit(f'short read at {start}: wanted {length}, got {len(data)}')
    return data


def total_size(url: str) -> int:
    request = urllib.request.Request(url, headers={'Range': 'bytes=0-0'})
    with urllib.request.urlopen(request, timeout=120) as response:
        content_range = response.headers.get('Content-Range', '')
    if '/' not in content_range:
        raise SystemExit('server did not answer with a Content-Range header')
    return int(content_range.rsplit('/', 1)[1])


def find_eocd(url: str, size: int) -> int:
    tail = min(size, 1 << 17)
    block = fetch(url, size - tail, tail)
    index = block.rfind(struct.pack('<I', EOCD))
    while index >= 0:
        if index + 22 <= len(block):
            comment, = struct.unpack_from('<H', block, index + 20)
            if index + 22 + comment <= len(block):
                return size - tail + index
        index = block.rfind(struct.pack('<I', EOCD), 0, index)
    raise SystemExit('no end-of-central-directory record found')


def central_directory(url: str, size: int) -> tuple[list[dict], int]:
    eocd_at = find_eocd(url, size)
    head = fetch(url, eocd_at, 22)
    _, disk, cd_disk, _, count, cd_size, cd_offset = struct.unpack('<IHHHHII', head)
    if count == 0xFFFF or cd_offset == 0xFFFFFFFF or cd_size == 0xFFFFFFFF:
        locator = fetch(url, eocd_at - 20, 20)
        signature, _, zip64_at, _ = struct.unpack('<IIQI', locator)
        if signature != ZIP64_LOCATOR:
            raise SystemExit('zip64 locator missing where the EOCD said it would be')
        record = fetch(url, zip64_at, 56)
        signature, _, _, _, _, _, _, count, cd_size, cd_offset = struct.unpack('<IQHHIIQQQQ', record)
        if signature != ZIP64_EOCD:
            raise SystemExit('bad zip64 end-of-central-directory record')
    blob = fetch(url, cd_offset, cd_size)
    entries = []
    pos = 0
    while pos + 46 <= len(blob):
        fields = struct.unpack_from('<IHHHHHHIIIHHHHHII', blob, pos)
        if fields[0] != CENTRAL:
            break
        (_, _, _, _, method, _, _, _, compressed, uncompressed, name_len, extra_len,
         comment_len, _, _, external, offset) = fields
        name = blob[pos + 46:pos + 46 + name_len].decode('utf-8', 'replace')
        extra = blob[pos + 46 + name_len:pos + 46 + name_len + extra_len]
        cursor = 0
        while cursor + 4 <= len(extra):
            tag, length = struct.unpack_from('<HH', extra, cursor)
            cursor += 4
            if tag == 1:
                fields64 = extra[cursor:cursor + length]
                at = 0
                if uncompressed == 0xFFFFFFFF and at + 8 <= len(fields64):
                    uncompressed, = struct.unpack_from('<Q', fields64, at)
                    at += 8
                if compressed == 0xFFFFFFFF and at + 8 <= len(fields64):
                    compressed, = struct.unpack_from('<Q', fields64, at)
                    at += 8
                if offset == 0xFFFFFFFF and at + 8 <= len(fields64):
                    offset, = struct.unpack_from('<Q', fields64, at)
            cursor += length
        entries.append(dict(name=name, method=method, compressed=compressed,
                            uncompressed=uncompressed, offset=offset))
        pos += 46 + name_len + extra_len + comment_len
    return entries, cd_offset + cd_size


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--url', required=True)
    ap.add_argument('--match', required=True, help='substring of the member name to fetch')
    ap.add_argument('--output', required=True)
    args = ap.parse_args()

    size = total_size(args.url)
    entries, cd_end = central_directory(args.url, size)
    matches = [e for e in entries if args.match in e['name']]
    for entry in matches:
        print('candidate %-40s method=%d compressed=%d uncompressed=%d'
              % (entry['name'], entry['method'], entry['compressed'], entry['uncompressed']))
    if len(matches) != 1:
        raise SystemExit(f'expected exactly one entry matching {args.match!r}, got {len(matches)}')
    entry = matches[0]
    if entry['method'] not in (0, 8):
        raise SystemExit(f"unsupported compression method {entry['method']} for {entry['name']}")

    local = fetch(args.url, entry['offset'], 30)
    signature, _, _, _, _, _, _, _, _, name_len, extra_len = struct.unpack('<IHHHHHIIIHH', local)
    if signature != LOCAL:
        raise SystemExit('bad local file header signature')
    start = entry['offset'] + 30 + name_len + extra_len
    payload = fetch(args.url, start, entry['compressed'])
    if entry['method'] == 8:
        payload = zlib.decompress(payload, -zlib.MAX_WBITS)
    if len(payload) != entry['uncompressed']:
        raise SystemExit(f"size mismatch after extraction: {len(payload)} != {entry['uncompressed']}")

    with open(args.output, 'wb') as stream:
        stream.write(payload)
    print('%s: %d bytes sha256=%s' % (args.output, len(payload), hashlib.sha256(payload).hexdigest()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
