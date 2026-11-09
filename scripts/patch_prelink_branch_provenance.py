#!/usr/bin/env python3
"""Apply the branch-provenance fix to scripts/prelink_fileset_kernel.py.

Two edits, both asserted by exact-once matching:

1. section_64 parsing keeps `flags`, so code sections are recognised from the
   image instead of being inferred from the segment name.
2. The kext->kernel B/BL pass stops scanning every 4 bytes. Candidates come from
   a recursive-descent walk over the kext's real entry points, a candidate must
   bind exactly to a symbol, and the pass asserts afterwards that the only words
   it changed are the ones it recorded.

A 32-bit word inside a constant table can match the B/BL encoding (SHA-256
K[31] = 0x14292967 has opcode 0x14000000); rewriting such a word corrupts the
kernel's digests, which then rejects correctly signed pages.

Run with --check to validate the anchors and compile without writing.
"""
import sys
from pathlib import Path

TARGET = Path('scripts/prelink_fileset_kernel.py')

OLD_SECTIONS = """            sectname, segname, addr, size, file_offset = struct.unpack_from(
                '<16s16sQQI', data, cursor)
            sections.append(dict(name=sectname.rstrip(b'\\x00').decode(),
                                 segment=segname.rstrip(b'\\x00').decode(),
                                 addr=addr, size=size, offset=file_offset,
                                 command_offset=cursor))"""

NEW_SECTIONS = """            sectname, segname, addr, size, file_offset = struct.unpack_from(
                '<16s16sQQI', data, cursor)
            flags = struct.unpack_from('<I', data, cursor + 64)[0]
            sections.append(dict(name=sectname.rstrip(b'\\x00').decode(),
                                 segment=segname.rstrip(b'\\x00').decode(),
                                 addr=addr, size=size, offset=file_offset,
                                 flags=flags, command_offset=cursor))"""

HELPERS = '''
# --- instruction provenance -------------------------------------------------
# A constant table can live inside __TEXT_EXEC, so the four bytes of a B/BL
# encoding are not evidence of an instruction: SHA-256 K[31] = 0x14292967 has
# opcode 0x14000000, and a scan that rewrites every such word turns that data
# into a branch and corrupts every digest the kernel computes.  Candidates here
# come from a recursive-descent walk over the kext, so literal pools are only
# reachable if real code branches into them.
S_ATTR_PURE_INSTRUCTIONS = 0x80000000
S_ATTR_SOME_INSTRUCTIONS = 0x00000400

NON_RETURNING_BRANCHES = ('b', 'br', 'ret', 'braa', 'braaz', 'retaa')


def sign_extend(value, bits):
    return value - (1 << bits) if value >> (bits - 1) else value


def instruction_class(word):
    """Branch class for the A64 encodings the walk follows, else None."""
    if word & 0xFC000000 == 0x14000000:
        return 'b'
    if word & 0xFC000000 == 0x94000000:
        return 'bl'
    if word & 0xFF000010 == 0x54000000:
        return 'bcond'
    if word & 0x7E000000 == 0x34000000:
        return 'cbz'
    if word & 0x7E000000 == 0x36000000:
        return 'tbz'
    if word in (0xD61F081F, 0xD61F0C1F):
        return 'braaz'
    if word in (0xD63F081F, 0xD63F0C1F):
        return 'blraaz'
    if word & 0xFFFFFC00 in (0xD71F0800, 0xD71F0C00):
        return 'braa'
    if word & 0xFFFFFC00 in (0xD73F0800, 0xD73F0C00):
        return 'blraa'
    if word in (0xD65F0BFF, 0xD65F0FFF):
        return 'retaa'
    if word & 0xFFFFFC1F == 0xD61F0000:
        return 'br'
    if word & 0xFFFFFC1F == 0xD63F0000:
        return 'blr'
    if word & 0xFFFFFC1F == 0xD65F0000:
        return 'ret'
    return None


def relative_target(word, site):
    """Absolute target for the PC-relative branch forms, else None."""
    if word & 0xFC000000 in (0x14000000, 0x94000000):
        return site + sign_extend(word & 0x03FFFFFF, 26) * 4
    if word & 0xFF000010 == 0x54000000:
        return site + sign_extend((word >> 5) & 0x7FFFF, 19) * 4
    if word & 0x7E000000 == 0x34000000:
        return site + sign_extend((word >> 5) & 0x7FFFF, 19) * 4
    if word & 0x7E000000 == 0x36000000:
        return site + sign_extend((word >> 5) & 0x3FFF, 14) * 4
    return None


def code_spans(image_segments):
    """[(start, end)] for sections the image itself marks as code."""
    spans = []
    for segment in image_segments:
        for section in segment['sections']:
            if not section['size']:
                continue
            flags = section.get('flags', 0)
            if not flags & (S_ATTR_PURE_INSTRUCTIONS | S_ATTR_SOME_INSTRUCTIONS):
                continue
            spans.append((section['addr'], section['addr'] + section['size']))
    return spans


def instruction_sites(data, image_segments, image_base, file_base, seeds):
    """VAs the recursive-descent walk proves are instruction starts.

    `image_base`/`file_base` map a component VA to its offset inside the kernel
    collection, because a fileset entry stores segment file offsets relative to
    the component, not to the collection.
    """
    spans = code_spans(image_segments)
    if not spans:
        return set(), spans

    def inside(va):
        return any(start <= va < end for start, end in spans)

    seen, pending = set(), [va for va in seeds if inside(va)]
    while pending:
        va = pending.pop()
        while True:
            if va in seen or not inside(va):
                break
            at = file_base + (va - image_base)
            if at < 0 or at + 4 > len(data):
                break
            word = struct.unpack_from('<I', data, at)[0]
            seen.add(va)
            target = relative_target(word, va)
            if target is not None and inside(target) and target not in seen:
                pending.append(target)
            if instruction_class(word) in NON_RETURNING_BRANCHES:
                break
            va += 4
    return seen, spans

'''

