#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# End-to-end tests for scripts/prelink_kcg_bind.py on the real 26.5 assets.
#
#   KERNEL=/tmp/k8/out/kernel.ci.nabu \
#   KC=out/macos26-assets/kc.mac13g.arm64.macho \
#   FILESET=out/macos26-assets/kc.mac13g.fileset.json \
#   WORK=/tmp/nabu2/out bash scripts/prelink_kcg_bind_tests.sh
#
# Tests:
#   1. bind       - produce the image, require loadable=true and 0 unresolved
#   2. verify     - independent verifier over the produced image
#   3. idempotent - a second run is byte-identical to the first
#   4. missing    - a kernel whose strtab lost one referenced symbol must make
#                   the tool refuse to write an image (unresolved blocks)
#   5. checker    - the produced image passes the structural checker inside
#                   scripts/prelink_fileset_kernel.py (stage 1 invariants)
set -euo pipefail

KERNEL=${KERNEL:-/tmp/k8/out/kernel.ci.nabu}
KC=${KC:-out/macos26-assets/kc.mac13g.arm64.macho}
FILESET=${FILESET:-out/macos26-assets/kc.mac13g.fileset.json}
WORK=${WORK:-/tmp/nabu2/out}
REPO=${REPO:-$(cd "$(dirname "$0")/.." && pwd)}

