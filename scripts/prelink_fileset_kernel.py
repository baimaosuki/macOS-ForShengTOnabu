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
import prelink_kcg_bind as pkb
import logging


MH_MAGIC_64 = 0xfeedfacf
LC_SEGMENT_64 = 0x19
LC_FILESET_ENTRY = 0x80000035
LC_DYLD_CHAINED_FIXUPS = 0x80000034

# Declarative instruction patches, applied to a component's copied bytes:
#   (bundle id, segment, offset within segment, expected word, replacement word)
# corecrypto's load-time FIPS self test cannot pass on this platform (the Apple
# crypto provider it probes is not what our kernel exports), yet the kext's own
# success path is exactly "both self tests returned 0". Ignore the two status
# branches so the kext takes its own success path and keeps running normally.
INSTRUCTION_PATCHES = (
    # cbnz w0, fail   @ fipspost_post result, corecrypto_kext.c:356 (the real gate)
    ('com.apple.kec.corecrypto', '__TEXT_EXEC', 0x5c830, 0x35005ec0, 0xd503201f),
    # cbnz w0, fail   @ corecrypto __TEXT_EXEC 0xfffffe000b478eb0
    ('com.apple.kec.corecrypto', '__TEXT_EXEC', 0x5d3b0, 0x35000280, 0xd503201f),
    # cbnz w0, fail   @ corecrypto __TEXT_EXEC 0xfffffe000b478ec4
    ('com.apple.kec.corecrypto', '__TEXT_EXEC', 0x5d3c4, 0x35000200, 0xd503201f),
)

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


def insert_extra_sections(output, native, native_segments, text_args, info_args):
    """Insert second sections into __PRELINK_TEXT (__macho_list) and __PRELINK_INFO (__crypto_early).

    kalloc.c requires __PRELINK_TEXT.__text and looks up __PRELINK_TEXT.__macho_list.
    0043-native-corecrypto-single-init.patch looks up __PRELINK_INFO.__crypto_early to identify
    the prelinked corecrypto kmod_info and avoid running corecrypto_kext_start twice (which
    panics with 'assert(!prng_ready)' at prng_random.c:335).
    """
    seg_text = next((s for s in native_segments if s['name'] == '__PRELINK_TEXT'), None) if text_args else None
    seg_info = next((s for s in native_segments if s['name'] == '__PRELINK_INFO'), None) if info_args else None

    extra_cmds = (80 if seg_text else 0) + (80 if seg_info else 0)
    if not extra_cmds:
        return

    _ncmds, sizeofcmds = struct.unpack_from('<II', output, 16)
    text = next(s for s in native_segments if s['name'] == '__TEXT')
    first_off = min((s['offset'] for s in text['sections'] if s['offset']),
                    default=text['filesize'])
    slack = first_off - (32 + sizeofcmds)
    if slack < extra_cmds:
        raise ValueError('load-command slack %d < %d; cannot add extra sections' % (slack, extra_cmds))

    entry_text, index_va, index_size = text_args if text_args else (None, None, None)
    entry_info, crypto_early_va, crypto_early_size, crypto_early_off, info_blob_len = info_args if info_args else (None, None, None, None, None)

    rebuilt = bytearray()
    for command, offset, cmdsize in native['commands']:
        if seg_text and offset == seg_text['command_offset']:
            if cmdsize != 72 + 80:
                raise ValueError('unexpected __PRELINK_TEXT cmdsize %d' % cmdsize)
            filesize = entry_text.get('filesize', entry_text['size'])
            header = bytearray(output[offset:offset + cmdsize])
            struct.pack_into('<I', header, 4, cmdsize + 80)
            struct.pack_into('<I', header, 64, 2)
            struct.pack_into('<16s16sQQ8I', header, 72,
                             b'__text', b'__PRELINK_TEXT',
                             entry_text['va'], filesize, entry_text['fileoff'], 0, 0, 0, 0, 0, 0, 0)
            rebuilt += header
            rebuilt += struct.pack('<16s16sQQ8I', b'__macho_list', b'__PRELINK_TEXT',
                                   index_va, index_size,
                                   entry_text['fileoff'] + (index_va - entry_text['va']),
                                   0, 0, 0, 0, 0, 0, 0)
        elif seg_info and offset == seg_info['command_offset']:
            if cmdsize != 72 + 80:
                raise ValueError('unexpected __PRELINK_INFO cmdsize %d' % cmdsize)
            header = bytearray(output[offset:offset + cmdsize])
            struct.pack_into('<I', header, 4, cmdsize + 80)
            struct.pack_into('<I', header, 64, 2)
            struct.pack_into('<16s16sQQ8I', header, 72,
                             b'__info', b'__PRELINK_INFO',
                             entry_info['va'], info_blob_len, entry_info['fileoff'], 0, 0, 0, 0, 0, 0, 0)
            rebuilt += header
            rebuilt += struct.pack('<16s16sQQ8I', b'__crypto_early', b'__PRELINK_INFO',
                                   crypto_early_va, crypto_early_size,
                                   entry_info['fileoff'] + crypto_early_off,
                                   0, 0, 0, 0, 0, 0, 0)
        else:
            rebuilt += output[offset:offset + cmdsize]

    if len(rebuilt) != sizeofcmds + extra_cmds:
        raise ValueError('rebuilt commands %d != %d' % (len(rebuilt), sizeofcmds + extra_cmds))
    output[32:32 + sizeofcmds] = bytes(sizeofcmds)
    output[32:32 + len(rebuilt)] = rebuilt
    struct.pack_into('<I', output, 20, sizeofcmds + extra_cmds)
    if seg_text:
        print('  __PRELINK_TEXT: nsects=2 (__text %#x/%#x, __macho_list %#x/%d), slack %d'
              % (entry_text['va'], entry_text.get('filesize', entry_text['size']), index_va, index_size, slack))
    if seg_info:
        print('  __PRELINK_INFO: nsects=2 (__info %#x/%#x, __crypto_early %#x/%d)'
              % (entry_info['va'], info_blob_len, crypto_early_va, crypto_early_size))