OLD_BRANCH_START = "    # --- direct BL/B leaving the placed components (kext -> Apple kernel text) ---"
OLD_BRANCH_END = """    print('  branch pass: %d rerouted, %d intra-component kept, %d outside kernel text,'
          ' %d unresolved %s'
          % (branch_kernel, branch_skipped, branch_outside, len(branch_unresolved),
             [c for c, _, _, _ in branch_unresolved[:5]]))"""

NEW_BRANCH = '''    # --- direct B/BL leaving the placed components (kext -> Apple kernel text) ---
    # Uniform Slide keeps intra-container PC-relative branches exact, but a branch
    # into the Apple kernel's own text must be rerouted to the same symbol in our
    # native kernel: its target did not move with us while the CPU adds our slide to
    # it (site moved by +slide, imm26 unchanged) -> lands in a zeroed hole.
    kernel_text = []
    for entry in fileset(kc, top):
        if entry['entry_id'] != 'com.apple.kernel':
            continue
        for seg in segments(kc, commands(kc, entry['fileoff'])):
            if seg['name'] in ('__TEXT', '__TEXT_EXEC') and seg['vmsize']:
                kernel_text.append((seg['vmaddr'], seg['vmaddr'] + seg['vmsize']))
    native_text = [(s['vmaddr'], s['vmaddr'] + s['vmsize'])
                   for s in native_segments
                   if s['name'] in ('__TEXT', '__TEXT_EXEC') and s['vmsize']]

    def in_spans(va, spans):
        return any(a <= va < b for a, b in spans)

    proven_cache = {}

    def proven_sites(component):
        """Instruction starts proven by recursive descent in this kext."""
        if component not in proven_cache:
            module = modules[component]
            seeds = list(pkb.read_function_starts(kc, module['image'], module['segments']))
            for _name, ntype, value in pkb.read_symtab(kc, module['image']):
                if value and (ntype & 0x0E) == 0x0E and not (ntype & 0xE0):
                    seeds.append(value)
            proven_cache[component] = instruction_sites(
                kc, module['segments'], module['vmaddr'], module['fileoff'], seeds)
        return proven_cache[component]

    manifest = {}
    for item in getattr(args, 'branch_manifest', None) or []:
        for record in json.loads(Path(item).read_text()):
            manifest[(record['component'], int(record['site'], 0))] = record.get('reason', '')

    branch_before = bytes(output)
    branch_sites = []
    branch_kernel = branch_skipped = branch_outside = 0
    branch_non_exact, branch_unresolved = [], []
    for record in ranges:
        if record['segment'] != '__TEXT_EXEC':
            continue
        proven, spans = proven_sites(record['component'])
        for site_orig in sorted(proven):
            if not (record['old_va'] <= site_orig < record['old_va'] + record['size'] - 3):
                continue
            at = record['new_off'] + (site_orig - record['old_va'])
            word = struct.unpack_from('<I', output, at)[0]
            op = word & 0xFC000000
            if op not in (0x94000000, 0x14000000):
                continue
            imm = word & 0x03FFFFFF
            if imm & 0x02000000:
                imm -= 0x04000000
            dest = site_orig + imm * 4
            if any(r['old_va'] <= dest < r['old_va'] + r['size'] for r in ranges):
                branch_skipped += 1
                continue
            if not in_spans(dest, kernel_text):
                branch_outside += 1
            site_new = record['new_va'] + (site_orig - record['old_va'])
            bound, sym, exact = bind_kernel(dest)
            if bound is None:
                branch_unresolved.append((record['component'], site_orig, dest, sym))
                continue
            if not in_spans(bound, native_text):
                branch_unresolved.append((record['component'], site_orig, dest,
                                          '%s->%#x' % (sym, bound)))
                continue
            if not exact and (record['component'], site_orig) not in manifest:
                # Nearest-symbol + offset is not evidence: it maps any word that
                # merely looks like a branch onto a plausible address. Such sites
                # must be inspected and listed explicitly.
                branch_non_exact.append((record['component'], site_orig, dest, sym))
                continue
            delta = bound - site_new
            if delta % 4 or not (-(1 << 27) <= delta < (1 << 27)):
                raise ValueError('branch %s:%#x -> %#x outside imm26 range'
                                 % (record['component'], site_orig, bound))
            new_word = op | ((delta >> 2) & 0x03FFFFFF)
            struct.pack_into('<I', output, at, new_word)
            branch_sites.append((at, word, new_word))
            branch_kernel += 1
    print('  branch pass: %d rerouted, %d intra-component kept, %d outside kernel text,'
          ' %d unresolved, %d non-exact refused'
          % (branch_kernel, branch_skipped, branch_outside, len(branch_unresolved),
             len(branch_non_exact)))
    if branch_unresolved:
        print('   unresolved sample %s' % [c for c, _, _, _ in branch_unresolved[:5]])
    if branch_non_exact:
        print('   non-exact sample %s'
              % [(c, site, dest, sym) for c, site, dest, sym in branch_non_exact[:5]])
    if branch_non_exact or branch_unresolved:
        raise SystemExit(
            'branch pass refused %d non-exact and %d unresolved site(s); each must be'
            ' verified and listed in --branch-manifest before this image is used'
            % (len(branch_non_exact), len(branch_unresolved)))

    # Post-condition: the pass may only change words it recorded. A data word
    # rewritten as a branch shows up here as a change with no recorded site.
    changed = set()
    for offset in range(0, len(branch_before) - 3, 4):
        if branch_before[offset:offset + 4] != output[offset:offset + 4]:
            changed.add(offset)
    unrecorded = sorted(changed - {offset for offset, _, _ in branch_sites})
    if unrecorded:
        raise SystemExit('branch pass changed %d unrecorded word(s), first at %#x'
                         % (len(unrecorded), unrecorded[0]))
    print('  branch pass diff: %d word(s) changed, all recorded' % len(changed))'''


