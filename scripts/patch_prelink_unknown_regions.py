#!/usr/bin/env python3
"""Third-stage patch: stop treating uncovered code as data, bind it by evidence.

The recursive-descent pass proves most instructions, but it stops at `br xN` and
does not follow jump tables, so real code stays uncovered. The previous revision
skipped the B/BL words in those gaps as if they were data, which silently dropped
two calls in `AppleMatch::_matchUnpack`: the kernel called Apple's `_kfree_data`
(+slide) and executed a zeroed page (`instr=0`).

Uncovered bytes are therefore *unknown*, not data. This patch:

  * counts and reports the code-flagged bytes the walk never reached;
  * treats a B/BL word in a gap as a candidate only when its target binds exactly
    to a symbol of our kernel (never "nearest symbol + offset");
  * records each such site as `unknown-exact` in the report, so the number is
    visible instead of implied;
  * keeps the constant-table gate as the hard post-condition, which is what still
    protects data that merely looks like a branch.

Run with --check to validate anchors without writing.
"""
import sys
from pathlib import Path

TARGET = Path('scripts/prelink_fileset_kernel.py')

OLD_DIAG = """    # Diagnostic: how many B/BL-shaped words live in __TEXT_EXEC but are not
    # proven instructions. Those are data (constant tables, literal pools) and
    # must never be rewritten; the count keeps that set visible in the report.
    unproven = []
    for record in ranges:
        if record['segment'] != '__TEXT_EXEC':
            continue
        proven, _spans = proven_sites(record['component'])
        for word_index in range(0, record['size'] - 3, 4):
            site_orig = record['old_va'] + word_index
            if site_orig in proven:
                continue
            word = struct.unpack_from('<I', output, record['new_off'] + word_index)[0]
            if word & 0xFC000000 in (0x94000000, 0x14000000):
                unproven.append((record['component'], site_orig, word))
"""

NEW_DIAG = """    # Uncovered bytes are *unknown*, not data: the walk stops at `br xN` and does
    # not follow jump tables, so real calls live in the gaps as well as constant
    # tables do. Both numbers stay visible, and a gap candidate is only rewritten
    # when its target binds exactly to one of our symbols.
    unproven = []
    gap_bytes = 0
    for record in ranges:
        if record['segment'] != '__TEXT_EXEC':
            continue
        proven, spans = proven_sites(record['component'])
        code_bytes = 0
        covered = 0
        for start, end in spans:
            low = max(start, record['old_va'])
            high = min(end, record['old_va'] + record['size'])
            if high - low <= 0:
                continue
            code_bytes += high - low
            for site_orig in range(low, high - 3, 4):
                if site_orig in proven:
                    covered += 4
                    continue
                word = struct.unpack_from(
                    '<I', output, record['new_off'] + (site_orig - record['old_va']))[0]
                if word & 0xFC000000 in (0x94000000, 0x14000000):
                    unproven.append((record['component'], site_orig, word))
        gap_bytes += max(0, code_bytes - covered)
"""

OLD_LOOP = """    for record in ranges:
        if record['segment'] != '__TEXT_EXEC':
            continue
        proven, spans = proven_sites(record['component'])
        for site_orig in sorted(proven):
            if not (record['old_va'] <= site_orig < record['old_va'] + record['size'] - 3):
                continue"""

NEW_LOOP = """    unknown_sites = []
    for record in ranges:
        if record['segment'] != '__TEXT_EXEC':
            continue
        proven, spans = proven_sites(record['component'])
        in_gap = {va for component, va, _word in unproven
                  if component == record['component']
                  and record['old_va'] <= va < record['old_va'] + record['size'] - 3}
        for site_orig in sorted(proven | in_gap):
            if not (record['old_va'] <= site_orig < record['old_va'] + record['size'] - 3):
                continue"""

OLD_BIND = """            if not exact and (record['component'], site_orig) not in manifest:
                # Nearest-symbol + offset is not evidence: it maps any word that
                # merely looks like a branch onto a plausible address. Such sites
                # must be inspected and listed explicitly.
                branch_non_exact.append((record['component'], site_orig, dest, sym))
                continue"""

NEW_BIND = """            if not exact and (record['component'], site_orig) not in manifest:
                # Nearest-symbol + offset is not evidence: it maps any word that
                # merely looks like a branch onto a plausible address. Such sites
                # must be inspected and listed explicitly.
                branch_non_exact.append((record['component'], site_orig, dest, sym))
                continue
            if site_orig not in proven:
                # Uncovered by the walk, but the target is an exact symbol of our
                # kernel: a real call behind a jump table. Keep it visible.
                unknown_sites.append(dict(component=record['component'],
                                          site='%#x' % site_orig,
                                          target='%#x' % dest, symbol=sym))"""

OLD_PRINT = """    print('  branch pass: %d rerouted, %d intra-component kept, %d outside kernel text,'
          ' %d unresolved, %d non-exact refused'
          % (branch_kernel, branch_skipped, branch_outside, len(branch_unresolved),
             len(branch_non_exact)))"""

NEW_PRINT = """    print('  branch pass: %d rerouted (%d behind jump tables), %d intra-component kept,'
          ' %d outside kernel text, %d unresolved, %d non-exact refused'
          % (branch_kernel, len(unknown_sites), branch_skipped, branch_outside,
             len(branch_unresolved), len(branch_non_exact)))"""

OLD_SAMPLE = """    print('  unproven sample %s' % unproven_sample)"""

NEW_SAMPLE = """    print('  uncovered code bytes in __TEXT_EXEC: %d (unknown, not data)' % gap_bytes)
    print('  unknown-exact sites: %d %s'
          % (len(unknown_sites), [(s['site'], s['symbol']) for s in unknown_sites[:6]]))
    print('  unproven sample %s' % unproven_sample)"""

OLD_REPORT = """        branch_data_words_refused=len(corrupted),"""

NEW_REPORT = """        code_gap_bytes=gap_bytes,
        branch_unknown_exact=len(unknown_sites),
        branch_unknown_sample=unknown_sites[:16],"""


def main():
    check = '--check' in sys.argv
    text = TARGET.read_text()
    parts = [(OLD_DIAG, NEW_DIAG), (OLD_LOOP, NEW_LOOP), (OLD_BIND, NEW_BIND),
             (OLD_PRINT, NEW_PRINT), (OLD_SAMPLE, NEW_SAMPLE), (OLD_REPORT, NEW_REPORT)]
    for old, _new in parts:
        assert text.count(old) == 1, 'anchor missing or ambiguous: %r' % old[:70]
    patched = text
    for old, new in parts:
        patched = patched.replace(old, new, 1)
    compile(patched, str(TARGET), 'exec')
    if check:
        print('third-stage patch applies cleanly (%d -> %d bytes)' % (len(text), len(patched)))
        return 0
    TARGET.write_text(patched)
    print('patched (%d -> %d bytes)' % (len(text), len(patched)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
