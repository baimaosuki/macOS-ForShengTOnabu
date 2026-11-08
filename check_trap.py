import struct
import sys

IMG = sys.argv[1] if len(sys.argv) > 1 else '/home/baimao/nabu_macos/.work/out/kernel.panictrap.nabu'
d = open(IMG, 'rb').read()

ncmds, = struct.unpack_from('<I', d, 16)
q = 32
sym = None
segs = []
for _ in range(ncmds):
    cmd, sz = struct.unpack_from('<II', d, q)
    if cmd == 0x19:
        nm = d[q + 8:q + 24].rstrip(b'\0').decode()
        vm, vs, fo, fs = struct.unpack_from('<QQQQ', d, q + 24)
        segs.append((nm, vm, vs, fo, fs))
    elif cmd == 0x2:
        symoff, nsyms, stroff, strsize = struct.unpack_from('<IIII', d, q + 8)
        sym = (symoff, nsyms, stroff, strsize)
    q += sz

print('segments:', len(segs), 'symtab:', bool(sym))

if sym:
    symoff, nsyms, stroff, strsize = sym
    target = None
    for i in range(nsyms):
        at = symoff + i * 16
        strx, = struct.unpack_from('<I', d, at)
        if not strx or strx >= strsize:
            continue
        end = d.find(b'\0', stroff + strx)
        name = d[stroff + strx:end].decode('ascii', 'replace')
        if name in ('_sheng_bootstrap_capture_enabled', 'sheng_bootstrap_capture_enabled'):
            value, = struct.unpack_from('<Q', d, at + 8)
            target = (name, value)
            break
    print('capture flag symbol:', target)

    if target:
        page = target[1] & ~0xFFF
        off = target[1] & 0xFFF
        # count adrp instructions whose page == page, followed within 3 instrs by ldr w,[x,#off]
        hits = 0
        for nm, vm, vs, fo, fs in segs:
            if nm not in ('__TEXT_EXEC', '__TEXT'):
                continue
            body = d[fo:fo + fs]
            for i in range(0, len(body) - 16, 4):
                w, = struct.unpack_from('<I', body, i)
                if (w & 0x9F000000) != 0x90000000:
                    continue
                imm = ((w >> 5) & 0x7FFFF) << 2 | ((w >> 29) & 3)
                if imm & (1 << 20):
                    imm -= 1 << 21
                pc = vm + i
                if ((pc & ~0xFFF) + (imm << 12)) == page:
                    rd = w & 0x1F
                    hits += 1
                    if hits <= 3:
                        print('  adrp at %#x -> x%d, page %#x' % (pc, rd, page))
        print('adrp references to that page:', hits)

# sanity: does the image carry the trap's immediate pair for 0x0000fffffffffff8 ?
lo = 0x0000FFFFFFFFFFF8
words = {
    'movz lo16': 0xD2A00000 | ((lo & 0xFFFF) << 5) | 16,
    'movz hi16': 0xD2A00000 | (((lo >> 16) & 0xFFFF) << 5) | 16,
}
for k, w in words.items():
    print(k, hex(w), 'present:', struct.pack('<I', w) in d)
