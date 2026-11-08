#!/usr/bin/env python3
"""Post-link word patches for the nabu kernel image.

prelink_kcg_bind.py owns every relocation; this owns the handful of byte
edits that make missing Apple firmware features optional.  Each patch states
the word it expects, so a band layout change fails loudly instead of quietly
corrupting an unrelated instruction.
"""
import argparse
import struct
import sys

PATCHES = {
    'image4-manifest-optional': [
        (0xfffffe0008b02a44, 0xb4000700, 0xd503201f,
         'AppleImage4 start(): cbz x0,<panic "failed to get manifest '
         'properties"> -> nop, so a boot without /chosen/manifest-properties '
         'continues with a NULL entry instead of panicking'),
    ],
}


def read_word(image, va):
    """Band segments are mapped with fileoff == vmaddr - image_base."""
    image_base = 0xfffffe0007004000
    off = va - image_base
    if off < 0 or off + 4 > len(image):
        raise SystemExit('VA %#x outside the file' % va)
    return off, struct.unpack_from('<I', image, off)[0]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('image')
    ap.add_argument('--patch', action='append', default=[])
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--check', action='store_true',
                    help='verify without writing')
    args = ap.parse_args(argv)

    if args.list:
        for name, words in PATCHES.items():
            print(name)
            for va, old, new, why in words:
                print('  %#018x %08x -> %08x  %s' % (va, old, new, why))
        return 0
    if not args.patch:
        raise SystemExit('nothing to do: pass --patch NAME or --list')

    data = bytearray(open(args.image, 'rb').read())
    for name in args.patch:
        if name not in PATCHES:
            raise SystemExit('unknown patch %r' % name)
        for va, old, new, why in PATCHES[name]:
            off, cur = read_word(data, va)
            state = 'ok' if cur == old else ('done' if cur == new else 'MISMATCH')
            print('  %-28s %#018x file=%#x %08x -> %08x  [%s]'
                  % (name, va, off, cur, new, state))
            if state == 'MISMATCH':
                raise SystemExit('refusing: %#x holds %08x, expected %08x'
                                 % (va, cur, old))
            if state == 'ok' and not args.check:
                struct.pack_into('<I', data, off, new)
    if not args.check:
        open(args.image, 'wb').write(data)
        print('  written %d bytes' % len(data))
    return 0


if __name__ == '__main__':
    sys.exit(main())
