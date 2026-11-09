#!/usr/bin/env python3
"""Second-stage patch: make kext branch binding verifiable instead of plausible.

`bind_kernel` maps an Apple kernel VA onto our kernel by symbol name and falls
back to "nearest symbol + offset" for anything that does not sit exactly on a
symbol. That fallback is how a SHA-256 constant became a branch target, so the
offset form now needs evidence:

  * the target must still be inside the same Apple symbol's extent (below the
    next symbol in the table), otherwise "nearest" is simply the wrong symbol;
  * the mapped address must land inside our kernel's executable text.

Anything else stays unbound and has to be listed in --branch-manifest after
manual inspection.

Run with --check to validate anchors without writing.
"""
import sys
from pathlib import Path

TARGET = Path('scripts/prelink_fileset_kernel.py')

OLD = """    def bind_kernel(apple_va):
        \"\"\"Apple kernel VA -> our kernel VA, by symbol name (exact first).\"\"\"
        names = apple_symbols.names_at(apple_va)
        present = sorted(n for n in names if n in kernel_symbols.by_name)
        if present:
            return kernel_symbols.by_name[present[0]], present[0], True
        nearest = apple_symbols.nearest_below(apple_va)
        if nearest is not None:
            near_names = sorted(n for n in apple_symbols.names_at(nearest)
                                if n in kernel_symbols.by_name)
            if near_names:
                return (kernel_symbols.by_name[near_names[0]] + (apple_va - nearest),
                        near_names[0], False)
        return None, (sorted(names)[0] if names else None), False
"""

NEW = """    native_text_spans = [(s['vmaddr'], s['vmaddr'] + s['vmsize'])
                         for s in native_segments
                         if s['name'] in ('__TEXT', '__TEXT_EXEC') and s['vmsize']]

    def in_native_text(va):
        return any(a <= va < b for a, b in native_text_spans)

    def bind_kernel(apple_va):
        \"\"\"Apple kernel VA -> our kernel VA, with the evidence kept explicit.

        The exact form binds by name. The offset form is only used when the
        target provably stays inside the same Apple function and the result
        provably lands in our kernel's code; otherwise it reports failure instead
        of inventing an address.
        \"\"\"
        import bisect as _bisect
        names = apple_symbols.names_at(apple_va)
        present = sorted(n for n in names if n in kernel_symbols.by_name)
        if present:
            return kernel_symbols.by_name[present[0]], present[0], True
        nearest = apple_symbols.nearest_below(apple_va)
        if nearest is not None:
            addresses = apple_symbols.sorted_addresses
            index = _bisect.bisect_right(addresses, nearest)
            next_symbol = addresses[index] if index < len(addresses) else None
            if next_symbol is None or apple_va < next_symbol:
                near_names = sorted(n for n in apple_symbols.names_at(nearest)
                                    if n in kernel_symbols.by_name)
                if near_names:
                    delta = apple_va - nearest
                    mapped = kernel_symbols.by_name[near_names[0]] + delta
                    if in_native_text(mapped):
                        return mapped, near_names[0], False
        return None, (sorted(names)[0] if names else None), False
"""


def main():
    check = '--check' in sys.argv
    text = TARGET.read_text()
    assert text.count(OLD) == 1, 'bind_kernel block not found exactly once'
    patched = text.replace(OLD, NEW, 1)
    compile(patched, str(TARGET), 'exec')
    if check:
        print('second-stage patch applies cleanly (%d -> %d bytes)' % (len(text), len(patched)))
        return 0
    TARGET.write_text(patched)
    print('patched bind_kernel (%d -> %d bytes)' % (len(text), len(patched)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
