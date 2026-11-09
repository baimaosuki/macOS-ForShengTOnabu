#!/usr/bin/env python3
"""Drop the prelinked kexts' __PLK_LINKEDIT (their symbol tables and function starts) from a
macOS-ForSheng prelinked kernel so it fits the 96 MiB uke vendor_boot.

The segment keeps its VM range (filesize 0, loaded as zeros). Every kext's LC_SYMTAB, LC_DYSYMTAB and
linkedit data command that pointed into it is emptied, and every file offset after it moves back.
Kext symbols are only needed for symbolication; the kernel's own __LINKEDIT and chained fixups and the
standalone NativeUfs (__NVB_*) are kept.
Usage: strip-plk-linkedit.py IN OUT
"""
import struct
import sys

src, dst = sys.argv[1], sys.argv[2]
d = bytearray(open(src, 'rb').read())
MAGIC = b'\xcf\xfa\xed\xfe\x0c\x00\x00\x01'
LINKEDIT_DATA = (0x1d, 0x1e, 0x26, 0x29, 0x2b, 0x2e, 0x80000033, 0x80000034)


def commands(at):
    count, cursor = struct.unpack_from('<I', d, at + 16)[0], at + 32
    for _ in range(count):
        kind, length = struct.unpack_from('<II', d, cursor)
        yield kind, cursor
        cursor += length


start = size = None
for kind, c in commands(0):
    if kind == 0x19 and d[c + 8:c + 24].rstrip(b'\0') == b'__PLK_LINKEDIT':
        start, size = struct.unpack_from('<QQ', d, c + 40)
assert start and size
end = start + size
inside = lambda off: start <= off < end


def moved(off):
    assert not inside(off)
    return off - size if off >= end else off


headers = [0] + [i for i in range(8, start, 8) if d[i:i + 8] == MAGIC] + \
          [i for i in range(end, len(d), 8) if d[i:i + 8] == MAGIC]
emptied = 0
for h in headers:
    for kind, c in commands(h):
        if kind == 0x19:
            fileoff, filesize = struct.unpack_from('<QQ', d, c + 40)
            if filesize and (inside(fileoff) or (h == 0 and fileoff == start)):
                struct.pack_into('<QQ', d, c + 40, 0, 0)
                emptied += 1
            elif filesize:
                struct.pack_into('<Q', d, c + 40, moved(fileoff))
        elif kind == 2:
            symoff, nsyms, stroff, strsize = struct.unpack_from('<IIII', d, c + 8)
            if inside(symoff) or inside(stroff):
                struct.pack_into('<IIII', d, c + 8, 0, 0, 0, 0)
            else:
                struct.pack_into('<IIII', d, c + 8, moved(symoff) if nsyms else symoff, nsyms,
                                 moved(stroff) if strsize else stroff, strsize)
        elif kind == 0xb:
            fields = list(struct.unpack_from('<18I', d, c + 8))
            if any(inside(fields[i]) for i in range(6, 18, 2) if fields[i + 1]):
                struct.pack_into('<18I', d, c + 8, *([0] * 18))
            else:
                for i in range(6, 18, 2):
                    if fields[i + 1]:
                        fields[i] = moved(fields[i])
                struct.pack_into('<18I', d, c + 8, *fields)
        elif kind in LINKEDIT_DATA:
            off, length = struct.unpack_from('<II', d, c + 8)
            if length and inside(off):
                struct.pack_into('<II', d, c + 8, 0, 0)
            elif length:
                struct.pack_into('<I', d, c + 8, moved(off))
out = d[:start] + d[end:]
open(dst, 'wb').write(out)
print(f'{dst}: {len(out) / 1048576:.1f} MB (removed {size / 1048576:.1f} MB, {len(headers)} headers, '
      f'{emptied} linkedit segments emptied)')
