#!/usr/bin/env python3
"""Generate the temporary PID 1 data-fault diagnostic patch for sleh.c.

The diagnostic runs inside handle_user_abort() after the page-fault recovery
path has already failed, so a recoverable fault never reaches it.  It panics
with the raw saved user state, which is the only channel that survives a
kernel that cannot start launchd.
"""
from difflib import unified_diff
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'sources/macos26/xnu/osfmk/arm64/sleh.c'
OUTPUT = ROOT / 'platform/nabu/macos26/patches/zzz-nabu-pid1-fault-diag.patch'

INCLUDE_ANCHOR = '#include <kern/task.h>\n'
INCLUDE_NEW = '#include <kern/task.h>\n#include <sys/proc.h>\n'

TRIAGE_ANCHOR = (
    '\texception_triage(exc, codes, numcodes);\n'
    '\t__builtin_unreachable();\n'
    '}\n'
    '\n'
    '/**\n'
    ' * Panic because the kernel abort handler'
)

BLOCK = '''\t/*
\t * Temporary diagnostic: when PID 1 dies on an unrecoverable EL0 data abort,
\t * the panic reason carries the raw fault context (user PC/LR/SP, ESR, FAR,
\t * the faulting instruction and every general register) so the effective
\t * address of the faulting access can be recomputed off-device.
\t * This sits after the recovery path has failed: recoverable faults never
\t * reach it, so nothing here consumes the normal fault accounting.
\t */
\tif (proc_selfpid() == 1 &&
\t    is_saved_state64(state) &&
\t    ESR_EC(esr) == ESR_EC_DABORT_EL0 &&
\t    fault_addr == 0x80080000ULL) {
\t\tconst arm_saved_state64_t *ss = const_saved_state64(state);
\t\tuint32_t insn = 0;
\t\tint fetch_error = copyin((user_addr_t)ss->pc, &insn, sizeof(insn));

\t\tpanic("NABU-PID1-FAULT "
\t\t    "UPC=%llx ULR=%llx USP=%llx "
\t\t    "UESR=%llx UFAR=%llx VMERR=%llx "
\t\t    "INSN=%08x FETCH=%d "
\t\t    "X0=%llx X1=%llx X2=%llx X3=%llx "
\t\t    "X4=%llx X5=%llx X6=%llx X7=%llx "
\t\t    "X8=%llx X9=%llx X10=%llx X11=%llx "
\t\t    "X12=%llx X13=%llx X14=%llx X15=%llx "
\t\t    "X16=%llx X17=%llx X18=%llx X19=%llx "
\t\t    "X20=%llx X21=%llx X22=%llx X23=%llx "
\t\t    "X24=%llx X25=%llx X26=%llx X27=%llx "
\t\t    "X28=%llx FP=%llx",
\t\t    ss->pc, ss->lr, ss->sp,
\t\t    esr, (uint64_t)fault_addr, (uint64_t)codes[0],
\t\t    insn, fetch_error,
\t\t    ss->x[0], ss->x[1], ss->x[2], ss->x[3],
\t\t    ss->x[4], ss->x[5], ss->x[6], ss->x[7],
\t\t    ss->x[8], ss->x[9], ss->x[10], ss->x[11],
\t\t    ss->x[12], ss->x[13], ss->x[14], ss->x[15],
\t\t    ss->x[16], ss->x[17], ss->x[18], ss->x[19],
\t\t    ss->x[20], ss->x[21], ss->x[22], ss->x[23],
\t\t    ss->x[24], ss->x[25], ss->x[26], ss->x[27],
\t\t    ss->x[28], ss->fp);
\t}

'''


def main() -> int:
    text = SOURCE.read_text()
    if text.count(INCLUDE_ANCHOR) != 1:
        raise SystemExit('include anchor is not unique')
    if text.count(TRIAGE_ANCHOR) != 1:
        raise SystemExit('triage anchor is not unique')
    patched = text.replace(INCLUDE_ANCHOR, INCLUDE_NEW, 1)
    patched = patched.replace(TRIAGE_ANCHOR, BLOCK + TRIAGE_ANCHOR, 1)
    diff = unified_diff(text.splitlines(keepends=True),
                        patched.splitlines(keepends=True),
                        fromfile='a/osfmk/arm64/sleh.c',
                        tofile='b/osfmk/arm64/sleh.c', n=3)
    body = ''.join(diff)
    OUTPUT.write_text('diff --git a/osfmk/arm64/sleh.c b/osfmk/arm64/sleh.c\n' + body)
    print('wrote', OUTPUT.relative_to(ROOT))
    print(body.count('@@'), 'hunks')
    for line in body.splitlines():
        if line.startswith('@@'):
            print('  ', line)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
