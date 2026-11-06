#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Inventory a macOS boot kernel collection (fileset Mach-O) for prelinking.

macOS 12+ ships the kernel cache as a file set (filetype 12): one Mach-O whose
top-level load commands point at per-kext images through LC_FILESET_ENTRY, and
whose coalesced __PRELINK_* segments are empty. The prelink tooling in
UniXNU/kits/NativePrelink was written for the older layout (kexts packed inside
a parent __PRELINK_TEXT), so it rejects these files. This tool reports the
structure a fileset-aware prelink needs: entry identifiers, virtual addresses,
sizes, segment maps and fixup metadata.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path

MH_MAGIC_64 = 0xfeedfacf
LC_SEGMENT_64 = 0x19
LC_FILESET_ENTRY = 0x80000035
LC_DYLD_CHAINED_FIXUPS = 0x80000034


def load_commands(data, header_offset):
    magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds = struct.unpack_from(
        '<IiiIII', data, header_offset)
    if magic != MH_MAGIC_64:
        raise ValueError('not a 64-bit Mach-O at %#x' % header_offset)
    commands, cursor = [], header_offset + 32
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from('<II', data, cursor)
        if cmdsize < 8 or cursor + cmdsize > len(data):
            raise ValueError('invalid load command at %#x' % cursor)
        commands.append((cmd, cursor, cmdsize))
        cursor += cmdsize
    return dict(cputype=cputype, cpusubtype=cpusubtype, filetype=filetype,
                ncmds=ncmds, sizeofcmds=sizeofcmds, commands=commands)


def segments(data, commands):
    result = []
    for cmd, offset, cmdsize in commands:
        if cmd != LC_SEGMENT_64:
            continue
        name, vmaddr, vmsize, fileoff, filesize, maxprot, initprot, nsects, flags = struct.unpack_from(
            '<16s4Q4I', data, offset + 8)
        result.append(dict(name=name.rstrip(b'\x00').decode(), vmaddr=vmaddr, vmsize=vmsize,
                           fileoff=fileoff, filesize=filesize, initprot=initprot,
                           sections=nsects))
    return result


def fileset_entries(data, commands):
    result = []
    for cmd, offset, cmdsize in commands:
        if cmd != LC_FILESET_ENTRY:
            continue
        vmaddr, fileoff = struct.unpack_from('<QQ', data, offset + 8)
        entry_id_offset, reserved = struct.unpack_from('<II', data, offset + 24)
        end = data.index(b'\x00', offset + entry_id_offset)
        result.append(dict(entry_id=data[offset + entry_id_offset:end].decode(),
                           vmaddr=vmaddr, fileoff=fileoff, reserved=reserved))
    return result


def chained_fixup_header(data, commands, entry_fileoff):
    for cmd, offset, cmdsize in commands:
        if cmd != LC_DYLD_CHAINED_FIXUPS:
            continue
        dataoff, datasize = struct.unpack_from('<II', data, offset + 8)
        blob = entry_fileoff + dataoff
        if blob + 28 > len(data):
            return None
        (version, starts, imports, symbols, imports_count, imports_format,
         symbols_format) = struct.unpack_from('<7I', data, blob)
        return dict(version=version, imports_count=imports_count,
                    imports_format=imports_format, symbols_format=symbols_format)
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--limits', action='store_true',
                        help='also report the coalesced segments the prelink must fill')
    args = parser.parse_args()

    data = args.input.read_bytes()
    top = load_commands(data, 0)
    entries = fileset_entries(data, top['commands'])
    report = dict(input=str(args.input), bytes=len(data),
                  sha256=hashlib.sha256(data).hexdigest(),
                  filetype=top['filetype'], cpusubtype=hex(top['cpusubtype'] & 0xffffffff),
                  entry_count=len(entries), entries=[])
    for entry in entries:
        sub = load_commands(data, entry['fileoff'])
        segs = segments(data, sub['commands'])
        report['entries'].append(dict(
            entry_id=entry['entry_id'], vmaddr=entry['vmaddr'], fileoff=entry['fileoff'],
            filetype=sub['filetype'], cpusubtype=hex(sub['cpusubtype'] & 0xffffffff),
            chained_fixups=chained_fixup_header(data, sub['commands'], entry['fileoff']),
            segments=[dict(name=s['name'], vmaddr=s['vmaddr'], vmsize=s['vmsize'],
                           filesize=s['filesize']) for s in segs],
            text_bytes=sum(s['filesize'] for s in segs if s['name'] == '__TEXT')))
    if args.limits:
        report['coalesced'] = [dict(name=s['name'], vmaddr=s['vmaddr'], vmsize=s['vmsize'])
                               for s in segments(data, top['commands'])
                               if s['name'].startswith('__PRELINK') or s['name'].startswith('__PLK')]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    largest = sorted(report['entries'], key=lambda item: -item['text_bytes'])[:6]
    print(json.dumps(dict(entries=report['entry_count'],
                          largest=[dict(id=e['entry_id'], text=e['text_bytes']) for e in largest],
                          coalesced=report.get('coalesced')), indent=2))


if __name__ == '__main__':
    main()

