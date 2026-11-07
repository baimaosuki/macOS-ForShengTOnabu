#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage 2 of the kext prelink pipeline: bind a macOS fileset KC's kexts into a
self-built XNU image using a *uniform slide*, so no runtime linker is needed.

Design (docs/nabu-kc-kext-load-contract.md, section 3):

  * Every segment of every selected component is moved by **one** constant
    ``slide``.  A uniform translation is an isometry, so
    ``new_target - new_site == old_target - old_site`` holds bit-exactly for
    every intra-closure reference.  Consequently no ``adrp``/``add``/``ldr``
    rewriting is needed inside kexts and no veneer is needed for
    intra-closure branches.
  * Kext Mach-O headers stay *sparse*: the NABU kernel
    (platform/nabu/macos26/patches/0008-nabu-sparse-prelink-index.patch) walks
    ``__PRELINK_TEXT,__macho_list`` and follows that pointer array, so kexts do
    not have to be packed contiguously at KEXT_ALIGN_BYTES.
  * Only two kinds of references change:
      a/b. absolute pointer slots (decoded from the top level chained fixups)
           get ``+ slide``;
      c.   references whose target lies in Apple's ``com.apple.kernel`` are
           redirected into *our* kernel: Apple VA -> symbol name -> our VA.
  * ``vm_kernel_slide == 0`` on this board, so every slot is rewritten as a bare
    little-endian 64-bit absolute address; no chained fixup has to survive.