mkdir -p "$WORK"
status=0
step() { printf '\n=== %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; status=1; }

step "1. bind ($(basename "$KERNEL") + $(basename "$KC"))"
python3 "$REPO/scripts/prelink_kcg_bind.py" \
    --native "$KERNEL" --kc "$KC" --fileset "$FILESET" \
    --output "$WORK/kernel.prelinked.nabu" \
    --report "$WORK/report.json" || fail "bind exited non-zero"

python3 - "$WORK/report.json" <<'PY' || fail "report is not loadable"
import json, sys
report = json.load(open(sys.argv[1]))
assert report['loadable'] is True, 'loadable is false'
assert report['unresolved_sites'] == 0, report['unresolved']
assert report['symbol_coverage']['exact_percent'] == 100.0
assert report['veneer_count'] == 0
assert all(a['ok'] for a in report['structural_assertions']), [
    a for a in report['structural_assertions'] if not a['ok']]
print('   loadable=True, %d components, coverage %.2f%%, %d pointers, %d branches'
      % (report['component_count'], report['symbol_coverage']['exact_percent'],
         report['pointer_relocation_count'], report['branch_relocation_count']))
for key in ('a', 'b', 'c', 'd'):
    print('   slots[%s] = %d' % (key, report['slots_by_category'][key]))
PY

step "2. independent verification"
python3 "$REPO/scripts/prelink_verify.py" \
    --image "$WORK/kernel.prelinked.nabu" --report "$WORK/report.json" \
    --native "$KERNEL" || fail "verifier rejected the image"

step "3. idempotence"
python3 "$REPO/scripts/prelink_kcg_bind.py" \
    --native "$KERNEL" --kc "$KC" --fileset "$FILESET" \
    --output "$WORK/kernel.prelinked.again.nabu" \
    --report "$WORK/report.again.json" >/dev/null
if cmp -s "$WORK/kernel.prelinked.nabu" "$WORK/kernel.prelinked.again.nabu"; then
    echo '   byte-identical on the second run'
else
    fail 'second run differs from the first'
fi

step "4. missing symbol must block output"
python3 - "$KERNEL" "$WORK/kernel.missingsym.nabu" "$WORK/report.json" <<'PY'
import struct, sys
source, destination, report_path = sys.argv[1], sys.argv[2], sys.argv[3]
import json
target = json.load(open(report_path))['pointer_relocations']
name = next(r['symbol'] for r in target if r['category'] == 'c' and r.get('symbol'))
data = bytearray(open(source, 'rb').read())
magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds = struct.unpack_from(
    '<IiiIII', data, 0)
cursor = 32
for _ in range(ncmds):
    cmd, size = struct.unpack_from('<II', data, cursor)
    if cmd == 0x2:
        symoff, nsyms, stroff, strsize = struct.unpack_from('<4I', data, cursor + 8)
        break
    cursor += size
hit = 0
for index in range(nsyms):
    strx, ntype, sect, desc, value = struct.unpack_from('<IBBHQ', data, symoff + index * 16)
    if not value or ntype & 0xE0 or (ntype & 0x0E) != 0x0E:
        continue
    end = data.find(b'\0', stroff + strx)
    if bytes(data[stroff + strx:end]).decode('utf-8', 'replace') == name:
        data[stroff + strx:stroff + strx + len(name)] = b'Z' * len(name)
        hit += 1
if not hit:
    sys.exit('could not find %s in the strtab' % name)
open(destination, 'wb').write(bytes(data))
print('   broke %d strtab entr%s for %s' % (hit, 'y' if hit == 1 else 'ies', name))
PY
rm -f "$WORK/kernel.unresolved.nabu"
set +e
python3 "$REPO/scripts/prelink_kcg_bind.py" \
    --native "$WORK/kernel.missingsym.nabu" --kc "$KC" --fileset "$FILESET" \
    --output "$WORK/kernel.unresolved.nabu" \
    --report "$WORK/report.unresolved.json" >/dev/null 2>"$WORK/unresolved.err"
code=$?
set -e
if [ "$code" -ne 2 ]; then
    fail "expected exit 2 for unresolved references, got $code"
fi
if [ -e "$WORK/kernel.unresolved.nabu" ]; then
    fail 'an image was written despite unresolved references'
else
    echo "   refused to write (exit $code): $(cat "$WORK/unresolved.err")"
fi
python3 - "$WORK/report.unresolved.json" <<'PY' || fail 'unresolved report is wrong'
import json, sys
report = json.load(open(sys.argv[1]))
assert report['unresolved_sites'] > 0
assert report['loadable'] is False
print('   report records %d unresolved sites across %d names, loadable=False'
      % (report['unresolved_sites'], len(report['unresolved'])))
PY

step "5. stage-1 structural checker"
if [ -f "$REPO/scripts/prelink_fileset_kernel.py" ]; then
    python3 - "$WORK/kernel.prelinked.nabu" "$WORK/report.json" <<'PY'
import json, struct, sys
image, report_path = sys.argv[1], sys.argv[2]
data = open(image, 'rb').read()
report = json.load(open(report_path))
magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds = struct.unpack_from(
    '<IiiIII', data, 0)
assert magic == 0xFEEDFACF and filetype == 2, (hex(magic), filetype)
segments, cursor = {}, 32
for _ in range(ncmds):
    cmd, size = struct.unpack_from('<II', data, cursor)
    if cmd == 0x19:
        name, va, vs, fo, fs, mp, ip, nsects, flags = struct.unpack_from(
            '<16s4Q4I', data, cursor + 8)
        segments[name.split(b'\0')[0].decode()] = (va, vs, fo, fs)
    cursor += size
for needed in ('__PRELINK_TEXT', '__PRELINK_INFO', '__PLK_TEXT_EXEC',
               '__PLK_DATA_CONST', '__PRELINK_DATA'):
    assert needed in segments, 'missing segment %s' % needed
low = min(va for va, vs, fo, fs in segments.values() if vs)
high = max(va + vs for va, vs, fo, fs in segments.values() if vs)
assert high - low < 254 * 1024 * 1024, 'kernel window %d exceeds the 254 MB guard' % (
    high - low)
assert len(data) < 128 * 1024 * 1024, 'image is %d bytes, above the ReadFile cap' % len(data)
print('   segments ok, window %.1f MB, file %.1f MB'
      % ((high - low) / 1048576, len(data) / 1048576))
PY
else
    echo '   scripts/prelink_fileset_kernel.py not found, skipped'
fi

step "result"
if [ "$status" -eq 0 ]; then echo 'ALL TESTS PASSED'; else echo 'TESTS FAILED'; fi
exit "$status"
