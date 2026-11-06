#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Mirror DarwinBoot's ParseKernel() so the kernel is accepted offline.

DarwinBoot.c:119 rejects an image long before it ever reaches XNU. Every one of
those conditions is re-implemented here, so a build can be qualified on the host
instead of on the tablet:

  * magic 0xfeedfacf, cputype 0x100000c (CPU_TYPE_ARM64), filetype 2
  * cpusubtype byte 0 (arm64) or 2 (arm64e, which additionally needs EL1 PAC)
  * sizeofcmds <= Size - 32, every load command 8-aligned and in bounds
  * LC_SEGMENT_64: vmsize != 0, vmsize <= WINDOW_SIZE, fileoff/filesize in
    bounds, filesize <= vmsize, no two segments overlap
  * exactly one LC_UNIXTHREAD of cmdsize 288, flavor 6, count 68
  * the entry point must fall inside an executable segment
  * the whole span must fit inside the DarwinBoot window
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path

LC_SEGMENT_64 = 0x19
LC_UNIXTHREAD = 0x5
WINDOW_SIZE = 0x10000000          # DarwinBoot.c:20 SIZE_256MB
MAX_SEGMENTS = 64                 # DarwinBoot.c:23
MAX_IMAGE_BYTES = 128 * 1024 * 1024


def verify(data: bytes, window: int = WINDOW_SIZE, arm64e_pac: bool = False) -> dict:
    result = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                  failures=[], segments=[], window=hex(window))
    if len(data) < 32:
        result['failures'].append('image shorter than a Mach-O header')
        return result
    magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds = struct.unpack_from('<IiiIII', data, 0)
    result.update(magic=hex(magic), cputype=hex(cputype & 0xFFFFFFFF),
                  cpusubtype=hex(cpusubtype & 0xFFFFFFFF), filetype=filetype, ncmds=ncmds)
    if magic != 0xFEEDFACF:
        result['failures'].append('magic is not 0xfeedfacf')
    if cputype & 0xFFFFFFFF != 0x0100000C:
        result['failures'].append('cputype is not CPU_TYPE_ARM64')
    subtype = cpusubtype & 0xFF
    if subtype not in (0, 2):
        result['failures'].append(f'cpusubtype low byte {subtype} is neither arm64 (0) nor arm64e (2)')
    if subtype == 2 and not arm64e_pac:
        result['failures'].append('arm64e image requires EL1 PAC support on this CPU')
    if filetype != 2:
        result['failures'].append(f'filetype {filetype} is not MH_EXECUTE')
    if sizeofcmds > len(data) - 32:
        result['failures'].append('sizeofcmds overruns the file')
        return result

    end = 32 + sizeofcmds
    offset = 32
    entry = None
    thread_commands = 0
    for _ in range(ncmds):
        if end - offset < 8:
            result['failures'].append('truncated load command')
            return result
        command, length = struct.unpack_from('<II', data, offset)
        if length < 8 or length & 7 or length > end - offset:
            result['failures'].append(f'invalid load command length {length} at {offset}')
            return result
        if command == LC_SEGMENT_64:
            if length < 72:
                result['failures'].append('LC_SEGMENT_64 shorter than 72 bytes')
                return result
            name, vmaddr, vmsize, fileoff, filesize, maxprot, initprot, nsects, flags = \
                struct.unpack_from('<16s4Q4I', data, offset + 8)
            if len(result['segments']) >= MAX_SEGMENTS:
                result['failures'].append(f'more than {MAX_SEGMENTS} loadable segments')
            if vmsize:
                segment = dict(name=name.rstrip(b'\0').decode(errors='replace'),
                               vmaddr=vmaddr, vmsize=vmsize, fileoff=fileoff,
                               filesize=filesize, initprot=initprot)
                if vmsize > window:
                    result['failures'].append(f'segment {segment["name"]} is larger than the window')
                if filesize > vmsize:
                    result['failures'].append(f'segment {segment["name"]} filesize > vmsize')
                if fileoff > len(data) or filesize > len(data) - fileoff:
                    result['failures'].append(f'segment {segment["name"]} file range is out of bounds')
                result['segments'].append(segment)
        elif command == LC_UNIXTHREAD:
            thread_commands += 1
            if length != 288:
                result['failures'].append(f'LC_UNIXTHREAD cmdsize {length} != 288')
            elif (struct.unpack_from('<I', data, offset + 8)[0] != 6
                  or struct.unpack_from('<I', data, offset + 12)[0] != 68):
                result['failures'].append('LC_UNIXTHREAD flavor/count are not (6, 68)')
            else:
                entry, = struct.unpack_from('<Q', data, offset + 272)
        offset += length
    if offset != end:
        result['failures'].append(f'load commands end at {offset}, header claims {end}')
    if thread_commands != 1:
        result['failures'].append(f'expected exactly one LC_UNIXTHREAD, found {thread_commands}')
    if not result['segments']:
        result['failures'].append('no loadable segments')

    if entry is None:
        result['failures'].append('no entry point')
        return result
    result['entry'] = hex(entry)
    executable = [s for s in result['segments'] if s['initprot'] & 4]
    host = next((s for s in executable
                 if s['vmaddr'] <= entry < s['vmaddr'] + s['filesize']), None)
    if host is None:
        result['failures'].append('entry point is not inside an executable segment')
    else:
        result['entry_segment'] = host['name']

    for index, segment in enumerate(result['segments']):
        for other in result['segments'][:index]:
            if (segment['vmaddr'] < other['vmaddr'] + other['vmsize']
                    and other['vmaddr'] < segment['vmaddr'] + segment['vmsize']):
                result['failures'].append(
                    f'segments {other["name"]} and {segment["name"]} overlap')
    base = min(s['vmaddr'] for s in result['segments'])
    stop = max(s['vmaddr'] + s['vmsize'] for s in result['segments'])
    result['base'] = hex(base)
    result['span'] = hex(stop - base)
    if stop - base > window:
        result['failures'].append('kernel span exceeds the DarwinBoot window')
    if len(data) > MAX_IMAGE_BYTES:
        result['failures'].append('image exceeds the 128 MB read limit')
    result['accepted'] = not result['failures']
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', required=True, type=Path)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--window', type=lambda v: int(v, 0), default=WINDOW_SIZE)
    parser.add_argument('--arm64e-pac', action='store_true',
                        help='accept an arm64e subtype (CPU advertises EL1 PAC)')
    args = parser.parse_args()
    report = verify(args.kernel.read_bytes(), args.window, args.arm64e_pac)
    report['kernel'] = str(args.kernel)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    for segment in report['segments']:
        print('  %-16s va %#018x size %#010x fileoff %#010x prot %d'
              % (segment['name'], segment['vmaddr'], segment['vmsize'],
                 segment['fileoff'], segment['initprot']))
    print('  entry %s in %s, base %s, span %s'
          % (report.get('entry'), report.get('entry_segment'),
             report.get('base'), report.get('span')))
    print('  accepted = %s' % report['accepted'])
    for failure in report['failures']:
        print('  FAIL %s' % failure)


if __name__ == '__main__':
    main()