Stdlib only: the branch scanner decodes the few AArch64 PC-relative forms it
needs instead of depending on capstone.
"""
import argparse
import hashlib
import json
import plistlib
import struct
import sys
from pathlib import Path

MH_MAGIC_64 = 0xFEEDFACF
LC_SEGMENT_64 = 0x19
LC_SYMTAB = 0x2
LC_UUID = 0x1B
LC_FUNCTION_STARTS = 0x26
LC_SEGMENT_SPLIT_INFO = 0x1E
LC_FILESET_ENTRY = 0x80000035
LC_DYLD_CHAINED_FIXUPS = 0x80000034

CPU_TYPE_ARM64 = 0x0100000C

PAGE = 0x1000
MAX_PRELINK_TEXT = 0x03000000      # arm_vm_init.c:2078
VENEER_RESERVE = 0x1000

GROUPS = (
    ('__PRELINK_TEXT', '__TEXT', 5),
    ('__PLK_TEXT_EXEC', '__TEXT_EXEC', 5),
    ('__PLK_DATA_CONST', '__DATA_CONST', 3),
    ('__PRELINK_DATA', '__DATA', 3),
)
PLACEHOLDER_SEGMENTS = tuple(g[0] for g in GROUPS) + ('__PRELINK_INFO',)

DEFAULT_KERNEL = 'out/nabu/kernel/kernel.development.nabu'
DEFAULT_KC = 'out/macos26-assets/kc.mac13g.arm64.macho'
DEFAULT_FILESET = 'out/macos26-assets/kc.mac13g.fileset.json'

# The real entry_id in the 26.5 collection is com.apple.security.AppleImage4;
# board.toml lists com.apple.driver.AppleImage4, which does not exist.
DEFAULT_ROOTS = (
    'com.apple.security.AppleImage4',
    'com.apple.driver.AppleMobileFileIntegrity',
    'com.apple.filesystems.apfs',
    'com.apple.security.sandbox',
)


class Refused(Exception):
    """The image must not be written."""


def align_up(value, boundary=PAGE):
    return (value + boundary - 1) & ~(boundary - 1)


def align_down(value, boundary=PAGE):
    return value & ~(boundary - 1)


def cstr(raw):
    return raw.split(b'\0', 1)[0].decode('utf-8', 'replace')


# --------------------------------------------------------------------------- #
# Mach-O plumbing
# --------------------------------------------------------------------------- #
def load_commands(data, offset):
    magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds = struct.unpack_from(
        '<IiiIII', data, offset)
    if magic != MH_MAGIC_64:
        raise ValueError('not a 64-bit Mach-O at %#x (magic %#x)' % (offset, magic))
    result, cursor = [], offset + 32
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from('<II', data, cursor)
        if cmdsize < 8 or cursor + cmdsize > len(data):
            raise ValueError('corrupt load command at %#x' % cursor)
        result.append((cmd, cursor, cmdsize))
        cursor += cmdsize
    return dict(offset=offset, cputype=cputype, cpusubtype=cpusubtype, filetype=filetype,
                ncmds=ncmds, sizeofcmds=sizeofcmds, commands=result)


def read_segments(data, image):
    result = []
    for cmd, offset, cmdsize in image['commands']:
        if cmd != LC_SEGMENT_64:
            continue
        name, vmaddr, vmsize, fileoff, filesize, maxprot, initprot, nsects, flags = \
            struct.unpack_from('<16s4Q4I', data, offset + 8)
        sections, cursor = [], offset + 72
        for _ in range(nsects):
            sectname, segname, addr, size, file_offset = struct.unpack_from(
                '<16s16sQQI', data, cursor)
            sections.append(dict(name=cstr(sectname), segment=cstr(segname), addr=addr,
                                 size=size, offset=file_offset, command_offset=cursor))
            cursor += 80
        result.append(dict(name=cstr(name), vmaddr=vmaddr, vmsize=vmsize, fileoff=fileoff,
                           filesize=filesize, initprot=initprot, maxprot=maxprot,
                           command_offset=offset, cmdsize=cmdsize, flags=flags,
                           sections=sections))
    return result


def read_symtab(data, image):
    for cmd, offset, _ in image['commands']:
        if cmd != LC_SYMTAB:
            continue
        symoff, nsyms, stroff, strsize = struct.unpack_from('<4I', data, offset + 8)
        result = []
        for index in range(nsyms):
            strx, ntype, _sect, _desc, value = struct.unpack_from(
                '<IBBHQ', data, symoff + index * 16)
            if strx >= strsize:
                continue
            end = data.find(b'\0', stroff + strx)
            result.append((data[stroff + strx:end].decode('utf-8', 'replace'), ntype, value))
        return result
    return []


def read_function_starts(data, image, segments):
    """Function start VAs from LC_FUNCTION_STARTS (ULEB128 deltas from __TEXT)."""
    text = next((s for s in segments if s['name'] == '__TEXT'), None)
    if text is None:
        return []
    for cmd, offset, _ in image['commands']:
        if cmd != LC_FUNCTION_STARTS:
            continue
        dataoff, datasize = struct.unpack_from('<II', data, offset + 8)
        if not datasize:
            return []
        blob = data[dataoff:dataoff + datasize]
        starts, cursor, address = [], 0, text['vmaddr']
        while cursor < len(blob):
            value, shift = 0, 0
            while cursor < len(blob):
                byte = blob[cursor]
                cursor += 1
                value |= (byte & 0x7F) << shift
                shift += 7
                if not byte & 0x80:
                    break
            if value == 0:
                break
            address += value
            starts.append(address)
        return starts
    return []


def read_fileset(data, image):
    result = []
    for cmd, offset, _ in image['commands']:
        if cmd != LC_FILESET_ENTRY:
            continue
        vmaddr, fileoff = struct.unpack_from('<QQ', data, offset + 8)
        entry_offset = struct.unpack_from('<I', data, offset + 24)[0]
        result.append(dict(entry_id=cstr(data[offset + entry_offset:]),
                           vmaddr=vmaddr, fileoff=fileoff))
    return result


def read_plist_section(data, segments, segment_name, section_name):
    segment = next((s for s in segments if s['name'] == segment_name), None)
    if segment is None:
        return None
    section = next((s for s in segment['sections'] if s['name'] == section_name), None)
    if section is None:
        return None
    blob = data[section['offset']:section['offset'] + section['size']]
    closing = blob.find(b'</plist>')
    if closing != -1:
        blob = blob[:closing + len(b'</plist>')]
    return plistlib.loads(blob)


# --------------------------------------------------------------------------- #
# top-level chained fixups (per-kext images carry none)
# --------------------------------------------------------------------------- #
def chain_header(data, image):
    for cmd, offset, _ in image['commands']:
        if cmd != LC_DYLD_CHAINED_FIXUPS:
            continue
        dataoff, datasize = struct.unpack_from('<II', data, offset + 8)
        version, starts_offset, imports_offset, symbols_offset, imports_count, \
            imports_format, symbols_format = struct.unpack_from('<7I', data, dataoff)
        return dict(offset=dataoff, size=datasize, version=version,
                    starts=dataoff + starts_offset, imports_count=imports_count)
    return None


def walk_slots(data, header, segments):
    starts = header['starts']
    count, = struct.unpack_from('<I', data, starts)
    for index in range(min(count, len(segments))):
        relative, = struct.unpack_from('<I', data, starts + 4 + index * 4)
        if not relative:
            continue
        info = starts + relative
        _span, page_size, pointer_format, _segment_offset, _max_valid, pages = \
            struct.unpack_from('<IHHQIH', data, info)
        if pointer_format != 8:
            raise ValueError('expected DYLD_CHAINED_PTR_64_KERNEL_CACHE (8), got %d'
                             % pointer_format)
        segment = segments[index]
        for page in range(pages):
            start, = struct.unpack_from('<H', data, info + 22 + 2 * page)
            if start == 0xFFFF:
                continue
            site = page * page_size + start
            while True:
                position = segment['fileoff'] + site
                if position + 8 > len(data):
                    break
                raw, = struct.unpack_from('<Q', data, position)
                yield dict(file_offset=position,
                           va=segment['vmaddr'] + site,
                           raw=raw,
                           target=raw & 0x3FFFFFFF,
                           is_auth=(raw >> 63) & 1,
                           cache_level=(raw >> 30) & 0x3,
                           following=(raw >> 51) & 0xFFF)
                following = (raw >> 51) & 0xFFF
                if not following:
                    break
                site += following * 4


# --------------------------------------------------------------------------- #
# minimal AArch64 decoders (stdlib only)
# --------------------------------------------------------------------------- #
def sign_extend(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def rd_of(word):
    return word & 0x1F


def rn_of(word):
    return (word >> 5) & 0x1F


def decode_pc_relative(word, address):
    top = word >> 26
    if top == 0b100101:                                   # BL
        return dict(kind='bl', target=address + sign_extend(word & 0x3FFFFFF, 26) * 4)
    if top == 0b000101:                                   # B
        return dict(kind='b', target=address + sign_extend(word & 0x3FFFFFF, 26) * 4)
    if (word & 0xFF000010) == 0x54000000:                 # B.cond
        return dict(kind='b.cond',
                    target=address + sign_extend((word >> 5) & 0x7FFFF, 19) * 4)
    if (word & 0x7E000000) == 0x34000000:                 # CBZ / CBNZ
        return dict(kind='cbz', target=address + sign_extend((word >> 5) & 0x7FFFF, 19) * 4)
    if (word & 0x7E000000) == 0x36000000:                 # TBZ / TBNZ
        # imm14 (bits 18:5) is a raw byte offset, not word scaled: unlike
        # B.cond/CBZ the immediate is not multiplied by 4.
        return dict(kind='tbz', target=address + sign_extend((word >> 5) & 0x3FFF, 14))
    if (word & 0x1F000000) == 0x10000000:                 # ADR / ADRP
        immediate = sign_extend(((word >> 5) & 0x7FFFF) << 2 | ((word >> 29) & 3), 21)
        if word >> 31:
            return dict(kind='adrp', target=(address & ~0xFFF) + (immediate << 12),
                        page_base=address & ~0xFFF)
        return dict(kind='adr', target=address + immediate)
    if (word & 0x3B000000) == 0x18000000:                 # LDR (literal)
        return dict(kind='ldr', target=address + sign_extend((word >> 5) & 0x7FFFF, 19) * 4)
    return None


def encode_branch(kind, delta):
    if kind in ('b', 'bl'):
        if delta % 4 or not -(1 << 27) <= delta < (1 << 27):
            return None
        return (0x94000000 if kind == 'bl' else 0x14000000) | ((delta >> 2) & 0x3FFFFFF)
    if kind == 'b.cond':
        if delta % 4 or not -(1 << 20) <= delta < (1 << 20):
            return None
        return 0x54000000 | (((delta >> 2) & 0x7FFFF) << 5)
    return None


def encode_adrp(register, page_delta):
    if page_delta % PAGE or not -(1 << 32) <= page_delta < (1 << 32):
        return None
    immediate = page_delta >> 12
    return (0x90000000 | register | ((immediate & 3) << 29)
            | (((immediate >> 2) & 0x7FFFF) << 5))


def is_add_imm12(word):
    return (word & 0x7F800000) == 0x11000000


def encode_veneer(target, address):
    """adrp x16, hi ; add x16, x16, lo ; br x16 -- 12 bytes, keeps LR intact."""
    adrp = encode_adrp(16, (target & ~0xFFF) - (address & ~0xFFF))
    if adrp is None:
        return None
    add = 0x91000000 | ((target & 0xFFF) << 10) | (16 << 5) | 16
    return struct.pack('<III', adrp, add, 0xD61F0200)


# --------------------------------------------------------------------------- #
# symbol tables
# --------------------------------------------------------------------------- #
class SymbolTable:
    def __init__(self, entries, label):
        import bisect
        self._bisect = bisect
        self.label = label
        self.by_name = {}
        self.by_address = {}
        for name, ntype, value in entries:
            if ntype & 0xE0:            # N_STAB
                continue
            if ntype & 0x0E != 0x0E:    # N_SECT
                continue
            if not value:
                continue
            self.by_name.setdefault(name, value)
            self.by_address.setdefault(value, []).append(name)
        self.sorted_addresses = sorted(self.by_address)
        self.duplicate_addresses = sum(1 for v in self.by_address.values() if len(v) > 1)

    def __len__(self):
        return len(self.by_name)

    def names_at(self, address):
        return self.by_address.get(address, [])

    def nearest_below(self, address):
        index = self._bisect.bisect_right(self.sorted_addresses, address) - 1
        return self.sorted_addresses[index] if index >= 0 else None


# --------------------------------------------------------------------------- #
# dependency closure
# --------------------------------------------------------------------------- #
def dependency_closure(declared, located, roots):
    selected, interfaces, pending = [], {}, list(roots)
    while pending:
        identifier = pending.pop(0)
        if identifier in selected or identifier in interfaces:
            continue
        if identifier not in declared:
            raise ValueError('%s is not declared in __PRELINK_INFO' % identifier)
        entry = declared[identifier]
        if entry.get('OSKernelResource') is True:
            interfaces[identifier] = entry
            continue
        selected.append(identifier)
        pending.extend(sorted((entry.get('OSBundleLibraries') or {}).keys()))
    return selected, interfaces, [i for i in selected if i not in located]


# --------------------------------------------------------------------------- #
# pipeline
# --------------------------------------------------------------------------- #
def run(args):
    decoder_checks = decoder_self_test()
    kernel_path = Path(args.native) if args.native else Path(DEFAULT_KERNEL)
    kc_path = Path(args.kc) if args.kc else Path(DEFAULT_KC)
    fileset_path = Path(args.fileset) if args.fileset else Path(DEFAULT_FILESET)

    native_data = bytearray(kernel_path.read_bytes())
    kernel_image = load_commands(native_data, 0)
    kernel_segments = read_segments(native_data, kernel_image)
    kernel_symbols = SymbolTable(read_symtab(native_data, kernel_image), 'native')

    kc = kc_path.read_bytes()
    kc_image = load_commands(kc, 0)
    if kc_image['filetype'] != 12:
        raise ValueError('kernel collection is not MH_FILESET (filetype %d)'
                         % kc_image['filetype'])
    kc_segments = read_segments(kc, kc_image)
    kc_base = min(s['vmaddr'] for s in kc_segments if s['vmsize'])
    located = {e['entry_id']: e for e in read_fileset(kc, kc_image)}

    metadata = read_plist_section(kc, kc_segments, '__PRELINK_INFO', '__info')
    if metadata is None:
        raise ValueError('collection carries no __PRELINK_INFO,__info')
    declared = {e['CFBundleIdentifier']: e for e in metadata['_PrelinkInfoDictionary']}

    roots = list(args.root) if args.root else list(DEFAULT_ROOTS)
    selected, interfaces, missing = dependency_closure(declared, located, roots)
    if missing:
        raise ValueError('selected components without an image in the collection: %s'
                         % missing)

    components = {}
    for identifier in selected:
        entry = located[identifier]
        image = load_commands(kc, entry['fileoff'])
        segments = read_segments(kc, image)
        component_symbols = read_symtab(kc, image)
        components[identifier] = dict(
            entry=entry, image=image, segments=segments, symbols=component_symbols,
            header=entry['vmaddr'],
            text=next((s for s in segments if s['name'] == '__TEXT'), None),
            kmod=next((v for n, _t, v in component_symbols if n == '_kmod_info'), None),
            functions=read_function_starts(kc, image, segments))

    kernel_entry = located.get('com.apple.kernel')
    if kernel_entry is None:
        raise ValueError('collection has no com.apple.kernel entry')
    apple_kernel_image = load_commands(kc, kernel_entry['fileoff'])
    apple_kernel_symbols = SymbolTable(read_symtab(kc, apple_kernel_image), 'apple-kernel')
    kernel_ranges = [(s['vmaddr'], s['vmaddr'] + max(s['vmsize'], 1))
                     for s in read_segments(kc, apple_kernel_image)
                     if s['name'] != '__LINKEDIT' and s['vmsize']]

    selected_ranges, group_parts = [], {g[0]: [] for g in GROUPS}
    for identifier in selected:
        for segment in components[identifier]['segments']:
            if segment['name'] == '__LINKEDIT' or not segment['filesize']:
                continue
            selected_ranges.append((segment['vmaddr'],
                                    segment['vmaddr'] + segment['filesize'],
                                    identifier, segment['name']))
            for group_name, component_name, _prot in GROUPS:
                if segment['name'] == component_name:
                    group_parts[group_name].append(dict(
                        identifier=identifier, vmaddr=segment['vmaddr'],
                        vmsize=segment['vmsize'], filesize=segment['filesize'],
                        fileoff=segment['fileoff']))
    selected_ranges.sort()

    def locate_selected(address):
        for low, high, identifier, name in selected_ranges:
            if low <= address < high:
                return identifier, name
        return None

    def inside(ranges, address):
        return any(low <= address < high for low, high in ranges)

    # ---- pointer census ----------------------------------------------------
    chain = chain_header(kc, kc_image)
    if chain is None:
        raise ValueError('kernel collection has no LC_DYLD_CHAINED_FIXUPS')
    slots, distinct_external = [], {}
    for slot in walk_slots(kc, chain, kc_segments):
        host = locate_selected(slot['va'])
        if host is None:
            continue
        slot['host'] = host
        slot['old_target'] = kc_base + slot['target']
        target_host = locate_selected(slot['old_target'])
        if target_host is not None and target_host[0] == host[0]:
            slot['category'] = 'a'
        elif target_host is not None:
            slot['category'] = 'b'
        elif inside(kernel_ranges, slot['old_target']):
            slot['category'] = 'c'
            distinct_external[slot['old_target']] = \
                distinct_external.get(slot['old_target'], 0) + 1
        else:
            slot['category'] = 'd'
        slots.append(slot)

    coverage = dict(
        external_slots=sum(1 for s in slots if s['category'] == 'c'),
        distinct_external_targets=len(distinct_external),
        slots_by_category={k: sum(1 for s in slots if s['category'] == k) for k in 'abcd'},
        apple_kernel_symbols=len(apple_kernel_symbols),
        native_symbols=len(kernel_symbols),
        apple_addresses_with_duplicate_names=apple_kernel_symbols.duplicate_addresses,
    )
    exact_hits = resolvable = 0
    for address in distinct_external:
        names = apple_kernel_symbols.names_at(address)
        if names:
            exact_hits += 1
            if any(n in kernel_symbols.by_name for n in names):
                resolvable += 1
    coverage['exact_symbol_hits'] = exact_hits
    coverage['exact_percent'] = round(100.0 * exact_hits / max(1, len(distinct_external)), 2)
    coverage['name_present_in_native'] = resolvable
    coverage['name_present_percent'] = round(
        100.0 * resolvable / max(1, len(distinct_external)), 2)
    coverage['apple_symbol_names_present_in_native'] = sum(
        1 for n in apple_kernel_symbols.by_name if n in kernel_symbols.by_name)
    coverage['apple_symbol_names'] = len(apple_kernel_symbols.by_name)
    coverage['meets_95_percent'] = coverage['exact_percent'] >= 95.0

    code_bytes = sum(s['filesize'] for i in selected
                     for s in components[i]['segments']
                     if s['name'] in ('__TEXT', '__TEXT_EXEC'))
    print('== dependency closure: %d components, %d kernel interfaces ==' % (
        len(selected), len(interfaces)))
    for identifier in selected:
        component = components[identifier]
        print('   %-52s text=%9d kmod=%s' % (
            identifier, sum(s['filesize'] for s in component['segments']
                            if s['name'] in ('__TEXT', '__TEXT_EXEC')),
            hex(component['kmod'] or 0)))
    print('   total component code: %d bytes (%.2f MB)' % (code_bytes, code_bytes / 1048576))
    print('   interfaces: %s' % ', '.join(sorted(interfaces)))
    print('== symbol coverage for kernel-directed references ==')
    print('   %d distinct targets; exact Apple symbol %.2f%%; name in our kernel %.2f%%'
          % (coverage['distinct_external_targets'], coverage['exact_percent'],
             coverage['name_present_percent']))

    # ---- layout: one uniform slide ---------------------------------------
    rel_low = align_down(min(s['vmaddr'] for parts in group_parts.values() for s in parts))
    rel_high = align_up(max(s['vmaddr'] + s['vmsize']
                            for parts in group_parts.values() for s in parts))
    extent = rel_high - rel_low

    placeholder = dict(metadata)
    placeholder_entries = []
    for identifier in selected:
        entry = dict(declared[identifier])
        entry['_PrelinkBundlePath'] = declared[identifier].get(
            '_PrelinkBundlePath', '/System/Library/Extensions/%s.kext' % identifier)
        entry['_PrelinkExecutableLoadAddr'] = struct.pack('<Q', 0)
        entry['_PrelinkExecutableSize'] = struct.pack('<Q', 0)
        entry['_PrelinkKmodInfo'] = struct.pack('<Q', 0)
        placeholder_entries.append(entry)
    for identifier in interfaces:
        placeholder_entries.append(dict(interfaces[identifier]))
    placeholder['_PrelinkInfoDictionary'] = placeholder_entries
    placeholder.pop('_PrelinkLinkKASLROffsets', None)
    info_size = align_up(len(plistlib.dumps(placeholder)))

    total_extent = extent + VENEER_RESERVE + info_size
    kernel_text = next(s for s in kernel_segments if s['name'] == '__TEXT')
    band_top = kernel_text['vmaddr']
    if total_extent >= band_top:
        raise Refused('uniform-slide band (%d bytes) does not fit below the kernel'
                      % total_extent)
    slide = (band_top - total_extent) - rel_low

    placed_groups = {}
    table_size = 8 * len([i for i in selected if components[i]['text'] is not None])
    for group_name, _component_name, prot in GROUPS:
        parts = group_parts[group_name]
        if not parts:
            continue
        low = align_down(min(s['vmaddr'] for s in parts))
        text_span = align_up(max(s['vmaddr'] + s['filesize'] for s in parts)) - low
        file_high = low + text_span
        table_offset = None
        if group_name == '__PRELINK_TEXT':
            # The sparse header index lives inside this segment, after __text:
            # reserve it now, because growing the segment after the other
            # segments have been placed on file would overlap their bytes.
            table_offset = align_up(text_span, 8)
            # arm_vm_page_granular_*() works in page granules, so the segment
            # must end on a page boundary even though the table itself is only
            # 8-byte aligned.
            file_high = max(file_high, align_up(low + table_offset + table_size))
        vm_high = max(align_up(max(s['vmaddr'] + s['vmsize'] for s in parts)), file_high)
        blob = bytearray(vm_high - low)
        for part in parts:
            start = part['vmaddr'] - low
            blob[start:start + part['filesize']] = \
                kc[part['fileoff']:part['fileoff'] + part['filesize']]
        placed_groups[group_name] = dict(
            name=group_name, old_low=low, vmaddr=low + slide, vmsize=vm_high - low,
            filesize=file_high - low, text_span=text_span, prot=prot,
            blob=blob, parts=parts, fileoff=None, table_offset=table_offset)

    veneer_va = rel_high + slide
    info_va = veneer_va + VENEER_RESERVE

    output = bytearray(native_data)
    for group_name, _component_name, _prot in GROUPS:
        group = placed_groups.get(group_name)
        if group is None:
            continue
        group['fileoff'] = align_up(len(output))
        output.extend(bytes(group['fileoff'] - len(output)))
        output.extend(group['blob'])
    veneer_fileoff = align_up(len(output))
    output.extend(bytes(veneer_fileoff - len(output) + VENEER_RESERVE))

    # ---- kernel-directed symbol binding ----------------------------------
    def bind(apple_va):
        """Apple kernel VA -> (our VA, symbol name, exact)."""
        names = apple_kernel_symbols.names_at(apple_va)
        if names:
            present = sorted(n for n in names if n in kernel_symbols.by_name)
            if present:
                return kernel_symbols.by_name[present[0]], present[0], True
            return None, sorted(names)[0], True
        nearest = apple_kernel_symbols.nearest_below(apple_va)
        if nearest is None:
            return None, None, False
        names = apple_kernel_symbols.names_at(nearest)
        present = sorted(n for n in names if n in kernel_symbols.by_name)
        if not present:
            return None, (sorted(names)[0] if names else None), False
        return (kernel_symbols.by_name[present[0]] + (apple_va - nearest),
                present[0], False)

    # ---- branch relocation (intra-closure untouched: uniform slide) -------
    branch_relocations, veneers, unresolved = [], [], {}
    approximate = 0

    def fail(name, entry):
        unresolved.setdefault(name or 'unknown', []).append(entry)

    for identifier in selected:
        component = components[identifier]
        if not component['functions']:
            continue
        for segment in component['segments']:
            if segment['name'] not in ('__TEXT', '__TEXT_EXEC') or not segment['filesize']:
                continue
            group = placed_groups.get('__PRELINK_TEXT' if segment['name'] == '__TEXT'
                                      else '__PLK_TEXT_EXEC')
            if group is None:
                continue
            body = group['blob']
            base_old = segment['vmaddr']
            offset_in_group = base_old - group['old_low']
            limit = segment['filesize']
            bounds = sorted(f for f in component['functions']
                            if base_old <= f < base_old + limit)
            spans = [(start, min(bounds[i + 1] if i + 1 < len(bounds) else base_old + limit,
                                 base_old + limit))
                     for i, start in enumerate(bounds)]
            for start, end in spans:
                for address in range(start, end - 3, 4):
                    position = offset_in_group + (address - base_old)
                    word, = struct.unpack_from('<I', body, position)
                    decoded = decode_pc_relative(word, address)
                    if decoded is None or decoded['kind'] in ('adr', 'adrp', 'ldr'):
                        # 'ldr' is a literal *load*, not a branch: its target is
                        # the in-kext literal pool (a pointer slot we rebind
                        # separately), never a kernel address.
                        continue
                    target = decoded['target']
                    if locate_selected(target) is not None:
                        continue                     # preserved by the uniform slide
                    if not inside(kernel_ranges, target):
                        continue
                    new_site = address + slide
                    value, symbol, exact = bind(target)
                    if value is None:
                        fail(symbol, dict(site=hex(new_site), old_target=hex(target),
                                          kind='branch:%s' % decoded['kind']))
                        continue
                    encoded = encode_branch(decoded['kind'], value - new_site)
                    if encoded is None:
                        veneer = encode_veneer(value, veneer_va + 12 * len(veneers))
                        if veneer is None:
                            fail(symbol, dict(site=hex(new_site), old_target=hex(target),
                                              kind='branch:%s' % decoded['kind'],
                                              reason='no veneer'))
                            continue
                        start_va = veneer_va + 12 * len(veneers)
                        encoded = encode_branch(decoded['kind'], start_va - new_site)
                        if encoded is None:
                            fail(symbol, dict(site=hex(new_site), old_target=hex(target),
                                              kind='branch:%s' % decoded['kind'],
                                              reason='branch form not veneerable'))
                            continue
                        veneers.append(dict(va=hex(start_va), target=hex(value),
                                            kind=decoded['kind'], bytes=veneer.hex()))
                    struct.pack_into('<I', body, position, encoded)
                    branch_relocations.append(dict(
                        site=hex(new_site), old_word=hex(word), new_word=hex(encoded),
                        kind=decoded['kind'], old_target=hex(target), new_target=hex(value),
                        symbol=symbol, exact=exact, component=identifier))
                    if not exact:
                        approximate += 1

    # ---- adrp + add pairs ------------------------------------------------
    for identifier in selected:
        component = components[identifier]
        if not component['functions']:
            continue
        for segment in component['segments']:
            if segment['name'] not in ('__TEXT', '__TEXT_EXEC') or not segment['filesize']:
                continue
            group = placed_groups.get('__PRELINK_TEXT' if segment['name'] == '__TEXT'
                                      else '__PLK_TEXT_EXEC')
            if group is None:
                continue
            body = group['blob']
            base_old = segment['vmaddr']
            offset_in_group = base_old - group['old_low']
            limit = segment['filesize']
            bounds = sorted(f for f in component['functions']
                            if base_old <= f < base_old + limit)
            spans = [(start, min(bounds[i + 1] if i + 1 < len(bounds) else base_old + limit,
                                 base_old + limit))
                     for i, start in enumerate(bounds)]
            for start, end in spans:
                for address in range(start, end - 7, 4):
                    position = offset_in_group + (address - base_old)
                    word, = struct.unpack_from('<I', body, position)
                    if (word & 0x9F000000) != 0x90000000 or not word >> 31:
                        continue
                    following, = struct.unpack_from('<I', body, position + 4)
                    if not (is_add_imm12(following) and rn_of(following) == rd_of(word)):
                        continue
                    decoded = decode_pc_relative(word, address)
                    full = decoded['page_base'] + ((following >> 10) & 0xFFF)
                    if locate_selected(full) is not None:
                        continue
                    if not inside(kernel_ranges, full):
                        continue
                    new_site = address + slide
                    value, symbol, exact = bind(full)
                    if value is None:
                        fail(symbol, dict(site=hex(new_site), old_target=hex(full),
                                          kind='adrp+add'))
                        continue
                    new_adrp = encode_adrp(rd_of(word), (value & ~0xFFF) - (new_site & ~0xFFF))
                    if new_adrp is None:
                        fail(symbol, dict(site=hex(new_site), old_target=hex(full),
                                          kind='adrp+add-range'))
                        continue
                    new_add = (following & ~(0xFFF << 10)) | ((value & 0xFFF) << 10)
                    struct.pack_into('<II', body, position, new_adrp, new_add)
                    branch_relocations.append(dict(
                        site=hex(new_site), kind='adrp+add', old_word=hex(word),
                        new_word=hex(new_adrp), old_target=hex(full),
                        new_target=hex(value), symbol=symbol, exact=exact,
                        component=identifier))
                    if not exact:
                        approximate += 1

    for veneer in veneers:
        position = veneer_fileoff + (int(veneer['va'], 16) - veneer_va)
        output[position:position + 12] = bytes.fromhex(veneer['bytes'])

    # ---- __macho_list (sparse header index) ------------------------------
    # Done before the blobs are published: appending the table grows the
    # __PRELINK_TEXT blob, and publishing afterwards would undo the per-kext
    # header surgery performed below.
    headers = sorted(components[i]['text']['vmaddr'] + slide for i in selected
                     if components[i]['text'] is not None)
    table = struct.pack('<%dQ' % len(headers), *headers)
    text_group = placed_groups['__PRELINK_TEXT']
    index_offset = text_group['table_offset']
    if index_offset + len(table) > text_group['filesize']:
        raise Refused('__macho_list (%d bytes at %#x) does not fit the %#x-byte '
                      '__PRELINK_TEXT segment'
                      % (len(table), index_offset, text_group['filesize']))
    struct.pack_into('<%dQ' % len(headers), text_group['blob'], index_offset, *headers)
    if args.debug:
        print('debug: __PRELINK_TEXT fileoff=%#x blob=%#x filesize=%#x text_span=%#x '
              'index_offset=%#x out=%#x packed=%s'
              % (text_group['fileoff'], len(text_group['blob']),
                 text_group['filesize'], text_group['text_span'], index_offset,
                 len(output),
                 text_group['blob'][index_offset:index_offset + 8].hex()), file=sys.stderr)
    macho_list_va = text_group['vmaddr'] + index_offset

    # ---- group blobs are final: publish them ------------------------------
    for group_name, group in placed_groups.items():
        output[group['fileoff']:group['fileoff'] + len(group['blob'])] = group['blob']

    def file_position(va, length=1):
        for group in placed_groups.values():
            if group['vmaddr'] <= va and va + length <= group['vmaddr'] + group['filesize']:
                return group['fileoff'] + (va - group['vmaddr'])
        raise KeyError('address %#x is not backed by a prelink group' % va)

    # ---- per-kext header surgery -----------------------------------------
    split_added, header_slack, kmod_names = [], [], []
    for identifier in selected:
        component = components[identifier]
        text = component['text']
        if text is None:
            continue
        new_header = text['vmaddr'] + slide
        position = file_position(new_header)
        first_section = min((s['offset'] for s in text['sections'] if s['offset']),
                            default=component['entry']['fileoff'] + 0x4000)
        end_of_commands = component['entry']['fileoff'] + 32 + component['image']['sizeofcmds']
        slack = first_section - end_of_commands
        header_slack.append(dict(component=identifier, slack=slack))
        if slack < 16:
            raise Refused('%s has %d bytes of header slack, cannot insert '
                          'LC_SEGMENT_SPLIT_INFO' % (identifier, slack))
        ncmds, sizeofcmds = struct.unpack_from('<II', output, position + 16)
        struct.pack_into('<II', output, position + 16, ncmds + 1, sizeofcmds + 16)
        struct.pack_into('<IIII', output, position + 32 + sizeofcmds,
                         LC_SEGMENT_SPLIT_INFO, 16, 0, 0)
        split_added.append(identifier)
        if component['kmod']:
            kmod_position = file_position(component['kmod'] + slide, 256)
            name = cstr(output[kmod_position + 16:kmod_position + 80])
            # The shipped layout does not match osfmk/mach/kmod.h field offsets
            # (address lands at 156, not 160), so locate the address field by
            # value: exactly one u64 in the struct equals this kext's load
            # address, and `size` sits immediately after it.
            hits = [o for o in range(140, 200, 4)
                    if struct.unpack_from('<Q', output, kmod_position + o)[0]
                    == text['vmaddr']]
            if len(hits) != 1:
                raise Refused('%s: kmod_info load address found at %r, expected one hit'
                              % (identifier, hits))
            address_offset = hits[0]
            previous_size, = struct.unpack_from('<Q', output,
                                                kmod_position + address_offset + 8)
            if not 0 < previous_size <= 0x4000000:
                raise Refused('%s: kmod_info size field %#x is implausible at offset %d'
                              % (identifier, previous_size, address_offset))
            kmod_names.append(dict(component=identifier, name=name,
                                   address_offset=address_offset,
                                   original_size=previous_size))
            struct.pack_into('<Q', output, kmod_position + address_offset, new_header)
            struct.pack_into('<Q', output, kmod_position + address_offset + 8,
                             max(previous_size, 0))

    # ---- pointer rebinding ------------------------------------------------
    relocations = []
    for slot in slots:
        host_identifier, host_name = slot['host']
        group = placed_groups[next(g[0] for g in GROUPS if g[1] == host_name)]
        new_site = slot['va'] + slide
        position = group['fileoff'] + (new_site - group['vmaddr'])
        if slot['category'] in ('a', 'b'):
            value, symbol, exact = slot['old_target'] + slide, None, True
        elif slot['category'] == 'c':
            value, symbol, exact = bind(slot['old_target'])
            if value is None:
                fail(symbol, dict(site=hex(new_site), old_target=hex(slot['old_target']),
                                  kind='pointer'))
                continue
            if not exact:
                approximate += 1
        else:
            fail('outside-closure@%#x' % slot['old_target'],
                 dict(site=hex(new_site), old_target=hex(slot['old_target']),
                      kind='pointer'))
            continue
        struct.pack_into('<Q', output, position, value)
        relocations.append(dict(site=hex(new_site), old_site=hex(slot['va']),
                                old_target=hex(slot['old_target']),
                                new_target=hex(value), category=slot['category'],
                                symbol=symbol, exact=exact, segment=host_name,
                                component=host_identifier))

    # ---- __PRELINK_INFO ---------------------------------------------------
    info_entries = []
    for identifier in selected:
        component = components[identifier]
        entry = dict(declared[identifier])
        entry['_PrelinkBundlePath'] = declared[identifier].get(
            '_PrelinkBundlePath', '/System/Library/Extensions/%s.kext' % identifier)
        header_va = component['text']['vmaddr'] + slide
        image_size = align_up(max(s['vmaddr'] + s['vmsize'] for s in component['segments']
                                  if s['name'] != '__LINKEDIT' and s['filesize'])
                              - component['text']['vmaddr'])
        entry['_PrelinkExecutableLoadAddr'] = struct.pack('<Q', header_va)
        entry['_PrelinkExecutableSize'] = struct.pack('<Q', image_size)
        entry['_PrelinkKmodInfo'] = struct.pack('<Q', (component['kmod'] or 0) + slide)
        info_entries.append(entry)
    for identifier in interfaces:
        info_entries.append(dict(interfaces[identifier]))
    info_plist = dict(metadata)
    info_plist['_PrelinkInfoDictionary'] = info_entries
    info_plist.pop('_PrelinkLinkKASLROffsets', None)
    info_blob = plistlib.dumps(info_plist)
    if align_up(len(info_blob)) != info_size:
        raise Refused('__PRELINK_INFO size changed between passes (%d vs %d)'
                      % (align_up(len(info_blob)), info_size))
    info_fileoff = align_up(len(output))
    output.extend(bytes(info_fileoff - len(output)))
    output.extend(info_blob)
    output.extend(bytes(info_size - len(info_blob)))

    # ---- __macho_list already emitted with the group blobs ---------------

    # ---- rewrite the placeholder segment commands ------------------------
    first_section_off = min((s['offset'] for s in kernel_text['sections'] if s['offset']),
                            default=kernel_text['filesize'])
    commands_end = 32 + kernel_image['sizeofcmds']
    if first_section_off - commands_end < 80:
        raise Refused('kernel has %d bytes of load-command slack; cannot add the '
                      '__macho_list section' % (first_section_off - commands_end))

    def segment_command(name, va, vmsize, fileoff, filesize, prot, sections):
        return (struct.pack('<II16s4Q4I', LC_SEGMENT_64, 72 + 80 * len(sections),
                            name.encode(), va, vmsize, fileoff, filesize, prot, prot,
                            len(sections), 0) + b''.join(sections))

    def section_command(name, segment, addr, size, offset):
        return struct.pack('<16s16sQQ8I', name.encode(), segment.encode(), addr, size,
                           offset, 0, 0, 0, 0, 0, 0, 0)

    replacements = {}
    for group_name, group in placed_groups.items():
        if group_name == '__PRELINK_TEXT':
            sections = [section_command('__text', group_name, group['vmaddr'],
                                        group['text_span'], group['fileoff']),
                        section_command('__macho_list', group_name, macho_list_va,
                                        len(table), group['fileoff'] + index_offset)]
        else:
            sections = [section_command('__data' if group['prot'] == 3 else '__text',
                                        group_name, group['vmaddr'], group['text_span'],
                                        group['fileoff'])]
        replacements[group_name] = segment_command(
            group_name, group['vmaddr'], group['vmsize'], group['fileoff'],
            group['filesize'], group['prot'], sections)
    replacements['__PRELINK_INFO'] = segment_command(
        '__PRELINK_INFO', info_va, info_size, info_fileoff, info_size, 3,
        [section_command('__info', '__PRELINK_INFO', info_va, len(info_blob),
                         info_fileoff)])

    rebuilt = bytearray()
    for cmd, offset, cmdsize in kernel_image['commands']:
        raw = bytes(native_data[offset:offset + cmdsize])
        if cmd == LC_SEGMENT_64:
            name = cstr(native_data[offset + 8:offset + 24])
            if name in replacements:
                raw = replacements[name]
        rebuilt.extend(raw)
    struct.pack_into('<II', output, 16, kernel_image['ncmds'], len(rebuilt))
    output[32:32 + len(rebuilt)] = rebuilt

    if struct.unpack_from('<i', output, 4)[0] != CPU_TYPE_ARM64:
        raise Refused('kernel cputype is not CPU_TYPE_ARM64')
    struct.pack_into('<i', output, 8, 0)

    # ---- structural assertions -------------------------------------------
    assertions = []

    def check(name, ok, detail=''):
        assertions.append(dict(name=name, ok=bool(ok), detail=detail))
        return bool(ok)

    image = load_commands(output, 0)
    out_segments = read_segments(output, image)
    out_text = next((s for s in out_segments if s['name'] == '__PRELINK_TEXT'), None)
    out_section_map = {s['name']: s for s in (out_text['sections'] if out_text else [])}
    check('__PRELINK_TEXT,__text exists', '__text' in out_section_map,
          hex(out_section_map['__text']['size']) if '__text' in out_section_map else 'missing')
    check('__PRELINK_TEXT,__macho_list exists', '__macho_list' in out_section_map,
          '%d entries' % len(headers) if '__macho_list' in out_section_map else 'missing')
    if '__macho_list' in out_section_map:
        listed = list(struct.unpack_from('<%dQ' % len(headers), output,
                                         out_section_map['__macho_list']['offset']))
        check('__macho_list content equals the computed header list',
              listed == headers,
              '%s vs %s' % ([hex(v) for v in listed[:3]], [hex(v) for v in headers[:3]]))
    check('__PRELINK_TEXT below the 48 MB ceiling',
          out_text is not None and out_text['vmsize'] < MAX_PRELINK_TEXT,
          hex(out_text['vmsize']) if out_text else 'missing')
    containment = True
    for group_name, group in placed_groups.items():
        for part in group['parts']:
            start = part['vmaddr'] + slide
            if not (group['vmaddr'] <= start and
                    start + part['filesize'] <= group['vmaddr'] + group['filesize']):
                containment = False
    check('component segments contained in their group', containment)

    split_ok = True
    for identifier in selected:
        component = components[identifier]
        position = file_position(component['text']['vmaddr'] + slide)
        ncmds, _sizeofcmds = struct.unpack_from('<II', output, position + 16)
        cursor, found = position + 32, False
        for _ in range(ncmds):
            cmd, cmdsize = struct.unpack_from('<II', output, cursor)
            found = found or cmd == LC_SEGMENT_SPLIT_INFO
            cursor += cmdsize
        split_ok = split_ok and found
    check('every flattened kext has LC_SEGMENT_SPLIT_INFO', split_ok)

    out_ranges = [(s['vmaddr'], s['vmaddr'] + max(s['vmsize'], 1)) for s in out_segments
                  if s['vmsize']]
    kernel_ranges_out = [(s['vmaddr'], s['vmaddr'] + max(s['vmsize'], 1))
                         for s in kernel_segments if s['vmsize']]
    dangling = [r for r in relocations
                if not inside(out_ranges, int(r['new_target'], 16))
                and not inside(kernel_ranges_out, int(r['new_target'], 16))]
    check('every written pointer lands in the image or our kernel', not dangling,
          '%d dangling' % len(dangling))

    sample = [r for r in relocations if r['category'] in ('a', 'b')]
    step = max(1, len(sample) // 500)
    sampled = sample[::step][:500]
    broken = [r for r in sampled
              if int(r['new_target'], 16) - int(r['site'], 16) !=
              int(r['old_target'], 16) - int(r['old_site'], 16)]
    check('uniform-slide relative distance preserved (%d samples)' % len(sampled),
          not broken, '%d mismatches' % len(broken))

    bind_ok = all(r['symbol'] in kernel_symbols.by_name
                  for r in relocations if r['category'] == 'c' and r['symbol'])
    check('every kernel binding names a real native symbol', bind_ok)

    coverage['approximate'] = approximate
    loadable = (not unresolved) and all(a['ok'] for a in assertions)

    report = dict(
        tool='scripts/prelink_kcg_bind.py',
        design='uniform-slide + sparse __macho_list (kalloc_sparse_prelink_index_version)',
        decoder_self_test=decoder_checks,
        kernel=str(kernel_path),
        kernel_sha256=hashlib.sha256(native_data).hexdigest(),
        collection=str(kc_path),
        collection_sha256=hashlib.sha256(kc).hexdigest(),
        fileset=str(fileset_path) if fileset_path.is_file() else None,
        roots=roots,
        slide=hex(slide),
        slide_signed=slide,
        components=sorted(selected),
        component_count=len(selected),
        component_code_bytes=code_bytes,
        kernel_interfaces=sorted(interfaces),
        kernel_collection_base=hex(kc_base),
        kernel_collection_cputype=hex(kc_image['cputype']),
        kernel_collection_cpusubtype=hex(kc_image['cpusubtype']),
        output_cputype=hex(struct.unpack_from('<I', output, 4)[0]),
        output_cpusubtype=hex(struct.unpack_from('<I', output, 8)[0]),
        chain_format=chain['version'],
        chain_imports=chain['imports_count'],
        prelink_text_layout=[
            dict(component=i, header=hex(components[i]['text']['vmaddr'] + slide),
                 size=components[i]['text']['filesize'])
            for i in selected if components[i]['text'] is not None],
        segment_placement={name: dict(vmaddr=hex(g['vmaddr']), vmsize=hex(g['vmsize']),
                                      filesize=hex(g['filesize']), fileoff=hex(g['fileoff']),
                                      prot=g['prot'], parts=len(g['parts']))
                           for name, g in placed_groups.items()},
        prelink_info=dict(vmaddr=hex(info_va), fileoff=hex(info_fileoff),
                          size=len(info_blob), entries=len(info_entries)),
        macho_list=dict(vmaddr=hex(macho_list_va), entries=len(headers),
                        offset=hex(index_offset)),
        band=dict(low=hex(rel_low + slide), high=hex(band_top), extent=total_extent,
                  extent_mb=round(total_extent / 1048576, 2),
                  gap_bytes=extent - sum(g['filesize'] for g in placed_groups.values())),
        symbol_coverage=coverage,
        slots_total=len(slots),
        slots_by_category=coverage['slots_by_category'],
        pointer_relocations=relocations,
        pointer_relocation_count=len(relocations),
        branch_relocations=branch_relocations,
        branch_relocation_count=len(branch_relocations),
        veneers=veneers,
        veneer_count=len(veneers),
        approximate=approximate,
        unresolved=dict(sorted(unresolved.items())),
        unresolved_sites=sum(len(v) for v in unresolved.values()),
        kalloc_split_info_added=split_added,
        header_slack=header_slack,
        kmod_info_names=kmod_names,
        structural_assertions=assertions,
        loadable=loadable,
        output=str(args.output),
        output_bytes=len(output),
        output_sha256=hashlib.sha256(output).hexdigest(),
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')

    if unresolved and not args.allow_unresolved:
        raise Refused('%d unresolved references across %d names; report at %s, image '
                      'not written (--allow-unresolved overrides)'
                      % (report['unresolved_sites'], len(unresolved), args.report))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(bytes(output))
    return report


def decoder_self_test():
    """Round-trip every PC-relative form; a wrong immediate layout here would
    silently mis-rewrite code, so it is asserted before any image is touched."""
    base = 0xFFFFFE0001234000
    checks = []
    for kind, delta in (('b', 0x1000), ('bl', -0x2000), ('b.cond', 0x400),
                        ('b.cond', -0x3FFFC)):
        word = encode_branch(kind, delta)
        if word is None:
            raise AssertionError('cannot encode %s %#x' % (kind, delta))
        decoded = decode_pc_relative(word, base)
        if decoded is None or decoded['kind'] != kind or decoded['target'] != base + delta:
            raise AssertionError('decode/encode mismatch for %s %#x -> %r'
                                 % (kind, delta, decoded))
        checks.append(kind)
    # TBZ/TBNZ: build the encoding by hand and require the exact target back.
    for bit, delta in ((31, 0x100), (5, -0x40), (0, 0x1FFC), (63, -0x2000)):
        immediate = delta & 0x3FFF
        word = (0x36000000 | (bit >> 5) << 31 | ((bit & 0x1F) << 19)
                | (immediate << 5) | 0)
        decoded = decode_pc_relative(word, base)
        if decoded is None or decoded['kind'] != 'tbz' or decoded['target'] != base + delta:
            raise AssertionError('TBZ decode mismatch bit=%d delta=%#x -> %r'
                                 % (bit, delta, decoded))
    checks.append('tbz')
    # CBZ: imm19 word-scaled (decode only; calls are never CBZ).
    for delta in (0x1FFFC, -0x20000):
        word = 0x34000000 | (((delta >> 2) & 0x7FFFF) << 5)
        decoded = decode_pc_relative(word, base)
        if decoded is None or decoded['kind'] != 'cbz' or decoded['target'] != base + delta:
            raise AssertionError('CBZ decode mismatch %#x -> %r' % (delta, decoded))
    checks.append('cbz')
    # ADRP / ADD
    adrp = encode_adrp(16, 0x1234000)
    decoded = decode_pc_relative(adrp, base)
    if decoded is None or decoded['target'] != (base & ~0xFFF) + 0x1234000:
        raise AssertionError('ADRP round-trip mismatch: %r' % (decoded,))
    checks.append('adrp')
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--native', type=Path, default=None,
                        help='our kernel image (default: %s)' % DEFAULT_KERNEL)
    parser.add_argument('--kc', type=Path, default=None,
                        help='macOS fileset kernel collection (default: %s)' % DEFAULT_KC)
    parser.add_argument('--fileset', type=Path, default=None,
                        help='per-entry fileset JSON (default: %s)' % DEFAULT_FILESET)
    parser.add_argument('--root', action='append', default=[],
                        help='bundle id to prelink (repeatable)')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--debug', action='store_true',
                        help='print placement diagnostics to stderr')
    parser.add_argument('--allow-unresolved', action='store_true',
                        help='write the image even when references stay unresolved')
    args = parser.parse_args(argv)

    try:
        report = run(args)
    except Refused as failure:
        print('prelink_kcg_bind: %s' % failure, file=sys.stderr)
        return 2
    print('\n== summary ==')
    for key in ('slide', 'component_count', 'component_code_bytes', 'slots_by_category',
                'pointer_relocation_count', 'branch_relocation_count', 'veneer_count',
                'approximate', 'unresolved_sites', 'loadable', 'output_bytes'):
        print('   %-26s %s' % (key, report[key]))
    print('   %-26s %.2f%%' % ('symbol coverage', report['symbol_coverage']['exact_percent']))
    print('   %-26s %.2f MB (%.2f MB gaps)' % (
        'band extent', report['band']['extent_mb'], report['band']['gap_bytes'] / 1048576))
    for assertion in report['structural_assertions']:
        print('   [%s] %-52s %s' % ('ok' if assertion['ok'] else 'FAIL',
                                    assertion['name'], assertion['detail']))
    return 0 if report['loadable'] else 1


if __name__ == '__main__':
    sys.exit(main())
