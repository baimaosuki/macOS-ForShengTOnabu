#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prelink a native XNU kernel with kexts taken from a macOS fileset kernel cache.

This is the fileset-aware counterpart to UniXNU/kits/NativePrelink/emit_prelink.py,
which only understands the pre-macOS-12 layout (kexts located through
_PrelinkExecutableLoadAddr inside a parent __PRELINK_TEXT and pointers listed by
_PrelinkLinkKASLROffsets).

Stage 1 (this tool, "layout"):
  * resolve the dependency closure of the requested roots from __PRELINK_INFO;
  * copy every selected component's segments into freshly allocated coalesced
    segments appended to the native kernel image;
  * rewrite the native placeholder segment/section commands to describe them;
  * write the sparse component header index (__macho_list) the kernel's
    kalloc_type_view_parse() walks when the sparse prelink ABI is present;
  * report the pointer slots that stage 2 must rebind.

Pointers are not yet relocated: stage 2 must walk the chain metadata and rewrite
every slot, so the emitted image is deliberately not bootable.
"""
import argparse
import hashlib
import json
import plistlib
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kc_chains


MH_MAGIC_64 = 0xfeedfacf
LC_SEGMENT_64 = 0x19
LC_FILESET_ENTRY = 0x80000035
LC_DYLD_CHAINED_FIXUPS = 0x80000034

COALESCED = {
    '__PRELINK_TEXT': ('__TEXT', 5),
    '__PLK_TEXT_EXEC': ('__TEXT_EXEC', 5),
    '__PLK_DATA_CONST': ('__DATA_CONST', 3),
    '__PRELINK_DATA': ('__DATA', 3),
}


def commands(data, offset):
    magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds = struct.unpack_from(
        '<IiiIII', data, offset)
    if magic != MH_MAGIC_64:
        raise ValueError('not a 64-bit Mach-O at %#x' % offset)
    result, cursor = [], offset + 32
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from('<II', data, cursor)
        result.append((cmd, cursor, cmdsize))
        cursor += cmdsize
    return dict(offset=offset, cputype=cputype, cpusubtype=cpusubtype, filetype=filetype,
                ncmds=ncmds, commands=result)


def segments(data, image):
    result = []
    for cmd, offset, cmdsize in image['commands']:
        if cmd != LC_SEGMENT_64:
            continue
        name, vmaddr, vmsize, fileoff, filesize, maxprot, initprot, nsects, flags = \
            struct.unpack_from('<16s4Q4I', data, offset + 8)
        sections = []
        cursor = offset + 72
        for _ in range(nsects):
            sectname, segname, addr, size, file_offset = struct.unpack_from(
                '<16s16sQQI', data, cursor)
            sections.append(dict(name=sectname.rstrip(b'\x00').decode(),
                                 segment=segname.rstrip(b'\x00').decode(),
                                 addr=addr, size=size, offset=file_offset,
                                 command_offset=cursor))
            cursor += 80
        result.append(dict(name=name.rstrip(b'\x00').decode(), vmaddr=vmaddr, vmsize=vmsize,
                           fileoff=fileoff, filesize=filesize, initprot=initprot,
                           maxprot=maxprot, command_offset=offset, sections=sections))
    return result


def fileset(data, top):
    result = []
    for cmd, offset, cmdsize in top['commands']:
        if cmd != LC_FILESET_ENTRY:
            continue
        vmaddr, fileoff = struct.unpack_from('<QQ', data, offset + 8)
        entry_id_offset = struct.unpack_from('<I', data, offset + 24)[0]
        end = data.index(b'\x00', offset + entry_id_offset)
        result.append(dict(entry_id=data[offset + entry_id_offset:end].decode(),
                           vmaddr=vmaddr, fileoff=fileoff))
    return result


def fixup_slots(data, image, segs):
    """Count chained pointer slots per segment (format 8 / arm64e chains)."""
    for cmd, offset, cmdsize in image['commands']:
        if cmd != LC_DYLD_CHAINED_FIXUPS:
            continue
        dataoff, datasize = struct.unpack_from('<II', data, offset + 8)
        blob = image['offset'] + dataoff
        if blob + 28 > len(data):
            return None
        (version, starts_offset, imports_offset, symbols_offset, imports_count,
         imports_format, symbols_format) = struct.unpack_from('<7I', data, blob)
        starts = blob + starts_offset
        segment_count = struct.unpack_from('<I', data, starts)[0]
        counts, total = {}, 0
        for index in range(min(segment_count, len(segs))):
            info_offset = struct.unpack_from('<H', data, starts + 4 + index * 2)[0]
            if not info_offset:
                continue
            info = starts + info_offset
            page_size, pointer_format, segment_offset, max_valid = struct.unpack_from(
                '<IHHQ', data, info)
            page_start = 0
            page_index = 0
            while page_start < max_valid:
                entry_offset = struct.unpack_from('<H', data, info + 22 + page_index * 2)[0]
                page_index += 1
                page_start = page_index * page_size
                if not entry_offset:
                    continue
                cursor = info + entry_offset
                while True:
                    raw, = struct.unpack_from('<Q', data, cursor)
                    if raw == 0:
                        break
                    total += 1
                    counts[segs[index]['name']] = counts.get(segs[index]['name'], 0) + 1
                    following = (raw >> 51) & (0xFFF if pointer_format >= 6 else 0x7FF)
                    if not following:
                        break
                    cursor += following * 4
        return dict(version=version, imports_count=imports_count,
                    imports_format=imports_format, slots=total, per_segment=counts)
    return None


def align(value, page=4096):
    return (value + page - 1) & ~(page - 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', required=True, type=Path)
    parser.add_argument('--kernelcache', required=True, type=Path)
    parser.add_argument('--root', action='append', default=[])
    parser.add_argument('--all', action='store_true',
                        help='prelink every component declared by __PRELINK_INFO')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--base', type=lambda v: int(v, 0), default=None,
                        help='virtual address of the first emitted coalesced segment')
    args = parser.parse_args()

    native_data = bytearray(args.kernel.read_bytes())
    native = commands(native_data, 0)
    native_segments = segments(native_data, native)
    kc = args.kernelcache.read_bytes()
    top = commands(kc, 0)
    if top['filetype'] != 12:
        raise ValueError('kernel cache is not a file set (filetype %d)' % top['filetype'])

    top_segments = segments(kc, top)
    info = next(s for s in top_segments if s['name'] == '__PRELINK_INFO')
    blob = kc[info['fileoff']:info['fileoff'] + info['filesize']]
    closing = blob.find(b'</plist>')
    if closing != -1:
        blob = blob[:closing + len(b'</plist>')]      # trim alignment padding
    metadata = plistlib.loads(blob)
    declared = {entry['CFBundleIdentifier']: entry
                for entry in metadata['_PrelinkInfoDictionary']}
    located = {entry['entry_id']: entry for entry in fileset(kc, top)}

    roots = list(args.root)
    if args.all:
        roots = sorted(identifier for identifier, entry in declared.items()
                       if entry.get('OSKernelResource') is not True)
    if not roots:
        raise SystemExit('no roots selected')
    selected, interfaces, pending = [], set(), list(roots)
    while pending:
        identifier = pending.pop()
        if identifier in selected or identifier in interfaces:
            continue
        if identifier not in declared:
            raise ValueError('unknown root %s' % identifier)
        entry = declared[identifier]
        if entry.get('OSKernelResource') is True:
            interfaces.add(identifier)
            continue
        selected.append(identifier)
        pending.extend(entry.get('OSBundleLibraries') or {})

    modules, codeless = {}, []
    for identifier in selected:
        found = located.get(identifier)
        if found is None:
            codeless.append(identifier)   # properties-only kext: no image in the collection
            continue
        image = commands(kc, found['fileoff'])
        segs = segments(kc, image)
        modules[identifier] = dict(image=image, segments=segs, fileoff=found['fileoff'],
                                   vmaddr=found['vmaddr'],
                                   fixups=fixup_slots(kc, image, segs))

    total_text = sum(s['filesize'] for m in modules.values()
                     for s in m['segments'] if s['name'] in ('__TEXT', '__TEXT_EXEC'))
    native_end = max(s['vmaddr'] + s['vmsize'] for s in native_segments if s['vmsize'])
    base = args.base if args.base is not None else align(native_end, 0x100000)

    output = bytearray(native_data)
    placements, emitted, ranges = {}, [], []

    def append(name, va, blob, protection, section_name):
        nonlocal output
        size = align(len(blob))
        fileoff = align(len(output))
        output.extend(bytes(fileoff - len(output)))
        output.extend(blob)
        output.extend(bytes(size - len(blob)))
        placeholder = next((s for s in native_segments if s['name'] == name), None)
        if placeholder is None or placeholder['vmsize']:
            raise ValueError('native image lacks an empty %s placeholder' % name)
        struct.pack_into('<4Q2I', output, placeholder['command_offset'] + 24,
                         va, size, fileoff, size, protection, protection)
        section = placeholder['sections'][0]
        struct.pack_into('<16s16sQQI', output, section['command_offset'],
                         section_name.encode(), name.encode(), va, size, fileoff)
        emitted.append(dict(segment=name, section=section_name, va=va, size=size,
                            fileoff=fileoff, payload=len(blob)))
        return dict(va=va, size=size)

    cursor = base
    for coalesced_name, (component_name, protection) in COALESCED.items():
        blob = bytearray()
        for identifier in modules:
            for segment in modules[identifier]['segments']:
                if segment['name'] != component_name or not segment['filesize']:
                    continue
                placements.setdefault(identifier, {})[segment['name']] = len(blob)
                ranges.append(dict(component=identifier, segment=segment['name'],
                                   old_va=segment['vmaddr'], size=segment['filesize'],
                                   offset_in_blob=len(blob), coalesced=coalesced_name))
                blob.extend(kc[segment['fileoff']:segment['fileoff'] + segment['filesize']])
                blob.extend(bytes(align(len(blob)) - len(blob)))
        if not blob:
            continue
        placed = append(coalesced_name, cursor, blob, protection,
                        '__macho_list' if coalesced_name == '__PRELINK_TEXT' else '__const')
        cursor = placed['va'] + placed['size']

    # Resolve each copied segment's destination address/file offset.
    for record in ranges:
        placed = next(e for e in emitted if e['segment'] == record['coalesced'])
        record['new_va'] = placed['va'] + record['offset_in_blob']
        record['new_off'] = placed['fileoff'] + record['offset_in_blob']

    chain = kc_chains.chain_header(kc, top['commands'])
    base = kc_chains.image_base(top_segments)
    rewrites, external, chain_slots = [], {}, 0
    if chain is not None:
        for slot in kc_chains.walk_slots(kc, chain, top_segments):
            chain_slots += 1
            host = next((r for r in ranges
                         if r['old_va'] <= slot['va'] < r['old_va'] + r['size'] - 7), None)
            if host is None:
                continue
            absolute = base + slot['target']
            target = next((r for r in ranges
                           if r['old_va'] <= absolute < r['old_va'] + r['size']), None)
            if target is not None:
                value = target['new_va'] + (absolute - target['old_va'])
                kind = 'component'
            else:
                value = absolute
                kind = 'external'
                owner = next((s['name'] for s in top_segments
                              if s['vmaddr'] <= absolute < s['vmaddr'] + s['vmsize']), 'unmapped')
                external[owner] = external.get(owner, 0) + 1
            offset = host['new_off'] + (slot['va'] - host['old_va'])
            rewrites.append((offset, value, kind))

    for offset, value, _ in rewrites:
        struct.pack_into('<Q', output, offset, value)

    header_index, headers = [], []
    for identifier in modules:
        segment_offsets = placements.get(identifier, {})
        text = modules[identifier]['segments']
        first = next((s for s in text if s['name'] in segment_offsets), None)
        if first is None:
            raise ValueError('component %s has no placed segment' % identifier)
        coalesced_name = next(name for name, (component, _) in COALESCED.items()
                              if component == first['name'])
        prelink = next(e for e in emitted if e['segment'] == coalesced_name)
        headers.append(prelink['va'] + segment_offsets[first['name']])
    if headers:
        index = struct.pack('<%dQ' % len(headers), *headers)
        for entry in emitted:
            if entry['segment'] != '__PRELINK_TEXT':
                continue
            index_va = (entry['va'] + entry['size'] - len(index)) & ~7
            at = entry['fileoff'] + (index_va - entry['va'])
            struct.pack_into('<%dQ' % len(headers), output, at, *headers)
            header_index = dict(va=index_va, count=len(headers))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(bytes(output))
    report = dict(
        native=str(args.kernel), native_sha256=hashlib.sha256(native_data).hexdigest(),
        kernelcache=str(args.kernelcache), kernelcache_sha256=hashlib.sha256(kc).hexdigest(),
        roots=roots, components=len(selected), kernel_interfaces=sorted(interfaces),
        component_names=sorted(selected), codeless=sorted(codeless), component_text_bytes=total_text,
        emitted_segments=emitted, header_index=header_index,
        base=hex(base), output=str(args.output),
        output_sha256=hashlib.sha256(output).hexdigest(), output_bytes=len(output),
        pointer_slots_seen=chain_slots,
        kernelcache_base=hex(base), chain_format=chain['version'] if chain else None,
        copied_segments=len(ranges),
        pointer_rewrites=len(rewrites),
        pointer_rewrites_component=sum(1 for _, _, k in rewrites if k == 'component'),
        pointer_rewrites_external=sum(1 for _, _, k in rewrites if k == 'external'),
        external_targets=dict(sorted(external.items(), key=lambda item: -item[1])[:8]),
        stage='layout+rebind', pointers_rebound=True, loadable=False)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in (
        'components', 'component_text_bytes', 'copied_segments', 'header_index',
        'pointer_rewrites', 'pointer_rewrites_external', 'output_bytes', 'loadable')}, indent=2))


if __name__ == '__main__':
    main()

