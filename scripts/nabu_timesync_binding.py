#!/usr/bin/env python3
"""Exact original Ethernet inherited-slot binding for the 25F71 companion.

This API mirrors native_storage_module.framebuffer_private_slots. It does not
permit private methods as ordinary function-call imports: every relocation using
one must pass validate_ethernet_slot, which checks site, target and PAC contract.
"""
import struct
from inspect_macos26_security import macho, exports, chains, backed_offset

FAMILY = 'com.apple.iokit.IONetworkingFamily'
BASE_TABLE = '__ZTV20IOEthernetController'
OWN_TABLE = '__ZTV20NabuTimeSyncIdentity'


def ethernet_private_slots(original, kc, own, base, slide):
    """Return (name -> allowed exact slot bindings, address -> method names).

    Inputs and result shape match framebuffer_private_slots; merge both maps
    into the companion prelink's private slot and additional_names maps. The
    inherited-vtable verifier must run with base_class='IOEthernetController',
    own_class='NabuTimeSyncIdentity', additional_names=returned_names.
    """
    if OWN_TABLE not in own:
        raise ValueError('TimeSync companion has no NabuTimeSyncIdentity vtable')
    family = macho(original, kc['entries'][FAMILY][1])
    command = next(p for c, p, size in family['commands'] if c == 2 and size == 24)
    off, count, strings, size = struct.unpack_from('<4I', original, command + 8)
    if off + count * 16 > len(original) or strings + size > len(original):
        raise ValueError('Ethernet private symbols exceed the paired collection')
    names = {}
    for index in range(count):
        name, kind, section, _, address = struct.unpack_from('<IBBHQ', original, off + index * 16)
        if kind & 0xe0 or kind & 0x0e != 0x0e or not section:
            continue
        end = original.find(b'\0', strings + name, strings + size)
        if name >= size or end < 0:
            raise ValueError('invalid Ethernet private method name')
        symbol = original[strings + name:end].decode()
        if not symbol.startswith(('__ZN20IOEthernetController', '__ZNK20IOEthernetController',
                                  '__ZN19IONetworkController', '__ZNK19IONetworkController')):
            continue
        if not any(s['initprot'] & 4 and s['va'] <= address < s['va'] + s['filesize']
                   for s in family['segments']):
            continue
        names.setdefault(address, []).append(symbol)
    public = exports(original, family)
    table = next(a for a, ns in public.items() if BASE_TABLE in ns)
    end = min(a for a, ns in public.items() if a > table and any(n.startswith('__ZTV') for n in ns))
    # Exact 25F71 class allocation ABI, not the 27.3 reference's headers.
    ctor = next(a for a, ns in public.items() if '__ZN20IOEthernetController9MetaClassC1Ev' in ns)
    ctor_off = backed_offset(family['segments'], ctor, 64, len(original))
    if struct.pack('<I', 0x52802303) not in original[ctor_off:ctor_off+64]:
        raise ValueError('Ethernet class does not have reviewed 25F71 size 0x118')
    pointers = {p['site']: p for p in chains(original, kc) if table + 16 <= p['site'] < end}
    if len(pointers) != 400 or set(pointers) != set(range(table + 16, table + 16 + 400 * 8, 8)):
        raise ValueError('Ethernet inherited vtable does not have reviewed 25F71 400 slots')
    own_table = base + own[OWN_TABLE]
    bindings = {}
    for site, pointer in sorted(pointers.items()):
        index = (site - table - 16) // 8
        for name in names.get(pointer['target'], []):
            bindings.setdefault(name, []).append(dict(slot=index, original=pointer,
                new_site=own_table + 16 + index * 8, new_target=pointer['target'] + slide))
    if not bindings:
        raise ValueError('Ethernet private inherited methods missing')
    return bindings, names


def validate_ethernet_slot(item, allowed):
    matches = [slot for slot in allowed
               if all(item[key] == slot[key] for key in ('new_site', 'new_target'))
               and all(item[key] == slot['original'][key]
                       for key in ('authenticated', 'key', 'diversity', 'address_diversity'))]
    if len(matches) != 1:
        raise ValueError('private Ethernet method may only bind its exact inherited vtable slot and PAC contract')
    return matches[0]
