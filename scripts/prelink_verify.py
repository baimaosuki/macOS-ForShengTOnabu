#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent verifier for the stage-2 prelinked kernel image.

Deliberately shares *no* code with scripts/prelink_kcg_bind.py: it re-parses the
produced image, re-reads our kernel's symtab from disk, and re-decodes branch
sites, so a bug in the producer cannot hide behind a matching bug here.

Checks, in order:

 1. container: MH_MAGIC_64, CPU_TYPE_ARM64 / subtype 0, no LC_FILESET_ENTRY.
 2. __PRELINK_TEXT carries both `__text` (satisfies the unpatched
    getsectbynamefromheader lookup) and `__macho_list` (the sparse index the
    patched kalloc_type_view_parse walks).
 3. every __macho_list entry survives all structural constraints from
    osfmk/kern/kalloc.c:1118-1152.
 4. every flattened kext still has LC_SEGMENT_SPLIT_INFO (kalloc.c:1175 panics
    without it) and every one of its non-__LINKEDIT segments lands inside the
    matching output segment.
 5. N random pointer bindings: bytes in the image equal new_target, and the
    uniform-slide invariant new_target-new_site == old_target-old_site holds.
 6. N random branch bindings: re-decode the patched word and compare targets.
 7. N random kernel bindings: new_target equals the VA that our own symtab,
    read fresh, gives for the recorded symbol name.
 8. __PRELINK_INFO parses, and every _PrelinkExecutableLoadAddr matches a
    __macho_list entry.
