#!/usr/bin/env python3
"""Audit original 25F71 cache pages and emit a bounded CPU-compositor policy.

Never modifies the cache, root image or device. The pager integration is an
explicit, default-off shared-code-page development policy.
"""
import argparse
import hashlib
import json
from pathlib import Path
import uuid

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / 'platform/nabu/macos26/software-compositor/contract.json'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def cbytes(data):
    return '{' + ','.join('0x%02x' % b for b in data) + '}'


def prepare(cache, output):
    contract = json.loads(CONTRACT.read_text())
    for name, key in (('dyld_shared_cache_arm64e', 'cache_uuid'),
                      ('dyld_shared_cache_arm64e.01', 'text_cache_uuid')):
        with (cache / name).open('rb') as stream:
            header = stream.read(104)
        if not header.startswith(b'dyld_v1') or header[88:104] != uuid.UUID(contract[key]).bytes:
            raise ValueError('requires original paired 25F71 cache UUID: ' + name)
    records = []
    for site in contract['sites']:
        with (cache / site['file']).open('rb') as stream:
            stream.seek(site['file_offset'])
            page = bytearray(stream.read(16384))
        if len(page) != 16384 or digest(page) != site['page_sha256']:
            raise ValueError('original 25F71 code page hash differs: ' + site['name'])
        offset = site['edit_offset']
        before, after = bytes.fromhex(site['before']), bytes.fromhex(site['after'])
        if len(before) != len(after) or not 0 < len(before) <= 16 or offset + len(before) > len(page):
            raise ValueError('unbounded compositor edit')
        if page[offset:offset + len(before)] != before:
            raise ValueError('original prologue preimage differs')
        if 'pacibsp' in site and page[offset - 4:offset].hex() != site['pacibsp']:
            raise ValueError('PAC prologue preimage differs')
        page[offset:offset + len(after)] = after
        if digest(page) != site['patched_page_sha256']:
            raise ValueError('patched page hash differs')
        records.append(site)
    output.mkdir(parents=True, exist_ok=True)
    lines = ['// Generated from exact original 25F71 cache; do not edit.', '#pragma once',
             '#include <vm/nabu_cpu_compositor_policy.h>', '#define NABU_CPU_PAGE_BYTES 16384u',
             'static const uint8_t nabu_cpu_cache_uuid[16] = ' + cbytes(uuid.UUID(contract['cache_uuid']).bytes) + ';',
             'static const uint8_t nabu_cpu_text_cache_uuid[16] = ' + cbytes(uuid.UUID(contract['text_cache_uuid']).bytes) + ';',
             'static const struct nabu_cpu_page nabu_cpu_pages[%d] = {' % len(records)]
    for site in records:
        before, after = bytes.fromhex(site['before']), bytes.fromhex(site['after'])
        lines.append('  {"%s", 0x%xULL, %s, %s, 1, {{%du, %du, %s, %s}}},' % (
            site['file'], site['file_offset'], cbytes(bytes.fromhex(site['page_sha256'])),
            cbytes(bytes.fromhex(site['patched_page_sha256'])), site['edit_offset'], len(before),
            cbytes(before), cbytes(after)))
    lines.append('};')
    header = output / 'nabu_cpu_compositor_pages.h'
    header.write_text('\n'.join(lines) + '\n')
    evidence = dict(contract, contract_sha256=digest(CONTRACT.read_bytes()),
                    generated_header_sha256=digest(header.read_bytes()), original_cache_unchanged=True)
    (output / 'source.json').write_text(json.dumps(evidence, indent=2) + '\n')
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.cache, args.output), indent=2))
