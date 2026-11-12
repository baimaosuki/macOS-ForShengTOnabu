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


def native_bringup_policy(data, kc, timesync):
    """Qualify the real 25F71 policy, including both sides of its wait branch.

    This is deliberately an exact-build admission check, not a string search:
    another OS may have the same option with different behavior. No bytes are
    changed, no clock identifier is invented and no resource is published here.
    """
    names = {n: a for a, ns in exports(data, timesync).items() for n in ns}
    kernel = macho(data, kc['entries']['com.apple.kernel'][1])
    kernel_names = {n: a for a, ns in exports(data, kernel).items() for n in ns}
    init = names['__ZN22IOTimeSyncClockManager4initEP12OSDictionary']
    delayed = names['__ZN22IOTimeSyncClockManager20delayedClockIDLookupEPv']
    find = names['__ZN22IOTimeSyncClockManager23findTimeSyncTimeClockIDEv']
    update = names['__ZN22IOTimeSyncClockManager24updateTranslationServiceEv']

    def read(address, size):
        at = backed_offset(timesync['segments'], address, size, len(data))
        return data[at:at + size]

    def exact(address, hex_bytes):
        expected = bytes.fromhex(hex_bytes)
        if read(address, len(expected)) != expected:
            raise ValueError('TimeSync native bringup policy differs from reviewed 25F71 instructions')

    def call(address, target):
        word, = struct.unpack('<I', read(address, 4))
        imm = word & 0x03ffffff
        if imm & 0x02000000:
            imm -= 0x04000000
        if word >> 26 != 0b100101 or address + imm * 4 != target:
            raise ValueError('TimeSync native bringup policy call target changed')

    def literal(address):
        adrp, add = struct.unpack('<II', read(address, 8))
        if adrp & 0x9f00001f != 0x90000000 or add & 0xffc003ff != 0x91000000:
            raise ValueError('TimeSync native bringup policy literal changed')
        pages = ((adrp >> 5 & 0x7ffff) << 2) | (adrp >> 29 & 3)
        if pages & 0x100000:
            pages -= 0x200000
        target = (address & ~0xfff) + pages * 4096 + (add >> 10 & 0xfff)
        at = backed_offset(timesync['segments'], target, 1, len(data))
        return data[at:data.index(b'\0', at)].decode()

    # PE_parse_boot_argn("-timesync_bringup", NULL, 0), then bool -> +0x128.
    if literal(init + 0x138) != '-timesync_bringup':
        raise ValueError('TimeSync native bringup option changed')
    exact(init + 0x140, '010080d202008052')
    call(init + 0x148, kernel_names['_PE_parse_boot_argn'])
    exact(init + 0x14c, '1f000071e8079f1a68a20439')
    # The flag selects IOSleep(5000); absent flag selects waitQuiet(UINT64_MAX).
    exact(delayed + 0x40, '68a244398800003600718252')
    call(delayed + 0x4c, kernel_names['_IOSleep'])
    exact(delayed + 0x50, '04000014e00314aa01008092')
    call(delayed + 0x5c, kernel_names['__ZN9IOService9waitQuietEy'])
    # Both paths rejoin the actual lookup and retain its failure test. Only
    # after successful lookup does native code update/publish its real clock.
    exact(delayed + 0x90, 'e00313aa')
    call(delayed + 0x94, find)
    exact(delayed + 0x98, '68ee423988040036')
    exact(delayed + 0xc8, 'e00313aa')
    call(delayed + 0xcc, update)
    if literal(delayed + 0xd0) != 'IOTimeSyncClockManager':
        raise ValueError('TimeSync native clock resource changed')
    exact(delayed + 0xd8, '010080d2')
    call(delayed + 0xdc, kernel_names['__ZN9IOService15publishResourceEPKcP8OSObject'])
    return dict(option='-timesync_bringup', presence_flag=True,
                default_wait='platform->waitQuiet(UINT64_MAX)',
                bringup_wait_ms=5000, lookup='original MAC-derived clock ID',
                resource='IOTimeSyncClockManager', bypasses_lookup=False,
                init=init, delayed_lookup=delayed, find_clock=find,
                update_translation=update,
                qualified_policy_sha256=hashlib.sha256(read(delayed, 0x19c)).hexdigest())

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
                  controller_size=0x118, controller_vtable=slots, interface_matched=ts_names[matched],
                  native_bringup_policy=native_bringup_policy(data, kc, timesync))
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
