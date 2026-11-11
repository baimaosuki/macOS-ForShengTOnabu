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


def companion_import_sites(data):
    """Return import-name -> bind sites in a linked ARM64 companion (formats 2/6).

    Every occurrence is checked, including GOT slots used by direct calls. A
    reserved SDK method is allowed only if all occurrences are inherited slots.
    """
    from native_macho import macho as own_macho
    image = own_macho(data)
    command = next(p for c, p, _ in image['commands'] if c == 0x80000034)
    offset, extent = struct.unpack_from('<II', data, command + 8)
    blob = data[offset:offset+extent]
    version, starts, imports, strings, count, fmt, symbols_fmt = struct.unpack_from('<7I', blob)
    if version or symbols_fmt or fmt not in (1, 2, 3):
        raise ValueError('unsupported Ethernet companion imports')
    stride = {1:4, 2:8, 3:16}[fmt]
    imported = []
    for i in range(count):
        word, = struct.unpack_from('<Q' if fmt == 3 else '<I', blob, imports+i*stride)
        at = strings + (word >> (32 if fmt == 3 else 9))
        end = blob.index(b'\0', at)
        imported.append(blob[at:end].decode())
    result = {name: [] for name in imported}
    segments, = struct.unpack_from('<I', blob, starts)
    if segments != len(image['segments']):
        raise ValueError('Ethernet companion chain segment count mismatch')
    for index, segment in enumerate(image['segments']):
        relative, = struct.unpack_from('<I', blob, starts+4+4*index)
        if not relative:
            continue
        p = starts+relative
        length, page_size, pointer_format, seg_offset, _, pages = struct.unpack_from('<IHHQIH', blob,p)
        if pointer_format not in (2, 6) or seg_offset != segment['va']:
            raise ValueError('reserved Ethernet adaptation requires reviewed ARM64 format-2/6 companion')
        for page in range(pages):
            first, = struct.unpack_from('<H', blob,p+22+2*page)
            if first == 0xffff:
                continue
            if first & 0x8000:
                raise ValueError('multi-start Ethernet companion chain')
            site = segment['va'] + page*page_size + first
            visited = set()
            while True:
                if site in visited or not segment['va']+page*page_size <= site < segment['va']+(page+1)*page_size:
                    raise ValueError('invalid Ethernet companion chain')
                visited.add(site)
                at = backed_offset(image['segments'],site,8,len(data))
                word, = struct.unpack_from('<Q',data,at)
                if word >> 63:
                    ordinal = word & 0xffffff
                    if ordinal >= count or (word >> 24) & 0xff:
                        raise ValueError('Ethernet inherited bind has invalid ordinal/addend')
                    result[imported[ordinal]].append(site)
                following = (word>>51)&0xfff
                if not following:
                    break
                site += following*4
    return result


def ethernet_reserved_slots(original, kc, own, base, slide, own_data):
    """Return reserved-placeholder bindings, proven to occur only in own vtable.

    Merge into ethernet_private_slots' bindings. All new entries require the
    same validate_ethernet_slot check (use ARM64 normalization described below).
    Exact target and original runtime names are retained for the final audit.
    """
    imports = companion_import_sites(own_data)
    family = macho(original,kc['entries'][FAMILY][1])
    public = exports(original,family)
    table = next(a for a,ns in public.items() if BASE_TABLE in ns)
    pointers = {p['site']:p for p in chains(original,kc) if table+16 <= p['site'] < table+16+400*8}
    own_table = own[OWN_TABLE]
    bindings = {}
    reviewed = {
        '__ZN19IONetworkController29_RESERVEDIONetworkController2Ev':
            (331, '__ZN19IONetworkController11outputStartEP18IONetworkInterfacej'),
        '__ZN19IONetworkController29_RESERVEDIONetworkController3Ev':
            (332, '__ZN19IONetworkController27setInputPacketPollingEnableEP18IONetworkInterfaceb'),
        '__ZN19IONetworkController29_RESERVEDIONetworkController4Ev':
            (333, '__ZN19IONetworkController16pollInputPacketsEP18IONetworkInterfacejP11IOMbufQueuePv'),
        '__ZN19IONetworkController29_RESERVEDIONetworkController5Ev':
            (334, '__ZN19IONetworkController28networkInterfaceNotificationEP18IONetworkInterfacejPv'),
    }
    for name, sites in imports.items():
        if name not in reviewed:
            continue
        if not sites:
            raise ValueError('unused reserved Ethernet import cannot be adapted')
        allowed = []
        for site in sites:
            index, remainder = divmod(site-own_table-16,8)
            if remainder or not 0 <= index < 400:
                raise ValueError('reserved Ethernet method is used outside its inherited vtable')
            pointer = pointers[table+16+index*8]
            expected_slot, expected_method = reviewed[name]
            if index != expected_slot or expected_method not in public.get(pointer['target'], []):
                raise ValueError('reserved Ethernet placeholder differs from reviewed 25F71 replacement')
            allowed.append(dict(slot=index,original=pointer,new_site=base+site,
                                new_target=pointer['target']+slide,
                                sdk_placeholder=name,runtime_names=public.get(pointer['target'],[])))
        if len({x['new_target'] for x in allowed}) != 1:
            raise ValueError('reserved Ethernet placeholder requires different slot targets')
        bindings[name]=allowed
    return bindings
