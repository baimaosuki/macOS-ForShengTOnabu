#!/usr/bin/env python3
"""Map NABU-WS PCs/frames using their captured slide and the paired cache map."""
import argparse
import bisect
import json
from pathlib import Path
import re

from native_macho import symbols


def read_map(path):
    result, image = [], None
    for line in path.read_text().splitlines():
        if line.startswith('/'):
            image = line.strip()
        elif image:
            match = re.match(r'\s+(\S+) 0x([0-9a-fA-F]+) -> 0x([0-9a-fA-F]+)', line)
            if match and match[1].startswith('__TEXT'):
                result.append((int(match[2], 16), int(match[3], 16), image))
    return sorted(result)


def analyze(text, cache_map, native=None):
    images = read_map(cache_map)
    starts = [row[0] for row in images]
    contexts, records = {}, []
    kernel_symbols = sorted((address, name) for name, address in symbols(native).items()) if native else []
    kernel_starts = [row[0] for row in kernel_symbols]

    def locate(raw, slide):
        address = int(raw, 16) & 0x00007fffffffffff
        result = {'runtime': hex(address)}
        if slide == 0xffffffffffffffff:
            result['unresolved'] = 'shared-cache slide unavailable'
            return result
        address -= slide
        result['unslid_candidate'] = hex(address)
        index = bisect.bisect_right(starts, address) - 1
        if index >= 0 and images[index][0] <= address < images[index][1]:
            start, _, image = images[index]
            result.update(image=image, text_offset=hex(address - start))
        else:
            result['unresolved'] = 'outside cache text; may belong to executable or another mapping'
        return result

    for number, line in enumerate(text.splitlines(), 1):
        if 'NABU-WS ' not in line:
            continue
        fields = dict(re.findall(r'(\w+)=([^\s]+)', line))
        pid, sequence = fields.get('pid'), fields.get('seq')
        key = (pid, sequence)
        if 'pc' in fields and 'slide' in fields:
            slide = int(fields['slide'], 16)
            contexts[key] = slide
            records.append(dict(line=number, pid=pid, sequence=sequence, slide=hex(slide),
                                pc=locate(fields['pc'], slide), lr=locate(fields['lr'], slide),
                                instruction=fields['insn'], fetch_error=fields['fetch']))
        elif 'frame' in fields and key in contexts:
            records.append(dict(line=number, pid=pid, sequence=sequence, frame=fields['frame'],
                                copy_error=fields['copy'], caller=locate(fields['caller'], contexts[key])))
        elif fields.get('event') == 'task-terminate':
            record = dict(line=number, **fields)
            if native:
                address = int(fields['kernel_caller'], 16)
                index = bisect.bisect_right(kernel_starts, address) - 1
                if index >= 0:
                    start, name = kernel_symbols[index]
                    record['nearest_native_symbol'] = name
                    record['symbol_offset'] = hex(address - start)
            records.append(record)
        elif 'event' in fields:
            records.append(dict(line=number, **fields))
    return records


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--cache-map', type=Path, required=True,
                        help='25F71 dyld_shared_cache_arm64e.map paired with the tested image')
    parser.add_argument('--native', type=Path, help='exact CI native kernel for kernel call-site symbols')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = analyze(args.log.read_text(errors='replace'), args.cache_map, args.native)
    result = json.dumps(report, indent=2) + '\n'
    if args.output:
        args.output.write_text(result)
    print(result, end='')