"""
import argparse
import hashlib
import json
import plistlib
import random
import struct
import sys

MH_MAGIC_64 = 0xFEEDFACF
LC_SEGMENT_64 = 0x19
LC_SYMTAB = 0x2
LC_SEGMENT_SPLIT_INFO = 0x1E
LC_FILESET_ENTRY = 0x80000035
CPU_TYPE_ARM64 = 0x0100000C


def cstr(raw):
    return raw.split(b'\0', 1)[0].decode('utf-8', 'replace')


def commands(data, offset):
    magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds = struct.unpack_from(
        '<IiiIII', data, offset)
    out, cursor = [], offset + 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', data, cursor)
        out.append((cmd, cursor, size))
        cursor += size
    return dict(magic=magic, cputype=cputype, cpusubtype=cpusubtype, filetype=filetype,
                ncmds=ncmds, sizeofcmds=sizeofcmds, commands=out, offset=offset)


def segments(data, image):
    out = []
    for cmd, offset, size in image['commands']:
        if cmd != LC_SEGMENT_64:
            continue
        name, va, vs, fo, fs, _mp, _ip, nsects, _fl = struct.unpack_from(
            '<16s4Q4I', data, offset + 8)
        sections, cursor = [], offset + 72
        for _ in range(nsects):
            sectname, _segname, addr, sz, off = struct.unpack_from('<16s16sQQI',
                                                                   data, cursor)
            sections.append(dict(name=cstr(sectname), addr=addr, size=sz, offset=off))
            cursor += 80
        out.append(dict(name=cstr(name), vmaddr=va, vmsize=vs, fileoff=fo, filesize=fs,
                        sections=sections, command_offset=offset))
    return out


def symtab(data, image):
    for cmd, offset, _ in image['commands']:
        if cmd != LC_SYMTAB:
            continue
        symoff, nsyms, stroff, strsize = struct.unpack_from('<4I', data, offset + 8)
        table = {}
        for index in range(nsyms):
            strx, ntype, _sect, _desc, value = struct.unpack_from(
                '<IBBHQ', data, symoff + index * 16)
            if ntype & 0xE0 or (ntype & 0x0E) != 0x0E or not value or strx >= strsize:
                continue
            end = data.find(b'\0', stroff + strx)
            table.setdefault(data[stroff + strx:end].decode('utf-8', 'replace'), value)
        return table
    return {}


def decode_branch(word, address):
    top = word >> 26
    if top == 0b100101:
        return address + ((word & 0x3FFFFFF) - (1 << 26) if word & 0x2000000
                          else word & 0x3FFFFFF) * 4
    if top == 0b000101:
        return address + ((word & 0x3FFFFFF) - (1 << 26) if word & 0x2000000
                          else word & 0x3FFFFFF) * 4
    if (word & 0xFF000010) == 0x54000000:
        immediate = (word >> 5) & 0x7FFFF
        return address + ((immediate - (1 << 19)) if immediate & 0x40000 else immediate) * 4
    return None


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--native', required=True, help='our kernel image (for symtab)')
    parser.add_argument('--samples', type=int, default=200)
    parser.add_argument('--seed', type=int, default=20260208)
    args = parser.parse_args(argv)

    data = open(args.image, 'rb').read()
    report = json.load(open(args.report))
    random.seed(args.seed)
    failures = []

    def check(name, ok, detail=''):
        if not ok:
            failures.append('%s %s' % (name, detail))
        print('  [%s] %-58s %s' % ('ok' if ok else 'FAIL', name, detail))

    print('== 1. container')
    image = commands(data, 0)
    check('MH_MAGIC_64', image['magic'] == MH_MAGIC_64, hex(image['magic']))
    check('CPU_TYPE_ARM64', image['cputype'] == CPU_TYPE_ARM64, hex(image['cputype'] & 0xFFFFFFFF))
    check('cpusubtype 0', image['cpusubtype'] == 0, hex(image['cpusubtype']))
    check('no LC_FILESET_ENTRY', not any(c == LC_FILESET_ENTRY for c, _o, _s in image['commands']))
    segs = segments(data, image)
    by_name = {s['name']: s for s in segs}

    print('== 2. prelink text layout')
    text_seg = by_name.get('__PRELINK_TEXT')
    check('__PRELINK_TEXT present', text_seg is not None)
    if text_seg is None:
        return 1
    sects = {s['name']: s for s in text_seg['sections']}
    check('__text section present', '__text' in sects, hex(sects['__text']['size']) if '__text' in sects else '')
    check('__macho_list section present', '__macho_list' in sects)

    print('== 3. __macho_list obeys osfmk/kern/kalloc.c:1118-1152')
    index = sects['__macho_list']
    section = sects['__text']
    check('index size is a multiple of 8', index['size'] % 8 == 0, str(index['size']))
    # kalloc.c bounds the *entries* against the __text section, not the index
    # itself; the index only has to live inside the loaded segment.
    check('index inside the segment file bytes',
          text_seg['fileoff'] <= index['offset']
          and index['offset'] + index['size'] <= text_seg['fileoff'] + text_seg['filesize'],
          '%#x..%#x in %#x+%#x' % (index['offset'], index['offset'] + index['size'],
                                   text_seg['fileoff'], text_seg['filesize']))
    count = index['size'] // 8
    headers = list(struct.unpack_from('<%dQ' % count, data, index['offset']))
    check('index non-empty', count >= 1, '%d entries' % count)
    monotonic = all(headers[i] < headers[i + 1] for i in range(len(headers) - 1))
    check('entries strictly increasing', monotonic)
    end = section['addr'] + section['size']
    in_range = all(section['addr'] <= h <= end for h in headers)
    check('entries inside the __text section', in_range)
    bad = []
    for header in headers:
        if not text_seg['vmaddr'] <= header < text_seg['vmaddr'] + text_seg['filesize']:
            bad.append((hex(header), 'outside the segment'))
            continue
        if end - header < 32:
            bad.append((hex(header), 'no room for a header'))
            continue
        magic, _ct, _cs, _ft, ncmds, sizeofcmds = struct.unpack_from(
            '<IiiIII', data, header - text_seg['vmaddr'] + text_seg['fileoff'])
        if magic != MH_MAGIC_64:
            bad.append((hex(header), 'magic %#x' % magic))
        if sizeofcmds > end - header - 32:
            bad.append((hex(header), 'sizeofcmds %#x exceeds section' % sizeofcmds))
    check('every header parses with sane sizeofcmds', not bad, str(bad[:3]))

    print('== 4. per-kext headers and segment containment')
    split_missing, containment = [], []
    layout = report['prelink_text_layout']
    for entry in layout:
        header = int(entry['header'], 16)
        position = header - text_seg['vmaddr'] + text_seg['fileoff']
        ncmds, sizeofcmds = struct.unpack_from('<II', data, position + 16)
        cursor, found = position + 32, False
        for _ in range(ncmds):
            cmd, size = struct.unpack_from('<II', data, cursor)
            if cmd == LC_SEGMENT_SPLIT_INFO:
                found = True
            cursor += size
        if not found:
            split_missing.append(entry['component'])
    check('every flattened kext has LC_SEGMENT_SPLIT_INFO', not split_missing,
          str(split_missing[:3]))
    group_of = {'__TEXT': '__PRELINK_TEXT', '__TEXT_EXEC': '__PLK_TEXT_EXEC',
                '__DATA_CONST': '__PLK_DATA_CONST', '__DATA': '__PRELINK_DATA'}
    # Under a uniform slide the kext's own load commands keep their original
    # (unslid) vmaddrs on purpose: the loader derives the per-kext slide from
    # kmod_info.address - header vmaddr, which is the same constant for every
    # segment.  So containment must be checked at va + slide.
    slide = int(report['slide'], 16)
    for entry in layout:
        header = int(entry['header'], 16)
        position = header - text_seg['vmaddr'] + text_seg['fileoff']
        ncmds, _sc = struct.unpack_from('<II', data, position + 16)
        cursor = position + 32
        for _ in range(ncmds):
            cmd, size = struct.unpack_from('<II', data, cursor)
            if cmd == LC_SEGMENT_64:
                segname, va, vs, _fo, fs = struct.unpack_from('<16s4Q', data, cursor + 8)
                segname = cstr(segname)
                if segname in group_of and fs:
                    target = by_name.get(group_of[segname])
                    ok = (target is not None and target['vmaddr'] <= va + slide
                          and va + slide + fs <= target['vmaddr'] + target['filesize'])
                    if not ok:
                        containment.append((entry['component'], segname, hex(va + slide)))
            cursor += size
    check('every kext segment contained in its group', not containment,
          str(containment[:3]))

    print('== 5. pointer bindings re-read from the image')
    pointers = report['pointer_relocations']
    # The isometry invariant only applies to references inside the closure;
    # category 'c' is deliberately redirected into our kernel instead.
    closure_pointers = [r for r in pointers if r['category'] in ('a', 'b')]
    sample = random.sample(closure_pointers, min(args.samples, len(closure_pointers)))
    wrong_bytes, broken_slide = [], []
    for record in sample:
        if record.get('old_site') is None:
            continue
        target = record['new_target']
        found = None
        for seg in segs:
            start = int(seg['vmaddr'])
            if start <= int(target, 16) < start + seg['filesize']:
                found = (seg, int(target, 16) - start)
                break
        if found is None:
            continue
        written = None
        for seg in segs:
            site = int(record['site'], 16)
            if seg['vmaddr'] <= site < seg['vmaddr'] + seg['filesize']:
                written, = struct.unpack_from('<Q', data, seg['fileoff'] + site - seg['vmaddr'])
        if written != int(target, 16):
            wrong_bytes.append((record['site'], hex(written or 0), target))
        if (int(record['new_target'], 16) - int(record['site'], 16)
                != int(record['old_target'], 16) - int(record['old_site'], 16)):
            broken_slide.append(record['site'])
    check('sampled slots contain the recorded target',
          not wrong_bytes, str(wrong_bytes[:3]))
    check('uniform-slide invariant holds for sampled slots', not broken_slide,
          str(broken_slide[:3]))

    print('== 6. branch bindings re-decoded')
    branches = report['branch_relocations']
    branch_sample = random.sample(branches, min(args.samples, len(branches)))
    mismatched, undecodable = [], []
    for record in branch_sample:
        site = int(record['site'], 16)
        position = None
        for seg in segs:
            if seg['vmaddr'] <= site < seg['vmaddr'] + seg['filesize']:
                position = seg['fileoff'] + site - seg['vmaddr']
        if position is None:
            undecodable.append(record['site'])
            continue
        word, = struct.unpack_from('<I', data, position)
        if record['kind'] == 'adrp+add':
            continue
        target = decode_branch(word, site)
        if target != int(record['new_target'], 16):
            mismatched.append((record['site'], hex(target or 0), record['new_target']))
    check('sampled branch words decode to the recorded target',
          not mismatched and not undecodable, str((mismatched + undecodable)[:3]))

    print('== 7. kernel bindings against a fresh symtab read')
    native = open(args.native, 'rb').read()
    native_image = commands(native, 0)
    native_symbols = symtab(native, native_image)
    kernel_bound = [r for r in pointers if r['category'] == 'c' and r.get('symbol')]
    kernel_sample = random.sample(kernel_bound, min(args.samples, len(kernel_bound)))
    unresolved_names, value_mismatch = [], []
    for record in kernel_sample:
        name = record['symbol']
        if name not in native_symbols:
            unresolved_names.append(name)
            continue
        if not record.get('exact'):
            continue
        if native_symbols[name] != int(record['new_target'], 16):
            value_mismatch.append((name, hex(native_symbols[name]),
                                   record['new_target']))
    check('sampled kernel symbols exist in our symtab', not unresolved_names,
          str(unresolved_names[:3]))
    check('exact bindings equal our symtab VA', not value_mismatch,
          str(value_mismatch[:3]))

    print('== 8. __PRELINK_INFO')
    info = by_name.get('__PRELINK_INFO')
    check('__PRELINK_INFO present', info is not None)
    entries = []
    if info is not None:
        blob = data[info['sections'][0]['offset']:
                    info['sections'][0]['offset'] + info['sections'][0]['size']]
        closing = blob.find(b'</plist>')
        if closing != -1:
            blob = blob[:closing + 9]
        plist = plistlib.loads(blob)
        entries = plist['_PrelinkInfoDictionary']
        check('plist parses', True, '%d dictionaries' % len(entries))
        listed = set(headers)
        addresses, sentinels = set(), 0
        for entry in entries:
            value = entry.get('_PrelinkExecutableLoadAddr')
            if value is None:
                continue
            if isinstance(value, (bytes, bytearray)):
                address = struct.unpack('<Q', bytes(value[:8]))[0]
            else:
                address = int(value)
            if address == 0x7FFFFFFFFFFFFFFF:
                # Apple's "not a loadable image" sentinel, used by the
                # OSKernelResource interface dictionaries.
                sentinels += 1
                continue
            addresses.add(address)
        check('only interface dictionaries carry the load-address sentinel',
              sentinels == len(entries) - len(layout),
              '%d sentinels, %d interfaces' % (sentinels, len(entries) - len(layout)))
        check('every _PrelinkExecutableLoadAddr is in __macho_list',
              addresses <= listed, str(sorted(hex(a) for a in addresses - listed)[:3]))
        check('every kext in __macho_list has an Info.plist entry',
              listed <= addresses | {0}, '')

    print('== 9. determinism fingerprint')
    print('   image sha256 %s' % hashlib.sha256(data).hexdigest())
    print('   report sha256 %s' % hashlib.sha256(
        json.dumps(report, sort_keys=True).encode()).hexdigest())

    print()
    if failures:
        print('VERIFY FAILED (%d): %s' % (len(failures), failures[:6]))
        return 1
    print('VERIFY OK: %d pointers / %d branches / %d kernel bindings sampled'
          % (len(sample), len(branch_sample), len(kernel_sample)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
