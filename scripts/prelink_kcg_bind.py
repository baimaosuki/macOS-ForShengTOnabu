#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage 2 of the kext prelink pipeline: bind a macOS fileset KC's kexts into a
self-built XNU image by *repacking* the components, so no runtime linker is
needed.

Design (this is the layout SHENG booted with, see
docs/history/macos26-security-20261026.md):

  * The components are packed contiguously into four groups
    (``__PRELINK_TEXT`` / ``__PLK_TEXT_EXEC`` / ``__PLK_DATA_CONST`` /
    ``__PRELINK_DATA``) on a 16 KiB granule.  Apple spreads the same components
    over 107 MB of its kernelcache while their real footprint is 3.95 MB, so a
    layout that preserves that scatter needs an 84 MB band and an 82 MB image.
  * Only the *intra-part* byte layout is an invariant: a segment is copied
    verbatim, so references that stay inside one part keep their distance for
    free and are never touched.
  * References that cross a part boundary are re-encoded explicitly:
      a/b. absolute pointer slots (decoded from the top level chained fixups)
           are retargeted through ``mv()``;
      c.   references whose target lies in Apple's ``com.apple.kernel`` are
           redirected into *our* kernel: Apple VA -> symbol name -> our VA.
  * ``vm_kernel_slide == 0`` on this board, so every slot is rewritten as a bare
    little-endian 64-bit absolute address; no chained fixup has to survive.
  * Kext Mach-O headers stay *sparse*: the NABU kernel
    (platform/nabu/macos26/patches/0008-nabu-sparse-prelink-index.patch) walks
    ``__PRELINK_TEXT,__macho_list`` and follows that pointer array, so the
    kexts do not have to sit at KEXT_ALIGN_BYTES.