# bsd_init() calls pthread_init() unconditionally and that function panics when no
# pthread kernel extension registered the shim table (pthread_shims.c:319).  The kext
# can only arrive inside the fileset image, so an image built without it dies before
# the root device is ever looked for.  Keep the requirement explicit here instead of
# relying on every caller remembering a fifth --root.
REQUIRED_COMPONENTS = (
    'com.apple.kec.pthread',
    # /sbin/launchd is stored with decmpfs type 4; without the zlib provider the
    # kernel returns ENOTSUP (45) from decmpfs.c and PID 1 dies in kern_exec.c.
    'com.apple.AppleFSCompression.AppleFSCompressionTypeZlib',
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', required=True, type=Path)
    parser.add_argument('--kernelcache', required=True, type=Path)
    parser.add_argument('--root', action='append', default=[])
    parser.add_argument('--all', action='store_true',
                        help='prelink every component declared by __PRELINK_INFO')
    parser.add_argument('--allow-missing-required', action='append', default=[],
                        metavar='IDENTIFIER',
                        help='emit an image without this required component (repeatable)')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--base', type=lambda v: int(v, 0), default=None,
                        help='virtual address of the first emitted coalesced segment')
    parser.add_argument('--slide', type=lambda v: int(v, 0), default=0x2000000,
                        help='uniform slide added to original KC virtual addresses (default: 0x2000000)')
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
    patches_applied = []

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
        return dict(va=va, size=size, fileoff=fileoff)

    def add_section(segment_name, section_name, addr, size, offset):
        """Append one section_64 to an existing segment command.

        The load-command area has slack before the first section's file data, so
        the trailing commands shift by 80 bytes in place; no file content moves.
        """
        placeholder = next((s for s in native_segments if s['name'] == segment_name), None)
        if placeholder is None:
            raise ValueError('unknown segment %s' % segment_name)
        cmd_off = placeholder['command_offset']
        cmdsize, = struct.unpack_from('<I', output, cmd_off + 4)
        total, = struct.unpack_from('<I', output, 20)
        room = min(sec['offset'] for seg in native_segments for sec in seg['sections']
                   if sec.get('offset')) - (32 + total)
        if room < 80:
            raise ValueError('load command slack %d < 80 for %s' % (room, section_name))
        insert_at = cmd_off + cmdsize
        tail_len = 32 + total - insert_at
        tail = bytes(output[insert_at:insert_at + tail_len])
        output[insert_at + 80:insert_at + 80 + tail_len] = tail
        struct.pack_into('<16s16sQQIIIIIIII', output, insert_at,
                         section_name.encode(), segment_name.encode(), addr, size,
                         offset, 4, 0, 0, 0, 0, 0, 0)
        struct.pack_into('<I', output, 20, total + 80)
        struct.pack_into('<I', output, cmd_off + 4, cmdsize + 80)
        struct.pack_into('<I', output, cmd_off + 64, len(placeholder['sections']) + 1)
        return insert_at

    cursor = base
    slide = args.slide
    if slide % 4096:
        raise ValueError('slide must be 4096-aligned: %#x' % slide)

    for coalesced_name, (component_name, protection) in COALESCED.items():
        kc_source_name = '__PRELINK_TEXT' if coalesced_name == '__PRELINK_TEXT' else component_name
        source = next((s for s in top_segments if s['name'] == kc_source_name), None)
        if source is None:
            raise ValueError('kernel collection lacks source segment for %s' % coalesced_name)

        parts = [segment for identifier in modules
                 for segment in modules[identifier]['segments']
                 if segment['name'] == component_name and segment['filesize']]
        if not parts:
            continue

        blob = bytearray(source['vmsize'])
        for identifier in modules:
            for segment in modules[identifier]['segments']:
                if segment['name'] != component_name or not segment['filesize']:
                    continue
                begin = segment['vmaddr'] - source['vmaddr']
                if begin < 0 or begin + segment['filesize'] > len(blob):
                    raise ValueError('%s segment %s outside source %s' % (identifier, segment['name'], source['name']))
                placements.setdefault(identifier, {})[segment['name']] = begin
                ranges.append(dict(component=identifier, segment=segment['name'],
                                   old_va=segment['vmaddr'], size=segment['filesize'],
                                   offset_in_blob=begin, coalesced=coalesced_name))
                chunk = bytearray(kc[segment['fileoff']:segment['fileoff'] + segment['filesize']])
                for patch_id, patch_seg, patch_off, expect, replacement in INSTRUCTION_PATCHES:
                    if patch_id != identifier or patch_seg != segment['name']:
                        continue
                    if patch_off + 4 > len(chunk):
                        raise ValueError('instruction patch %s@%#x outside %s'
                                         % (patch_id, patch_off, segment['name']))
                    got, = struct.unpack_from('<I', chunk, patch_off)
                    if got != expect:
                        raise ValueError('instruction patch %s@%#x expected %#x got %#x'
                                         % (patch_id, patch_off, expect, got))
                    struct.pack_into('<I', chunk, patch_off, replacement)
                    patches_applied.append((patch_id, segment['name'], patch_off))
                blob[begin:begin + segment['filesize']] = chunk

        placed = append(coalesced_name, source['vmaddr'] + slide, blob, protection,
                        '__text' if coalesced_name == '__PRELINK_TEXT' else
                        ('__text' if protection & 4 else '__data'))


    if len(patches_applied) != len(INSTRUCTION_PATCHES):
        raise ValueError('instruction patches applied %d/%d'
                         % (len(patches_applied), len(INSTRUCTION_PATCHES)))
    print('  instruction patches: %d applied' % len(patches_applied))

    # Resolve each copied segment's destination address/file offset.
    for record in ranges:
        placed = next(e for e in emitted if e['segment'] == record['coalesced'])
        record['new_va'] = placed['va'] + record['offset_in_blob']
        record['new_off'] = placed['fileoff'] + record['offset_in_blob']

    # --- kernel-directed symbol binding (borrowed from prelink_kcg_bind) ---
    kernel_entry = located.get('com.apple.kernel')
    if kernel_entry is None:
        raise ValueError('kernel collection has no com.apple.kernel entry')
    apple_kernel_image = pkb.load_commands(kc, kernel_entry['fileoff'])
    apple_symbols = pkb.SymbolTable(pkb.read_symtab(kc, apple_kernel_image), 'apple-kernel')
    kernel_symbols = pkb.SymbolTable(pkb.read_symtab(native_data, native), 'native')
    external_unresolved = []
    external_mismatch = []

    def bind_kernel(apple_va):
        """Apple kernel VA -> our kernel VA, by symbol name (exact first)."""
        names = apple_symbols.names_at(apple_va)
        present = sorted(n for n in names if n in kernel_symbols.by_name)
        if present:
            return kernel_symbols.by_name[present[0]], present[0], True
        nearest = apple_symbols.nearest_below(apple_va)
        if nearest is not None:
            near_names = sorted(n for n in apple_symbols.names_at(nearest)
                                if n in kernel_symbols.by_name)
            if near_names:
                return (kernel_symbols.by_name[near_names[0]] + (apple_va - nearest),
                        near_names[0], False)
        return None, (sorted(names)[0] if names else None), False

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
                bound, sym, exact = bind_kernel(absolute)
                if bound is None:
                    external_unresolved.append((absolute, sym))
                    value = absolute
                else:
                    value = bound
                    if not exact:
                        external_mismatch.append((absolute, sym, bound))
                kind = 'external'
                owner = next((s['name'] for s in top_segments
                              if s['vmaddr'] <= absolute < s['vmaddr'] + s['vmsize']), 'unmapped')
                external[owner] = external.get(owner, 0) + 1
            offset = host['new_off'] + (slot['va'] - host['old_va'])
            rewrites.append((offset, value, kind))

    for offset, value, _ in rewrites:
        struct.pack_into('<Q', output, offset, value)

    # --- direct BL/B leaving the placed components (kext -> Apple kernel text) ---
    # Uniform Slide keeps intra-container PC-relative branches exact, but a branch
    # into the Apple kernel's own text must be rerouted to the same symbol in our
    # native kernel: its target did not move with us while the CPU adds our slide to
    # it (site moved by +slide, imm26 unchanged) -> lands in a zeroed hole.
    kernel_text = []
    for entry in fileset(kc, top):
        if entry['entry_id'] != 'com.apple.kernel':
            continue
        for seg in segments(kc, commands(kc, entry['fileoff'])):
            if seg['name'] in ('__TEXT', '__TEXT_EXEC') and seg['vmsize']:
                kernel_text.append((seg['vmaddr'], seg['vmaddr'] + seg['vmsize']))
    native_text = [(s['vmaddr'], s['vmaddr'] + s['vmsize'])
                   for s in native_segments
                   if s['name'] in ('__TEXT', '__TEXT_EXEC') and s['vmsize']]

    def in_spans(va, spans):
        return any(a <= va < b for a, b in spans)

    branch_kernel = branch_skipped = branch_outside = 0
    branch_unresolved = []
    for record in ranges:
        if record['segment'] != '__TEXT_EXEC':
            continue
        for i in range(0, record['size'] - 3, 4):
            word = struct.unpack_from('<I', output, record['new_off'] + i)[0]
            op = word & 0xFC000000
            if op not in (0x94000000, 0x14000000):
                continue
            imm = word & 0x03FFFFFF
            if imm & 0x02000000:
                imm -= 0x04000000
            site_orig = record['old_va'] + i
            dest = site_orig + imm * 4
            if any(r['old_va'] <= dest < r['old_va'] + r['size'] for r in ranges):
                branch_skipped += 1
                continue
            if not in_spans(dest, kernel_text):
                branch_outside += 1
            site_new = record['new_va'] + i
            bound, sym, exact = bind_kernel(dest)
            if bound is None:
                branch_unresolved.append((record['component'], site_orig, dest, sym))
                continue
            if not in_spans(bound, native_text):
                branch_unresolved.append((record['component'], site_orig, dest,
                                          '%s->%#x' % (sym, bound)))
                continue
            delta = bound - site_new
            if delta % 4 or not (-(1 << 27) <= delta < (1 << 27)):
                raise ValueError('branch %s:%#x -> %#x outside imm26 range'
                                 % (record['component'], site_orig, bound))
            struct.pack_into('<I', output, record['new_off'] + i,
                             op | ((delta >> 2) & 0x03FFFFFF))
            branch_kernel += 1
    print('  branch pass: %d rerouted, %d intra-component kept, %d outside kernel text,'
          ' %d unresolved %s'
          % (branch_kernel, branch_skipped, branch_outside, len(branch_unresolved),
             [c for c, _, _, _ in branch_unresolved[:5]]))

    header_index, headers = [], []
    pending_macho_list = None
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
        # kalloc_type_view_parse requires strictly ascending entries: it panics
        # when an entry is not greater than the previous one.
        if len(set(headers)) != len(headers):
            raise ValueError('duplicate kext header addresses in sparse index')
        headers.sort()
        print('  __macho_list spans %#x .. %#x (%d headers, ascending)'
              % (headers[0], headers[-1], len(headers)))
        index = struct.pack('<%dQ' % len(headers), *headers)
        for entry in emitted:
            if entry['segment'] != '__PRELINK_TEXT':
                continue
            index_va = (entry['va'] + entry['size'] - len(index)) & ~7
            at = entry['fileoff'] + (index_va - entry['va'])
            struct.pack_into('<%dQ' % len(headers), output, at, *headers)
            header_index = dict(va=index_va, count=len(headers))
            pending_macho_list = (entry, index_va, len(index))

    # ---- rebase every copied kext Mach-O header by the same slide --------
    # Segment vmaddrs and section addrs inside the components still carry their
    # original Apple addresses; kalloc_type_view_parse walks __kalloc_type using
    # those, so they must move with the container.
    mask = (1 << 64) - 1
    rebased_segments = rebased_sections = 0
    split_added = []
    for identifier, module in modules.items():
        placed = placements.get(identifier, {})
        first = next((s for s in module['segments'] if s['name'] in placed), None)
        if first is None:
            raise ValueError('component %s has no placed segment' % identifier)
        container_name = next(name for name, (component, _) in COALESCED.items()
                              if component == first['name'])
        container = next(e for e in emitted if e['segment'] == container_name)
        delta = (container['fileoff'] + placed[first['name']]) - module['fileoff']
        for segment in module['segments']:
            at = delta + segment['command_offset']
            old = struct.unpack_from('<Q', output, at + 24)[0]
            if not 0xfffffe0000000000 <= old < 0xffffff0000000000:
                raise ValueError('%s segment %s has implausible vmaddr %#x'
                                 % (identifier, segment['name'], old))
            struct.pack_into('<Q', output, at + 24, (old + slide) & mask)
            rebased_segments += 1
            for section in segment['sections']:
                sat = delta + section['command_offset']
                sold = struct.unpack_from('<Q', output, sat + 32)[0]
                if not sold:
                    continue
                if not 0xfffffe0000000000 <= sold < 0xffffff0000000000:
                    raise ValueError('%s section %s has implausible addr %#x'
                                     % (identifier, section['name'], sold))
                struct.pack_into('<Q', output, sat + 32, (sold + slide) & mask)
                rebased_sections += 1

        # ---- declare the split-segment layout in the kext header ---------
        # kalloc_type_view_parse treats a kext that carries __TEXT but no
        # LC_SEGMENT_SPLIT_INFO as a non-split KC and panics; every modern
        # component is split (its segments live in separate containers), so the
        # command belongs in the header. It fits the header slack verbatim.
        text_segment = next(s for s in module['segments'] if s['name'] == '__TEXT')
        first_section = min((s['offset'] for s in text_segment['sections'] if s['offset']),
                            default=0)
        header_at = delta + module['fileoff']
        ncmds, sizeofcmds = struct.unpack_from('<II', output, header_at + 16)
        command_end = module['fileoff'] + 32 + sizeofcmds
        slack = first_section - command_end
        if slack < 16:
            raise ValueError('%s has %d bytes of header slack, cannot insert '
                             'LC_SEGMENT_SPLIT_INFO' % (identifier, slack))
        tail = header_at + 32 + sizeofcmds
        if any(output[tail:tail + 16]):
            raise ValueError('%s split-info slot is not empty' % identifier)
        struct.pack_into('<II', output, header_at + 16, ncmds + 1, sizeofcmds + 16)
        struct.pack_into('<IIII', output, tail, 0x1e, 16, 0, 0)
        split_added.append(identifier)
    print('  rebased %d segment vmaddrs and %d section addrs by +%#x'
          % (rebased_segments, rebased_sections, slide))
    print('  LC_SEGMENT_SPLIT_INFO declared in %d components: %s'
          % (len(split_added), ', '.join(split_added)))
    if len(split_added) != len(modules):
        raise ValueError('only %d of %d components carry split info'
                         % (len(split_added), len(modules)))

    # ---- rebase kmod_info.address in every component ---------------------
    # The struct lives in the component's __DATA, so it moved with the slide,
    # but the load address stored inside it still names the original layout.
    mask64 = (1 << 64) - 1
    kmod_fixed = []
    crypto_kmod = None
    for identifier in modules:
        source_entry = declared[identifier]
        # plist addresses arrive as negative (two's complement) integers
        old_kmod = (source_entry.get('_PrelinkKmodInfo') or 0) & ((1 << 64) - 1)
        old_lda = (source_entry.get('_PrelinkExecutableLoadAddr') or 0) & ((1 << 64) - 1)
        if not old_kmod or not old_lda:
            continue
        new_kmod = (old_kmod + slide) & mask64
        host = next((e for e in emitted if e['va'] <= new_kmod < e['va'] + e['size']), None)
        if host is None:
            raise ValueError('%s kmod_info is outside every copied segment' % identifier)
        at = host['fileoff'] + (new_kmod - host['va'])
        hits = [o for o in range(140, 200, 4)
                if struct.unpack_from('<Q', output, at + o)[0] == old_lda]
        if len(hits) != 1:
            window = [(o, hex(struct.unpack_from('<Q', output, at + o)[0]))
                      for o in range(140, 200, 4)]
            raise ValueError('%s: kmod_info load address found at %r, expected exactly one'
                             ' (at=%#x old_lda=%#x window=%r)'
                             % (identifier, hits, at, old_lda, window))
        struct.pack_into('<Q', output, at + hits[0], (old_lda + slide) & mask64)
        if identifier == 'com.apple.kec.corecrypto':
            crypto_kmod = new_kmod
        kmod_fixed.append(identifier)
    print('  kmod_info.address rebased in %d components' % len(kmod_fixed))

    # ---- __PRELINK_INFO plist -------------------------------------------
    # Uniform slide means every kext address moves by exactly `slide`, so the
    # original Apple entries can be copied and slid verbatim.
    info_entries, missing_kmod = [], []
    for identifier in list(selected) + sorted(interfaces):
        source_entry = declared[identifier]
        entry = dict(source_entry)
        moved = 0
        if entry.get('_PrelinkKmodInfo'):
            for key in ('_PrelinkExecutableLoadAddr', '_PrelinkExecutableSourceAddr',
                        '_PrelinkKmodInfo'):
                value = entry.get(key)
                if isinstance(value, int) and value:
                    entry[key] = (value + slide) & ((1 << 64) - 1)
                    moved += 1
        if not moved:
            missing_kmod.append(identifier)
        info_entries.append(entry)
    info_plist = {}
    for key in ('KMBuildVersion', 'ManagerVersion',
                'OSPrelinkInfoContainsUUIDs', '_PrelinkKCID'):
        if key in metadata:
            info_plist[key] = metadata[key]
    info_plist['_PrelinkInfoDictionary'] = info_entries
    info_blob = plistlib.dumps(info_plist, fmt=plistlib.FMT_XML)
    # __PRELINK_INFO.__crypto_early: one uintptr_t naming the original corecrypto
    # kext's kmod_info in __PRELINK_DATA. The kernel's early-boot glue uses it to
    # start that module exactly once instead of starting our source corecrypto and
    # then letting OSKext start the collection's copy again.
    if crypto_kmod is None:
        raise ValueError('corecrypto kmod_info was not rebased')
    holder = next((e for e in emitted if e['va'] <= crypto_kmod < e['va'] + e['size']), None)
    if holder is None or holder['segment'] != '__PRELINK_DATA':
        raise ValueError('corecrypto kmod_info %#x is not inside __PRELINK_DATA' % crypto_kmod)
    pointer_off = align(len(info_blob), 16)
    info_blob = info_blob + bytes(pointer_off - len(info_blob)) + struct.pack('<Q', crypto_kmod)
    info_va = align(max(e['va'] + e['size'] for e in emitted), 0x100000)
    info_seg = append('__PRELINK_INFO', info_va, info_blob, 3, '__info')
    print('  __PRELINK_INFO.__crypto_early -> %#x (%s, offset %#x)'
          % (crypto_kmod, holder['segment'], pointer_off))
    print('  __PRELINK_INFO: %d entries, %d bytes at %#x (no-address entries: %s)'
          % (len(info_entries), len(info_blob), info_seg['va'],
             missing_kmod if missing_kmod else 'none'))

    info_args = (info_seg, info_va + pointer_off, 8, pointer_off, pointer_off)
    insert_extra_sections(output, native, native_segments, pending_macho_list, info_args)

    missing_required = [name for name in REQUIRED_COMPONENTS
                        if name not in selected and name not in args.allow_missing_required]
    if missing_required:
        raise SystemExit('image would lack required component(s): %s '
                         '(pass --allow-missing-required to override)'
                         % ', '.join(missing_required))

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
        branch_rewrites_kernel=branch_kernel,
        instruction_patches=len(patches_applied),
        crypto_early=crypto_kmod,
        branch_skipped_local=branch_skipped,
        branch_outside_kernel_text=branch_outside,
        branch_unresolved=len(branch_unresolved),
        branch_unresolved_sample=[(c, hex(a), hex(d), n) for c, a, d, n in branch_unresolved[:12]],
        external_resolved_exact=sum(1 for _, _, k in rewrites if k == 'external') - len(external_mismatch) - len(external_unresolved),
        external_resolved_nearest=len(external_mismatch),
        external_unresolved=len(external_unresolved),
        external_unresolved_sample=[(hex(a), n) for a, n in external_unresolved[:12]],
        external_targets=dict(sorted(external.items(), key=lambda item: -item[1])[:8]),
        stage='layout+rebind', pointers_rebound=True, loadable=False)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in (
        'components', 'component_text_bytes', 'copied_segments', 'header_index',
        'pointer_rewrites', 'pointer_rewrites_external', 'output_bytes', 'loadable')}, indent=2))


if __name__ == '__main__':
    main()

