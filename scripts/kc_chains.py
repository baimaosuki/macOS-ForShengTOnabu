#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage-2 helper: decode the top-level chained fixups of a boot kernel cache.

macOS boot kernel collections carry exactly one LC_DYLD_CHAINED_FIXUPS in the
top-level file-set image; the per-kext images have none.  The per-segment chain
starts index the top-level segments, and every slot is a
DYLD_CHAINED_PTR_64_KERNEL_CACHE word:

    target:30  cacheLevel:2  diversity:16  addrDiv:1  key:2  next:12  isAuth:1

`target` is an offset from the collection's preferred load address, so this
module turns a slot word into an absolute address and reports whether the target
lands inside a region a prelink is copying.
"""
import struct

LC_DYLD_CHAINED_FIXUPS = 0x80000034
LC_SEGMENT_64 = 0x19


def top_segments(data, commands):
    result = []
    for cmd, offset, cmdsize in commands:
        if cmd != LC_SEGMENT_64:
            continue
        name, vmaddr, vmsize, fileoff, filesize, maxprot, initprot, nsects, flags = \
            struct.unpack_from('<16s4Q4I', data, offset + 8)
        result.append(dict(name=name.rstrip(b'\x00').decode(), vmaddr=vmaddr, vmsize=vmsize,
                           fileoff=fileoff, filesize=filesize))
    return result


def chain_header(data, commands):
    found = [(offset, cmdsize) for cmd, offset, cmdsize in commands
             if cmd == LC_DYLD_CHAINED_FIXUPS]
    if not found:
        return None
    offset, _ = found[0]
    dataoff, datasize = struct.unpack_from('<II', data, offset + 8)
    blob = dataoff
    version, starts_offset, imports_offset, symbols_offset, imports_count, \
        imports_format, symbols_format = struct.unpack_from('<7I', data, blob)
    return dict(offset=blob, size=datasize, version=version,
                starts=blob + starts_offset, imports_count=imports_count,
                imports_format=imports_format)


def walk_slots(data, header, segments):
    """Yield (slot_file_offset, slot_va, target_offset, is_auth, cache_level)."""
    starts = header['starts']
    count, = struct.unpack_from('<I', data, starts)
    for index in range(min(count, len(segments))):
        relative, = struct.unpack_from('<I', data, starts + 4 + index * 4)
        if not relative:
            continue
        info = starts + relative
        span, page_size, fmt, segment_offset, max_valid, pages = struct.unpack_from(
            '<IHHQIH', data, info)
        if fmt != 8:
            raise ValueError('expected format-8 kernel-cache chains, got %d' % fmt)
        segment = segments[index]
        for page in range(pages):
            start, = struct.unpack_from('<H', data, info + 22 + 2 * page)
            if start == 0xFFFF:
                continue
            site = page * page_size + start
            while True:
                file_pos = segment['fileoff'] + site
                raw, = struct.unpack_from('<Q', data, file_pos)
                yield dict(file_offset=file_pos, va=segment['vmaddr'] + site,
                           raw=raw, target=raw & 0x3FFFFFFF,
                           is_auth=(raw >> 63) & 1, cache_level=(raw >> 30) & 0x3,
                           following=(raw >> 51) & 0xFFF)
                following = (raw >> 51) & 0xFFF
                if not following:
                    break
                site += following * 4


def image_base(segments):
    return min(s['vmaddr'] for s in segments if s['vmsize'])


def base_guess_report(data, commands, segments):
    header = chain_header(data, commands)
    if header is None:
        return dict(error='no LC_DYLD_CHAINED_FIXUPS in the top-level image')
    base = image_base(segments)
    inside = outside = 0
    samples = []
    for slot in walk_slots(data, header, segments):
        address = base + slot['target']
        hit = next((s for s in segments
                    if s['vmaddr'] <= address < s['vmaddr'] + s['vmsize']), None)
        if hit:
            inside += 1
            if len(samples) < 5:
                samples.append(dict(slot_va=hex(slot['va']), target=hex(slot['target']),
                                    resolved=hex(address), segment=hit['name'],
                                    auth=slot['is_auth']))
        else:
            outside += 1
    return dict(base=hex(base), inside_segments=inside, outside_segments=outside,
                samples=samples)

