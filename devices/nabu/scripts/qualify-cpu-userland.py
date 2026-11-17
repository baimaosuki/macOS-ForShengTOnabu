#!/usr/bin/env python3
"""Qualify the CPU renderer against the unchanged 26C5035r shared cache.

No cache bytes are rewritten. XNU's upstream signed-text facility applies the
exact original/replacement pairs after authenticating the original page.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import tomllib


def word(stream, offset):
    stream.seek(offset)
    return struct.unpack('<I', stream.read(4))[0]


def branch_target(address, instruction):
    assert instruction >> 26 == 0b000101, 'expected an unconditional B'
    immediate = instruction & 0x3ffffff
    if immediate & 0x2000000:
        immediate -= 0x4000000
    return address + immediate * 4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cache', type=Path, help='dyld_shared_cache_arm64e (main cache)')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    device = Path(__file__).resolve().parents[1]
    fixture = json.loads((device / 'compat/cpu-display/userland-26C5035r.json').read_text())
    configured = tomllib.loads((device / 'board.toml').read_text())['payload']['text_fixups']
    checked = []
    session = json.loads((device / 'compat/cpu-display/safe-aperture-26C5035r.json').read_text())
    magnify = json.loads((device / 'compat/cpu-display/magnify-26C5035r.json').read_text())
    disclosure = json.loads((device / 'compat/cpu-display/managed-tcc-26C5035r.json').read_text())
    hotpath = json.loads((device / 'compat/cpu-display/nopac-hotpath-26C5035r.json').read_text())
    entries = fixture['patches'] + session['patches'] + magnify['patches'] + disclosure['patches'] + hotpath['patches']
    for entry in entries:
        path = args.cache.with_name(entry['file'])
        offset = int(entry['offset'], 16)
        expected = bytes.fromhex(entry['original'])
        with path.open('rb') as stream:
            stream.seek(offset)
            assert stream.read(len(expected)) == expected, entry['name']
            stream.seek(offset & ~4095)
            original_page = stream.read(4096)
        assert hashlib.sha256(original_page).hexdigest() == entry['page_sha256']
        setting = '{file},{offset},{original},{replacement}'.format(**dict(entry, offset=entry['offset'][2:]))
        assert setting in configured, 'qualified CPU fixup absent from payload'
        patched = bytearray(original_page)
        for other in entries:
            other_offset = int(other['offset'], 16)
            if other['file'] != entry['file'] or other_offset & ~4095 != offset & ~4095:
                continue
            at = other_offset & 4095
            before = bytes.fromhex(other['original'])
            assert patched[at:at + len(before)] == before
            patched[at:at + len(before)] = bytes.fromhex(other['replacement'])
        actual=hashlib.sha256(patched).hexdigest()
        if entry.get('patched_page_sha256'):
            assert actual==entry['patched_page_sha256'],entry['name']
        checked.append(dict(entry, patched_page_sha256=actual))
    with args.cache.with_name('dyld_shared_cache_arm64e.03').open('rb') as stream:
        allocation = fixture['cpu_framebuffer_allocation']
        assert word(stream, 0x18e1e63cc - 0x188400000) == 0x1a981118
        assert int(allocation['pixel_format'], 16) == 0x42475241
        assert allocation['superseded_partial_branch_fix'] not in configured
        final = next(e for e in fixture['patches']
                     if e['va'] == allocation['final_format_site'])
        assert final['replacement'] == 'f803082a'  # mov w24, w8: final BGRA before reuse/allocation
        # Both exact NULL branches still restore the caller's stack and jump
        # to the real Server::render_update, which calls sw_renderer itself.
        for site, target in ((0x18df5203c, 0x18e186bec), (0x18df4297c, 0x18e186bec)):
            assert branch_target(site, word(stream, site - 0x188400000)) == target
        call = word(stream, 0x18e186c04 - 0x188400000)
        assert call >> 26 == 0b100101
        signed = (call & 0x3ffffff) - (0x4000000 if call & 0x2000000 else 0)
        assert 0x18e186c04 + signed * 4 == 0x18e1869b8
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(build='26C5035r', cpu_null_fallback_verified=True,
        native_sw_renderer_unchanged=True, disk_cache_modified=False,
        final_framebuffer_format_before_reuse_and_allocation=True,
        superseded_partial_format_branch_removed=True,
        destination_guard_not_patched=True,
        patches=checked, device_tested=False), indent=2) + '\n')
    print(f'26C5035r: exact CPU NULL fallback, native software renderer and {len(checked)} signed-text rules verified')


if __name__ == '__main__':
    main()
