"""Read linker-authored plain ARM64 kernel relocations for the fileset emitter.

Apple ld emits LC_DYSYMTAB local UNSIGNED relocations for a plain arm64 kernel,
including with -fixup_chains. The CPU subtype has no authenticated native
pointers. Retained arm64e kexts keep the upstream chained-pointer parser.
"""
import hashlib
import struct


def classic_fixups(image):
    magic, cpu, subtype, kind = struct.unpack_from('<4I', image.data, image.at)
    if (magic, cpu, subtype & 0xff, kind, image.at) != (0xfeedfacf, 0x100000c, 0, 2, 0):
        raise ValueError('Classic native relocation input must be a standalone plain ARM64 executable')
    command = image.command(0xb)
    if command is None or command[1] != 80:
        raise ValueError('Missing complete LC_DYSYMTAB local relocation description')
    external, external_count, offset, count = struct.unpack_from('<4I', image.data, command[0] + 64)
    if external_count or count == 0 or offset + 8 * count > len(image.data):
        raise ValueError('Requires a complete locally resolved native relocation table')
    linkedit = [s for s in image.segments if s['name'] == '__LINKEDIT' and
                s['fileoff'] <= offset and offset + 8 * count <= s['fileoff'] + s['filesize']]
    if len(linkedit) != 1:
        raise ValueError('Native relocation table must belong to the one LINKEDIT segment')
    result = {}
    for index in range(count):
        address, flags = struct.unpack_from('<II', image.data, offset + index * 8)
        section = flags & 0xffffff
        if (address & 0x80000000 or flags >> 24 != 6 or
                not 1 <= section <= len(image.sections)):
            raise ValueError(f'Unsupported native relocation kind or target section at index {index}')
        site = image.base + address
        # Packed kernel structures carry 64-bit fields on four-byte boundaries.
        # The collection chain stride is also four bytes.
        if site & 3 or site in result:
            raise ValueError('Unaligned or duplicate native pointer relocation')
        source = image.section(site)
        if source is None or site + 8 > source['va'] + source['size']:
            raise ValueError('Native pointer relocation exceeds its source section')
        target = struct.unpack('<Q', image.read(site, 8))[0]
        destination = image.sections[section - 1]
        # One-past-end addresses are legal C pointers; the linker supplied the
        # target section ordinal and the upstream split record rechecks it.
        if not destination['va'] <= target <= destination['va'] + destination['size']:
            raise ValueError('Native pointer disagrees with its linker target section')
        result[site] = (target, 0, None, None, None)
    return result


def install_reader(image_type):
    original = image_type.fixups

    def fixups(image):
        if image.kind == 2 and image.command(0x80000034) is None:
            return classic_fixups(image)
        return original(image)

    image_type.fixups = fixups


def evidence(image):
    pointers = classic_fixups(image)
    command = image.command(0xb)
    offset, count = struct.unpack_from('<II', image.data, command[0] + 72)
    return dict(format='LC_DYSYMTAB ARM64_RELOC_UNSIGNED', count=len(pointers),
                table_sha256=hashlib.sha256(image.data[offset:offset + count * 8]).hexdigest(),
                native_authentication=False, original_kext_chain_reader_unchanged=True)
