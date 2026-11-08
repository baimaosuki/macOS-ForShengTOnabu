import struct
import json
import bisect

KC = 'out/nabu-bootkc/kernelcache.release.mac13g.nabu.kc'
k = open(KC, 'rb').read()


def lcs(data, base):
    ncmds, = struct.unpack_from('<I', data, base + 16)
    cur = base + 32
    out = []
    for _ in range(ncmds):
        cmd, cs = struct.unpack_from('<II', data, cur)
        out.append((cmd, cur, cs))
        cur += cs
    return out


# top-level
top = lcs(k, 0)
segs = {}
for cmd, cur, cs in top:
    if cmd == 0x19:
        nm = k[cur + 8:cur + 24].rstrip(b'\0').decode()
        vm, vs, fo, fs = struct.unpack_from('<QQQQ', k, cur + 24)
        segs[nm] = (vm, vs, fo, fs, cur)
print('KC __LINKEDIT vmaddr=%#x fileoff=%#x filesize=%#x' % (segs['__LINKEDIT'][0], segs['__LINKEDIT'][2], segs['__LINKEDIT'][3]))

# fileset entries
fs = json.load(open('out/macos26-assets/kc.mac13g.fileset.json'))
ids = ['com.apple.security.AppleImage4', 'com.apple.driver.AppleMobileFileIntegrity']
for e in fs['entries']:
    if e['entry_id'] not in ids:
        continue
    hdr = e.get('load_address') or e['segments'][0]['vmaddr']
    print('entry %s header=%#x' % (e['entry_id'], hdr))
    for cmd, cur, cs in lcs(k, hdr):
        if cmd == 0x19:
            nm = k[cur + 8:cur + 24].rstrip(b'\0').decode()
            vm, vs, fo, fs2 = struct.unpack_from('<QQQQ', k, cur + 24)
            if nm in ('__TEXT', '__LINKEDIT'):
                print('   seg %-14s vmaddr=%#x vmsize=%#x fileoff=%#x filesize=%#x'
                      % (nm, vm, vs, fo, fs2))
        if cmd == 0x2:
            symoff, nsyms, stroff, strsize = struct.unpack_from('<IIII', k, cur + 8)
            print('   LC_SYMTAB symoff=%#x nsyms=%d' % (symoff, nsyms))

# does any kext segment range cover the shared LINKEDIT va?
le_lo, le_hi = segs['__LINKEDIT'][0], segs['__LINKEDIT'][0] + segs['__LINKEDIT'][3]
print('shared LINKEDIT old range %#x..%#x' % (le_lo, le_hi))
overlap = []
for e in fs['entries']:
    for s in e['segments']:
        if s['name'] == '__LINKEDIT' or not s['vmsize']:
            continue
        lo, hi = s['vmaddr'], s['vmaddr'] + s['vmsize']
        if lo < le_hi and le_lo < hi:
            overlap.append((e['entry_id'], s['name'], hex(lo), hex(hi)))
print('segments overlapping the LINKEDIT range: %d' % len(overlap))
for o in overlap[:8]:
    print('   ', o)