def main():
    check = '--check' in sys.argv
    text = TARGET.read_text()
    for anchor, label in ((OLD_SECTIONS, 'section parse block'),
                          (OLD_BRANCH_START, 'branch pass comment'),
                          (OLD_BRANCH_END, 'branch pass print'),
                          ("\n\ndef fixup_slots(", 'fixup_slots anchor'),
                          ("    parser.add_argument('--base', type=lambda v: int(v, 0), default=None,\n",
                           'argparse anchor')):
        assert text.count(anchor) == 1, '%s not found exactly once' % label

    patched = text.replace(OLD_SECTIONS, NEW_SECTIONS, 1)
    patched = patched.replace("\n\ndef fixup_slots(", HELPERS + "\n\ndef fixup_slots(", 1)
    start = patched.index(OLD_BRANCH_START)
    end = patched.index(OLD_BRANCH_END) + len(OLD_BRANCH_END)
    patched = patched[:start] + NEW_BRANCH + patched[end:]

    added_arg = ("    parser.add_argument('--branch-manifest', type=Path, action='append',\n"
                 "                        help='JSON list of {component, site, dest, reason}'\n"
                 "                             ' branch sites verified by hand; non-exact'\n"
                 "                             ' bindings outside this list fail the build')\n")
    patched = patched.replace(
        "    parser.add_argument('--base', type=lambda v: int(v, 0), default=None,\n",
        added_arg + "    parser.add_argument('--base', type=lambda v: int(v, 0), default=None,\n", 1)

    compile(patched, str(TARGET), 'exec')
    if check:
        print('patch applies cleanly (%d -> %d bytes)' % (len(text), len(patched)))
        return 0
    TARGET.write_text(patched)
    print('patched %s (%d -> %d bytes)' % (TARGET, len(text), len(patched)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
