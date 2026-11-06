#!/usr/bin/env python3
"""Measure whether Apple kext undefined symbols exist in our rebuilt kernel.

The kernel cache keeps per-image LC_SYMTABs, so every undefined symbol still has
a *name* even though the chained fixups were fully resolved at Apple build time
(imports_count == 0). Comparing those names against the source-built kernel tells
us how faithful the rebuild is, and whether a name-based relink could ever work.
"""
import argparse
import json
import struct
from pathlib import Path

LC_SYMTAB = 0x2
LC_FILESET_ENTRY = 0x80000035


def iter_commands(data: bytes, base: int = 0):
    ncmds = struct.unpack_from('<I', data, base + 16)[0]
    offset = base + 32
    for _ in range(ncmds):
        command, size = struct.unpack_from('<II', data, offset)
        yield command, size, offset
        offset += size


def symbols(data: bytes, base: int = 0):
    """Yield (name, is_undefined) for every symbol in this Mach-O image."""
    for command, size, offset in iter_commands(data, base):
        if command != LC_SYMTAB:
            continue
        symoff, nsyms, stroff, _ = struct.unpack_from('<IIII', data, offset + 8)
        for index in range(nsyms):
            strx, ntype, _, _, _ = struct.unpack_from('<IBBHQ', data, symoff + index * 16)
            end = data.index(b'\0', stroff + strx)
            name = data[stroff + strx:end].decode(errors='replace')
            if name:
                yield name, (ntype & 0x0e) == 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--kernelcache', type=Path, required=True)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()

    exported = {name for name, undefined in symbols(args.kernel.read_bytes())
                if not undefined and name.startswith('_')}
    cache = args.kernelcache.read_bytes()
    rows = []
    for command, size, offset in iter_commands(cache):
        if command != LC_FILESET_ENTRY:
            continue
        fileoff = struct.unpack_from('<Q', cache, offset + 16)[0]
        name_off = struct.unpack_from('<I', cache, offset + 24)[0]
        identifier = cache[offset + name_off:offset + size].split(b'\0')[0].decode(errors='replace')
        undefined = [n for n, und in symbols(cache, fileoff) if und and n.startswith('_')]
        if not undefined:
            continue
        hits = sum(1 for n in undefined if n in exported)
        rows.append(dict(kext=identifier, undefined=len(undefined), resolved=hits,
                         ratio=round(hits / len(undefined), 4)))
    rows.sort(key=lambda r: -r['undefined'])
    total = sum(r['undefined'] for r in rows)
    resolved = sum(r['resolved'] for r in rows)
    report = dict(kernel=str(args.kernel), kernel_symbols=len(exported),
                  kernelcache=str(args.kernelcache), kexts=len(rows),
                  undefined_total=total, resolved_total=resolved,
                  resolved_ratio=round(resolved / max(total, 1), 4), rows=rows)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    print('  kernel symbols %d, kexts %d, undefined %d, resolved %d (%.1f%%)'
          % (len(exported), len(rows), total, resolved, 100.0 * resolved / max(total, 1)))
    for row in rows[:12]:
        print('  %-46s %6d %6d  %.0f%%'
              % (row['kext'].split('.')[-1][:46], row['undefined'], row['resolved'],
                 100.0 * row['ratio']))


if __name__ == '__main__':
    main()
