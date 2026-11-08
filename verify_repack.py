import struct
import json
import sys

IMG = '.work/out/kernel.repacked.bisect.nabu'
RPT = '.work/out/kernel.repacked.bisect.json'
KC = 'out/nabu-bootkc/kernelcache.release.mac13g.nabu.kc'

d = open(IMG, 'rb').read()
r = json.load(open(RPT))
kc = open(KC, 'rb').read()

print('image %d B (%.2f MB)' % (len(d), len(d) / 1048576))
print('band  %s' % r.get('band'))
hr = r.get('header_rewrite', [])
for h in hr[:3]:
    print('  hdr %-46s segments=%d carriers=%d symbols=%d'
          % (h['component'], h['segments'], h['carriers'], h['symbols']))
print('  symbols rewritten total: %d'
      % sum(h.get('symbols', 0) for h in hr))

n, = struct.unpack_from('<I', d, 16)
q = 32
segs = {}
for _ in range(n):
    c, sz = struct.unpack_from('<II', d, q)
    if c == 0x19:
        nm = d[q + 8:q + 24].rstrip(b'\0').decode()
        vm, vs, fo, fs = struct.unpack_from('<QQQQ', d, q + 24)
        segs[nm] = (vm, vs, fo, fs, q)
    q += sz

for k in ('__PRELINK_TEXT', '__PLK_TEXT_EXEC', '__PLK_DATA_CONST',
          '__PRELINK_DATA', '__PRELINK_INFO', '__PLK_LINKEDIT'):
    if k in segs:
        vm, vs, fo, fs, _ = segs[k]
        print('  %-16s %#014x vmsize %9d file %9d' % (k, vm, vs, fs))

if '__PLK_LINKEDIT' in segs and '__LINKEDIT' in segs:
    a = d[segs['__PLK_LINKEDIT'][2]:segs['__PLK_LINKEDIT'][2] + 0x2000]
    b = kc[segs['__LINKEDIT'][2]:segs['__LINKEDIT'][2] + 0x2000]
    print('  LINKEDIT payload identical to KC: %s' % (a == b))

# embedded headers
text = segs['__PRELINK_TEXT']
for e in r['prelink_text_layout'][:2]:
    hv = int(e['header'], 16)
    off = hv - text[0]
    fo = text[2]
    ncmds, = struct.unpack_from('<I', d, fo + off + 16)
    cur = fo + off + 32
    print('  --- %s' % e['component'])
    for _ in range(ncmds):
        cmd, cs = struct.unpack_from('<II', d, cur)
        if cmd == 0x2:
            symoff, nsyms, stroff, strsize = struct.unpack_from('<IIII', d, cur + 8)
            n0 = struct.unpack_from('<Q', d, symoff + 8)[0] if symoff and nsyms else 0
            print('    LC_SYMTAB symoff=%#x nsyms=%d stroff=%#x n_value[0]=%#x'
                  % (symoff, nsyms, stroff, n0))
        elif cmd in (0x26, 0x1d, 0x1e, 0x29):
            off2, szd = struct.unpack_from('<II', d, cur + 8)
            print('    cmd %#x dataoff=%#x size=%#x' % (cmd, off2, szd))
        elif cmd == 0x19:
            nm = d[cur + 8:cur + 24].rstrip(b'\0').decode()
            svm, svs, sfo, sfs = struct.unpack_from('<QQQQ', d, cur + 24)
            print('    seg %-14s vmaddr=%#x fileoff=%#x filesize=%#x'
                  % (nm, svm, sfo, sfs))
        cur += cs

# prelink info
info = segs['__PRELINK_INFO']
vm, vs, fo, fs, cmdpos = info
nsects, = struct.unpack_from('<I', d, cmdpos + 64)
for i in range(nsects):
    sp = cmdpos + 72 + i * 80
    sn = d[sp:sp + 16].rstrip(b'\0').decode()
    sa, ssz, soff = struct.unpack_from('<QQI', d, sp + 32)
    extra = ''
    if sn == '__crypto_early':
        extra = ' value=%#x' % struct.unpack_from('<Q', d, soff)[0]
    print('  section %-16s addr=%#x size=%#x%s' % (sn, sa, ssz, extra))

txt = d[fo:fo + fs].rstrip(b'\0').decode('utf-8', 'replace')
for key in ('_PrelinkExecutableLoadAddr', '_PrelinkExecutableSize',
            '_PrelinkKmodInfo', '_PrelinkExecutableSourceAddr',
            '_NativePrelinkPrimary'):
    m = txt.find('<key>%s</key>' % key)
    val = 'MISSING'
    if m >= 0:
        val = txt[m + len(key) + 13:].strip().split('\n')[0].strip()
    print('  %-32s %s' % (key, val))
