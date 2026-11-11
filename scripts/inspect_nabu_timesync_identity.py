#!/usr/bin/env python3
"""Read the paired BootKC's TimeSync identity ABI; never synthesizes a clock.

"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from inspect_macos26_security import macho, exports, chains, backed_offset

NETWORK = 'com.apple.iokit.IONetworkingFamily'
TIMESYNC = 'com.apple.iokit.IOTimeSyncFamily'
TABLE = '__ZTV20IOEthernetController'

def inspect(path):
    data = path.read_bytes()
    kc = macho(data)
    needed = (NETWORK, TIMESYNC, 'com.apple.plugin.IOgPTPPlugin')
    missing = [key for key in needed if key not in kc['entries']]
    if missing:
        raise ValueError(f'paired BootKC lacks TimeSync dependencies: {missing}')
    net = macho(data, kc['entries'][NETWORK][1])
    names = exports(data, net)
    by_name = {name: address for address, values in names.items() for name in values}
    table = by_name[TABLE]
    next_table = min(a for a, values in names.items() if a > table and any(n.startswith('__ZTV') for n in values))
    fixups = {p['site']: p for p in chains(data, kc) if table <= p['site'] < next_table}
    slots = []
    for site, pointer in sorted(fixups.items()):
        if site < table + 16:
            continue
        slots.append(dict(slot=(site-table-16)//8, names=names.get(pointer['target'], []),
                          **pointer))
    ctor = by_name['__ZN20IOEthernetController9MetaClassC1Ev']
    offset = backed_offset(net['segments'], ctor, 64, len(data))
    # mov w3,#0x118, followed by the OSMetaClass constructor call.
    if struct.pack('<I', 0x52802303) not in data[offset:offset+64]:
        raise ValueError('IOEthernetController metaclass no longer has the reviewed 0x118 byte layout')
    timesync = macho(data, kc['entries'][TIMESYNC][1])
    ts_names = {n:a for a, ns in exports(data, timesync).items() for n in ns}
    matched = '__ZN22IOTimeSyncClockManager16interfaceMatchedEP9IOServiceP10IONotifier'
    if matched not in ts_names:
        raise ValueError('TimeSync interfaceMatched entry absent')
    result = dict(kernelcache_sha256=hashlib.sha256(data).hexdigest(), roots=list(needed),
                  controller_size=0x118, controller_vtable=slots, interface_matched=ts_names[matched])
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernelcache', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = inspect(args.kernelcache)
    text = json.dumps(report, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        print(text, end='')

if __name__ == '__main__':
    main()