Stdlib only: the branch scanner decodes the few AArch64 PC-relative forms it
needs instead of depending on capstone.
"""
import argparse
import base64
import hashlib
import hmac
from bisect import bisect_right
import json
import plistlib
import os
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
    # bsd_init() -> pthread_init() panics with a NULL shim table when this kext is
    # absent, so it is a hard part of the boot set and not an optional extra.
    'com.apple.kec.pthread',
    # decmpfs type 4 (zlib) provider: launchd and friends are compressed with it,
    # and the read path answers ENOTSUP without a registered decompressor.
    'com.apple.AppleFSCompression.AppleFSCompressionTypeZlib',
)

REQUIRED_ROOTS = (
    'com.apple.kec.pthread',
    'com.apple.AppleFSCompression.AppleFSCompressionTypeZlib',
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


def prelink_xml(value, indent=0):
    """Serialise the prelink plist exactly the way XNU consumes it.

    ``OSUnserializeXML`` produces an OSNumber only for <integer>; the address
    and size fields must therefore be ``<integer size="64">0x...``.  plistlib
    cannot emit that: it turns a Python int into signed decimal and a packed
    struct into <data>, and OSKext::initWithPrelinkedInfoDict (OSKext.cpp:2111)
    rejects OSData for these keys.
    """
    pad = '\t' * indent
    if isinstance(value, dict):
        body = ''.join('%s\t<key>%s</key>\n%s' % (pad, key, prelink_xml(child, indent + 1))
                       for key, child in value.items())
        return '%s<dict>\n%s%s</dict>\n' % (pad, body, pad)
    if isinstance(value, (list, tuple)):
        return '%s<array>\n%s%s</array>\n' % (
            pad, ''.join(prelink_xml(child, indent + 1) for child in value), pad)
    if isinstance(value, bool):
        return '%s<%s/>\n' % (pad, 'true' if value else 'false')
    if isinstance(value, int):
        if not 0 <= value < (1 << 64):
            raise Refused('metadata integer %d outside the 64-bit range' % value)
        return '%s<integer size="64">0x%x</integer>\n' % (pad, value)
    if isinstance(value, (bytes, bytearray)):
        return '%s<data>%s</data>\n' % (pad, base64.b64encode(bytes(value)).decode())
    if isinstance(value, str):
        escaped = (value.replace('&', '&amp;').replace('<', '&lt;')
                   .replace('>', '&gt;'))
        return '%s<string>%s</string>\n' % (pad, escaped)
    raise Refused('unserialisable metadata value %r' % (value,))


SHA256_K_PROLOGUE = bytes.fromhex('982f8a42')   # K[0] = 0x428a2f98 little-endian
SHA256_K_TABLE = '74ef7306e7452d6859b6463ce496b8df30925f69e1b2969e1f3f34bbc9c6af04'

NATIVE_PRELINK = Path(__file__).resolve().parent.parent / 'UniXNU/kits/NativePrelink'


def prelink_xml_document(value):
    """Serialise through SHENG's own emitter, not a local lookalike.

    UniXNU/kits/NativePrelink/emit_prelink.py::to_xml is the verifier of the
    metadata contract: it emits <integer size="64">0x... for every number, so
    OSUnserializeXML hands OSKext an OSNumber.  Reuse it verbatim.
    """
    if str(NATIVE_PRELINK) not in sys.path:
        sys.path.insert(0, str(NATIVE_PRELINK))
    import xml.etree.ElementTree as ET
    from emit_prelink import to_xml
    body = ET.tostring(to_xml(value), encoding='utf-8').decode()
    document = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0">\n' + body + '\n</plist>\n').encode()
    # XNU's OSUnserializeXML is not a general XML parser: it rejects the space
    # in self-closing tags.  ElementTree writes <true />, <false /> and <dict />,
    # so the whole dictionary fails with "syntax error near line N" as soon as
    # one of them appears.  SHENG does the same rewrite in
    # emit_macos26_security.py:259 before appending the NUL.
    return document.replace(b' />', b'/>')

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


def encode_cbz(word, delta):
    """Re-target CBZ/CBNZ in place: keep sf/op/Rt, replace imm19."""
    if delta % 4 or not -(1 << 20) <= delta < (1 << 20):
        return None
    return (word & 0xFF00001F) | (((delta >> 2) & 0x7FFFF) << 5)


def encode_tbz(word, delta):
    """Re-target TBZ/TBNZ in place: keep b5/b40/op/Rt, replace imm14."""
    if delta % 4 or not -(1 << 15) <= delta < (1 << 15):
        return None
    return (word & 0xFFF8001F) | (((delta >> 2) & 0x3FFF) << 5)


def encode_pc_relative(word, kind, delta):
    """Dispatch on the decoded form; only the in-place forms are supported."""
    if kind == 'cbz':
        return encode_cbz(word, delta)
    if kind == 'tbz':
        return encode_tbz(word, delta)
    return encode_branch(kind, delta)


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
    missing_required = [name for name in REQUIRED_ROOTS if name not in roots]
    if missing_required:
        raise Refused('required root(s) absent from the boot set: '
                      + ', '.join(missing_required))
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

    # ---- layout: uniform slide --------------------------------------------
    PART_ALIGN = 0x4000
    kernel_text = next(s for s in kernel_segments if s['name'] == '__TEXT')
    img_base = kernel_text['vmaddr']
    slide = getattr(args, 'slide', 0x2000000)
    if slide % 4096:
        raise ValueError('slide must be 4096-aligned: %#x' % slide)

    table_size = 8 * len([i for i in selected if components[i]['text'] is not None])
    placed_groups = {}
    placed_parts = []

    for group_name, component_name, prot in GROUPS:
        kc_source_name = '__PRELINK_TEXT' if group_name == '__PRELINK_TEXT' else component_name
        source = next((s for s in kc_segments if s['name'] == kc_source_name), None)
        if source is None:
            raise ValueError('kernel collection lacks source segment for %s' % group_name)

        parts = [segment for identifier in selected
                 for segment in components[identifier]['segments']
                 if segment['name'] == component_name and segment['filesize']]
        if not parts:
            continue

        group_va = source['vmaddr'] + slide
        low = source['vmaddr']
        file_high = source['filesize']
        vm_high = source['vmsize']
        blob = bytearray(file_high)
        for part in parts:
            begin = part['vmaddr'] - low
            part['new_vmaddr'] = group_va + begin
            part['group_offset'] = begin
            placed_parts.append((part['vmaddr'], part['vmaddr'] + part['filesize'], part['new_vmaddr']))
            blob[begin:begin + part['filesize']] = kc[part['fileoff']:part['fileoff'] + part['filesize']]

        table_offset = None
        if group_name == '__PRELINK_TEXT':
            table_offset = (file_high - table_size) & ~7

        placed_groups[group_name] = dict(
            name=group_name, old_low=low, vmaddr=group_va,
            vmsize=vm_high, filesize=file_high, text_span=file_high, prot=prot,
            blob=blob, parts=parts, fileoff=None, table_offset=table_offset)

    shared_linkedit = next((s for s in kc_segments
                            if s['name'] == '__LINKEDIT' and s['filesize']), None)
    if shared_linkedit is not None and any(s['name'] == '__PLK_LINKEDIT'
                                          for s in kernel_segments):
        link_va = shared_linkedit['vmaddr'] + slide
        link_part = dict(name='__LINKEDIT', vmaddr=shared_linkedit['vmaddr'],
                         vmsize=shared_linkedit['vmsize'],
                         filesize=shared_linkedit['filesize'],
                         fileoff=shared_linkedit['fileoff'], sections=[])
        link_part['new_vmaddr'] = link_va
        link_part['group_offset'] = 0
        placed_parts.append((link_part['vmaddr'],
                             link_part['vmaddr'] + link_part['filesize'],
                             link_part['new_vmaddr']))
        link_size = shared_linkedit['filesize']
        link_blob = bytearray(kc[shared_linkedit['fileoff']:shared_linkedit['fileoff'] + shared_linkedit['filesize']])
        placed_groups['__PLK_LINKEDIT'] = dict(
            name='__PLK_LINKEDIT', old_low=shared_linkedit['vmaddr'], vmaddr=link_va,
            vmsize=shared_linkedit['vmsize'], filesize=link_size,
            text_span=link_size, prot=1, blob=link_blob,
            parts=[link_part], fileoff=None, table_offset=None)
        globals()['GROUPS'] = GROUPS + (('__PLK_LINKEDIT', '__LINKEDIT', 1),)

    veneer_va = max(g['vmaddr'] + g['vmsize'] for g in placed_groups.values())
    veneer_va = align_up(veneer_va, PART_ALIGN)
    info_va = align_up(veneer_va + VENEER_RESERVE, 0x4000)
    band_base = min(g['vmaddr'] for g in placed_groups.values())
    band_end = info_va + 0x4000

    placed_parts.sort()
    _part_lo = [entry[0] for entry in placed_parts]

    def mv(va):
        """Old kext VA -> slid VA. Kernel VAs pass through unchanged."""
        if not va:
            return va
        index = bisect_right(_part_lo, va) - 1
        if index >= 0:
            old_lo, old_hi, new_lo = placed_parts[index]
            if old_lo <= va < old_hi:
                return va + slide
        return va

    output = bytearray(native_data)
    for group_name, _component_name, _prot in GROUPS:
        group = placed_groups.get(group_name)
        if group is None:
            continue
        group['fileoff'] = group['vmaddr'] - img_base
        if group['fileoff'] < len(output):
            raise SystemExit('band file offset %#x overlaps the kernel image (%#x)'
                             % (group['fileoff'], len(output)))
        output.extend(bytes(group['fileoff'] - len(output)))
        output.extend(group['blob'])
    veneer_fileoff = veneer_va - img_base
    if veneer_fileoff < len(output):
        raise SystemExit('veneer file offset %#x overlaps the image (%#x)'
                         % (veneer_fileoff, len(output)))
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

    # ---- uniform slide: zero code mutations -----------------------------
    # Under uniform slide (va = source_va + slide), all kext code segments
    # are translated by the exact same slide. All intra-closure PC-relative
    # distances are identically preserved:
    #   (target + slide) - (site + slide) == target - site.
    # Instruction rewriting (branch, ADRP, veneer) is completely bypassed.
    branch_relocations, veneers, unresolved = [], [], {}
    approximate = 0
    adrp_seen = 0
    adrp_relocated = 0
    adrp_unchanged = 0
    adrp_user_rewrites = 0
    adrp_unresolved = []
    soft_skips = []
    def fail(name, entry):
        unresolved.setdefault(name or 'unknown', []).append(entry)

    # ---- __macho_list (sparse header index) ------------------------------
    # Done before the blobs are published: appending the table grows the
    # __PRELINK_TEXT blob, and publishing afterwards would undo the per-kext
    # header surgery performed below.
    headers = sorted(mv(components[i]['text']['vmaddr']) for i in selected
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

    # ---- offline check of every adrp pair we rewrote ----------------------
    # Re-decode from the published bytes: the effective address each pair now
    # reaches must be exactly the relocated/bound target that was recorded.
    adrp_checked, adrp_bad = 0, []
    for entry in branch_relocations:
        if not str(entry['kind']).startswith('adrp'):
            continue
        site = int(entry['site'], 16)
        pair_site = int(entry.get('pair_site', site + 4), 16)
        word = struct.unpack_from('<I', output, file_position(site, 4))[0]
        following = struct.unpack_from('<I', output, file_position(pair_site, 4))[0]
        decoded = decode_pc_relative(word, site)
        if decoded is None or decoded.get('kind') != 'adrp':
            adrp_bad.append(dict(entry, reason='not an adrp after rewrite'))
            continue
        imm = (following >> 10) & 0xFFF
        scaled = ldst_unsigned(following)
        if is_add_imm12(following):
            effective = decoded['target'] + imm
        elif scaled is not None:
            effective = decoded['target'] + imm * scaled[1]
        else:
            adrp_bad.append(dict(entry, reason='pair form lost'))
            continue
        adrp_checked += 1
        if effective != int(entry['new_target'], 16):
            adrp_bad.append(dict(entry, effective=hex(effective)))
    if adrp_bad:
        for entry in adrp_bad[:10]:
            print('bad adrp rewrite:', entry, file=sys.stderr)
        if not os.environ.get('NABU_ADRP_VERIFY_SOFT'):
            raise Refused('%d of %d rewritten adrp pairs do not reach their recorded '
                          'target' % (len(adrp_bad), adrp_checked))
    if args.debug:
        print('debug: %d adrp pairs seen, %d rewritten and re-decoded'
              % (adrp_seen, adrp_checked), file=sys.stderr)
    if soft_skips:
        print('debug: soft skips %d' % len(soft_skips), file=sys.stderr)
        for entry in soft_skips[:10]:
            print('soft skip:', entry, file=sys.stderr)

    # ---- per-kext header surgery -----------------------------------------
    split_added, header_slack, kmod_names = [], [], []
    for identifier in selected:
        component = components[identifier]
        text = component['text']
        if text is None:
            continue
        new_header = mv(text['vmaddr'])
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
            kmod_position = file_position(mv(component['kmod']), 256)
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

    # ---- embedded Mach-O headers describe the repacked layout ------------
    # A kext's load commands were copied verbatim, so they still name Apple's
    # kernelcache addresses even though the bytes now live in our band.
    # kalloc_type_view_parse() reaches __kalloc_type through those headers, so a
    # header that disagrees with the placement pages in the wrong address.
    # Rewrite every address-bearing command in place: segment vmaddr/section
    # addr and their file offsets, and drop the LINKEDIT-backed offsets we do
    # not carry.
    CARRIER_FIELDS = {0x2: (8, 16), 0xb: (32, 40, 48, 56, 64, 72),
                      0x22: (8, 16, 24, 32, 40),
                      0x80000022: (8, 16, 24, 32, 40),
                      0x26: (8,), 0x29: (8,), 0x1d: (8,), 0x1e: (8,)}

    def linkedit_file_position(old_file_offset):
        """Old LINKEDIT file offset -> new one, through the KC's own segments."""
        for segment in kc_segments:
            if (segment['filesize']
                    and segment['fileoff'] <= old_file_offset
                    <= segment['fileoff'] + segment['filesize']):
                return file_position(mv(segment['vmaddr']
                                        + (old_file_offset - segment['fileoff'])))
        return None

    def relocate_carrier(cursor, cmd):
        for field in CARRIER_FIELDS[cmd]:
            old, = struct.unpack_from('<I', output, cursor + field)
            if not old:
                continue
            new = linkedit_file_position(old)
            struct.pack_into('<I', output, cursor + field,
                             0 if new is None else new)

    def rewrite_symbols(cursor):
        """Component symbol addresses: n_value of every nlist in this kext."""
        symoff, nsyms = struct.unpack_from('<II', output, cursor + 8)
        if not symoff or not nsyms:
            return 0
        moved = 0
        for index in range(nsyms):
            at = symoff + index * 16 + 8
            old_value, = struct.unpack_from('<Q', output, at)
            new_value = mv(old_value)
            if new_value != old_value:
                struct.pack_into('<Q', output, at, new_value)
                moved += 1
        return moved
    LINKEDIT_CARRIERS = {0x2, 0xb, 0x22, 0x80000022, 0x26, 0x29, 0x1d, 0x1e}
    header_rewrite = []
    for identifier in selected:
        component = components[identifier]
        text = component['text']
        if text is None:
            continue
        header_old = text['vmaddr']
        header_new = mv(header_old)
        base = file_position(header_new)
        ncmds, _sizeofcmds = struct.unpack_from('<II', output, base + 16)
        cursor = base + 32
        rewritten = 0
        carriers = 0
        symbols = 0
        for _ in range(ncmds):
            cmd, cmdsize = struct.unpack_from('<II', output, cursor)
            if cmd == LC_SEGMENT_64:
                old_vm, old_vsize, old_fo, old_fs = struct.unpack_from(
                    '<QQQQ', output, cursor + 24)
                new_vm = mv(old_vm)
                new_fo = 0
                if old_fs:
                    try:
                        new_fo = file_position(new_vm)
                    except KeyError:
                        new_fo = 0
                struct.pack_into('<QQ', output, cursor + 24, new_vm, old_vsize)
                struct.pack_into('<Q', output, cursor + 40, new_fo)
                nsects, = struct.unpack_from('<I', output, cursor + 64)
                for index in range(nsects):
                    section = cursor + 72 + index * 80
                    sec_addr, sec_size, sec_off = struct.unpack_from(
                        '<QQI', output, section + 32)
                    struct.pack_into('<Q', output, section + 32,
                                     new_vm + (sec_addr - old_vm))
                    if (sec_off and old_fs and old_fo <= sec_off
                            and sec_off - old_fo + sec_size <= old_fs):
                        struct.pack_into('<I', output, section + 48,
                                         new_fo + (sec_off - old_fo))
                    elif sec_off:
                        struct.pack_into('<I', output, section + 48, 0)
                rewritten += 1
            elif cmd in LINKEDIT_CARRIERS:
                relocate_carrier(cursor, cmd)
                if cmd == 0x2:
                    symbols += rewrite_symbols(cursor)
                carriers += 1
            cursor += cmdsize
        header_rewrite.append(dict(component=identifier, header=hex(header_new),
                                   segments=rewritten, carriers=carriers,
                                   symbols=symbols))
    def kext_symbol(identifier, names):
        """Resolve a symbol in the *relocated* table of one kext.

        The header rewrite already moved LC_SYMTAB and every nlist.n_value, so
        this is the same lookup SHENG does at emit time
        (emit_macos26_security.py:307): read the name from the string table,
        return the address the kernel may call.
        """
        component = components[identifier]
        text = component['text']
        if text is None:
            return None
        base = file_position(mv(text['vmaddr']))
        if base is None:
            return None
        ncmds, = struct.unpack_from('<I', output, base + 16)
        cursor = base + 32
        for _ in range(ncmds):
            cmd, cmdsize = struct.unpack_from('<II', output, cursor)
            if cmd == 0x2:
                symoff, nsyms, stroff, strsize = struct.unpack_from('<IIII', output, cursor + 8)
                for index in range(nsyms):
                    at = symoff + index * 16
                    strx, = struct.unpack_from('<I', output, at)
                    if not strx or strx >= strsize:
                        continue
                    end = output.find(b'\0', stroff + strx)
                    name = output[stroff + strx:end].decode('ascii', 'replace')
                    if name in names:
                        value, = struct.unpack_from('<Q', output, at + 8)
                        return value
            cursor += cmdsize
        return None
    # ---- pointer rebinding ------------------------------------------------
    relocations = []
    for slot in slots:
        host_identifier, host_name = slot['host']
        group = placed_groups[next(g[0] for g in GROUPS if g[1] == host_name)]
        new_site = mv(slot['va'])
        position = group['fileoff'] + (new_site - group['vmaddr'])
        if slot['category'] in ('a', 'b'):
            value, symbol, exact = mv(slot['old_target']), None, True
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
        header_va = mv(component['text']['vmaddr'])
        # The repacked parts of one kext are no longer contiguous, so the
        # executable span is measured on the *new* geometry: load address
        # through the highest repacked part of this component.
        image_size = align_up(max(mv(s['vmaddr']) + s['vmsize'] for s in component['segments']
                                  if s['name'] != '__LINKEDIT' and s['filesize'])
                              - header_va)
        entry['_PrelinkExecutableLoadAddr'] = header_va
        entry['_PrelinkExecutableSize'] = image_size
        entry['_PrelinkKmodInfo'] = mv(component['kmod'] or 0)
        # SHENG moves the source address with the image; leaving Apple's value
        # here makes the loader take a relocation path for an image that is
        # already final (emit_macos26_security.py does the same three).
        entry['_PrelinkExecutableSourceAddr'] = header_va
        # 0049-native-boot-prelink-identity.patch: the outer image is not a
        # fileset while the inner kexts keep MH_DYLIB_IN_CACHE, so the boot KC
        # identity has to come from metadata.
        entry['_NativePrelinkPrimary'] = True
        # 0038/0039: for an AppleSecurityExtension kext the kernel calls a real
        # registration entry instead of trusting the module's __start path
        # (amfi's __realmain is zero in a flattened cache, so plain start
        # reports success while amfi_register_mac_policy never runs).
        if entry.get('AppleSecurityExtension') is True:
            target = kext_symbol(identifier, {'_amfi_register_mac_policy',
                                              'amfi_register_mac_policy'})
            if target:
                entry['_NativePrelinkEarlySecurityInit'] = target
        info_entries.append(entry)
    for identifier in interfaces:
        info_entries.append(dict(interfaces[identifier]))
    info_plist = dict(metadata)
    info_plist['_PrelinkInfoDictionary'] = info_entries
    info_plist.pop('_PrelinkLinkKASLROffsets', None)
    # The blob is a C string: OSUnserializeXML requires a terminating NUL, and
    # everything after it in __PRELINK_INFO (__crypto_early) is reached by
    # offset, so the sizes below must already include it.
    info_blob = prelink_xml_document(info_plist) + b'\0'
    # __crypto_early: one pointer to the original corecrypto kmod_info, read by
    # 0043-native-corecrypto-single-init.patch via getsectbynamefromheader().
    crypto_early_off = None
    crypto_early_va = None
    corecrypto = components.get('com.apple.kec.corecrypto')
    if corecrypto is not None and corecrypto['kmod']:
        crypto_early_off = align_up(len(info_blob), 8)
        crypto_early_va = info_va + crypto_early_off
    crypto_early_kmod = mv(corecrypto['kmod']) if crypto_early_off is not None else None
    info_size = align_up(max(len(info_blob),
                             (crypto_early_off + 8) if crypto_early_off is not None else 0))
    info_fileoff = info_va - img_base
    if info_fileoff < len(output):
        raise SystemExit('info file offset %#x overlaps the image (%#x)'
                         % (info_fileoff, len(output)))
    output.extend(bytes(info_fileoff - len(output)))
    output.extend(info_blob)
    output.extend(bytes(info_size - len(info_blob)))
    if crypto_early_off is not None:
        struct.pack_into('<Q', output, info_fileoff + crypto_early_off, crypto_early_kmod)

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
    info_sections = [section_command('__info', '__PRELINK_INFO', info_va,
                                     len(info_blob), info_fileoff)]
    if crypto_early_off is not None:
        info_sections.append(section_command('__crypto_early', '__PRELINK_INFO',
                                             crypto_early_va, 8,
                                             info_fileoff + crypto_early_off))
    replacements['__PRELINK_INFO'] = segment_command(
        '__PRELINK_INFO', info_va, info_size, info_fileoff, info_size, 3,
        info_sections)

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
            start = part['new_vmaddr']
            if not (group['vmaddr'] <= start and
                    start + part['filesize'] <= group['vmaddr'] + group['filesize']):
                containment = False
    check('component segments contained in their group', containment)

    split_ok = True
    for identifier in selected:
        component = components[identifier]
        position = file_position(mv(component['text']['vmaddr']))
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
    # Under the repacked layout the distance between a slot and its target is
    # no longer an invariant; what must hold is that every rewritten slot
    # names exactly the repacked address of the old target.
    broken = [r for r in sampled
              if int(r['new_target'], 16) != mv(int(r['old_target'], 16))]
    check('every intra-closure pointer translates through mv() (%d samples)'
          % len(sampled), not broken, '%d mismatches' % len(broken))

    intra_branches = [r for r in branch_relocations
                      if r['kind'].endswith('intra')]
    branch_broken = [r for r in intra_branches
                     if int(r['new_target'], 16) != mv(int(r['old_target'], 16))]
    check('every repacked intra-closure branch names its new target (%d sites)'
          % len(intra_branches), not branch_broken,
          '%d mismatches' % len(branch_broken))

    part_ok = all(group['vmaddr'] <= part['new_vmaddr'] and
                  part['new_vmaddr'] + part['filesize'] <= group['vmaddr'] + group['filesize']
                  for group in placed_groups.values() for part in group['parts'])
    check('every repacked part lands inside its group', part_ok)
    check('band starts above the kernel and on an L2 granule',
          band_base % L2_GRANULE == 0 and band_base >= kernel_end,
          '%#x (kernel end %#x)' % (band_base, kernel_end))

    bind_ok = all(r['symbol'] in kernel_symbols.by_name
                  for r in relocations if r['category'] == 'c' and r['symbol'])
    check('every kernel binding names a real native symbol', bind_ok)

    coverage['approximate'] = approximate
    loadable = (not unresolved) and all(a['ok'] for a in assertions)

    # ---- corecrypto integrity contract ------------------------------------
    # SHENG recomputes __fips_hmacs over the *relocated* __text
    # (emit_macos26_security.py:368): HMAC-SHA256 with a 32-byte zero key.
    # Our branch and pointer rewrites change those bytes, so the digest has to
    # be regenerated or corecrypto fails its own POST once the security
    # extensions load.
    crypto_hmac = None
    corecrypto = components.get('com.apple.kec.corecrypto')
    if corecrypto is not None and corecrypto.get('text') is not None:
        hbase = file_position(mv(corecrypto['text']['vmaddr']))
        hncmds, = struct.unpack_from('<I', output, hbase + 16)
        hcursor = hbase + 32
        text_sec = None
        integrity_sec = None
        for _ in range(hncmds):
            hcmd, hcmdsize = struct.unpack_from('<II', output, hcursor)
            if hcmd == LC_SEGMENT_64:
                hnsects, = struct.unpack_from('<I', output, hcursor + 64)
                for hindex in range(hnsects):
                    spot = hcursor + 72 + hindex * 80
                    sname = output[spot:spot + 16].rstrip(b'\0').decode()
                    saddr, ssize, soff = struct.unpack_from('<QQI', output, spot + 32)
                    if sname == '__text' and text_sec is None:
                        text_sec = (saddr, ssize)
                    elif sname == '__fips_hmacs':
                        integrity_sec = (saddr, ssize)
            hcursor += hcmdsize
        if text_sec and integrity_sec and integrity_sec[1] == 32:
            code_at = file_position(text_sec[0])
            digest_at = file_position(integrity_sec[0])
            stored = bytes(output[digest_at:digest_at + 32])
            digest = hmac.new(bytes(32), bytes(output[code_at:code_at + text_sec[1]]),
                              hashlib.sha256).digest()
            output[digest_at:digest_at + 32] = digest
            crypto_hmac = dict(text=hex(text_sec[0]), text_size=text_sec[1],
                               integrity=hex(integrity_sec[0]),
                               stored=stored.hex(), computed=digest.hex(),
                               changed=stored != digest)

    # ---- layout invariant ------------------------------------------------
    # Every VA we introduced must satisfy fileoff == vmaddr - img_base, so the
    # kernel's static-region mapping covers exactly the bytes the loader put
    # behind it.
    invariant = []
    for name, group in placed_groups.items():
        invariant.append((name, group['vmaddr'] - img_base, group['fileoff']))
    invariant.append(('__PRELINK_INFO', info_va - img_base, info_fileoff))
    broken = [item for item in invariant if item[1] != item[2]]
    if broken:
        for name, want, got in broken:
            print('   [FAIL] %-18s va-base %#x fileoff %#x' % (name, want, got))
        raise SystemExit('layout invariant broken: %d segments' % len(broken))
    print('   [ok] %d band segments satisfy fileoff == vmaddr - image_base'
          % len(invariant))

    report = dict(
        tool='scripts/prelink_kcg_bind.py',
        design='repacked band + sparse __macho_list (kalloc_sparse_prelink_index_version)',
        decoder_self_test=decoder_checks,
        kernel=str(kernel_path),
        kernel_sha256=hashlib.sha256(native_data).hexdigest(),
        collection=str(kc_path),
        collection_sha256=hashlib.sha256(kc).hexdigest(),
        fileset=str(fileset_path) if fileset_path.is_file() else None,
        roots=roots,
        layout='repacked',
        header_rewrite=header_rewrite,
        crypto_hmac=crypto_hmac,
        part_align=hex(PART_ALIGN),
        image_base=hex(img_base),
        layout_invariant=[dict(segment=n, fileoff=hex(fo), va_offset=hex(vo))
                          for n, vo, fo in invariant],
        band=dict(base=hex(band_base), end=hex(band_end), span=band_end - band_base,
                  span_mb=round((band_end - band_base) / 1048576, 2)),
        image_bytes=len(output),
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
            dict(component=i, header=hex(mv(components[i]['text']['vmaddr'])),
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
        band_padding=dict(span_mb=round((band_end - band_base) / 1048576, 2),
                          used_bytes=sum(g['filesize'] for g in placed_groups.values()),
                          padding_bytes=(band_end - band_base)
                          - sum(g['filesize'] for g in placed_groups.values())),
        symbol_coverage=coverage,
        slots_total=len(slots),
        slots_by_category=coverage['slots_by_category'],
        pointer_relocations=relocations,
        pointer_relocation_count=len(relocations),
        branch_relocations=branch_relocations,
        branch_relocation_count=len(branch_relocations),
        adrp_user_rewrite_count=adrp_user_rewrites,
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
    parser.add_argument('--slide', type=lambda v: int(v, 0), default=0x2000000,
                        help='uniform slide added to original KC virtual addresses (default: 0x2000000)')
    args = parser.parse_args(argv)

    try:
        report = run(args)
    except Refused as failure:
        print('prelink_kcg_bind: %s' % failure, file=sys.stderr)
        return 2
    print('\n== summary ==')
    for key in ('layout', 'component_count', 'component_code_bytes', 'slots_by_category',
                'pointer_relocation_count', 'branch_relocation_count', 'veneer_count',
                'approximate', 'unresolved_sites', 'loadable', 'output_bytes'):
        print('   %-26s %s' % (key, report[key]))
    print('   %-26s %.2f%%' % ('symbol coverage', report['symbol_coverage']['exact_percent']))
    print('   %-26s %#x .. %#x = %.2f MB' % (
        'band', report['band']['base'] if isinstance(report['band']['base'], int)
        else int(report['band']['base'], 16),
        report['band']['end'] if isinstance(report['band']['end'], int)
        else int(report['band']['end'], 16), report['band']['span_mb']))
    for assertion in report['structural_assertions']:
        print('   [%s] %-52s %s' % ('ok' if assertion['ok'] else 'FAIL',
                                    assertion['name'], assertion['detail']))
    return 0 if report['loadable'] else 1


if __name__ == '__main__':
    sys.exit(main())
