"""Independent ADRP-pair verifier.

Re-decodes every recorded ADRP rewrite with Capstone (not with the binder's own
decoder, which is what shipped the LDR-Q scaling bug) and recomputes the
effective address of the completing instruction.

    python3 verify_adrp_independent.py IMAGE REPORT

Exit status 0 means every recorded pair reaches its recorded target.
"""

import json
import struct
import sys

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM

MD = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
MD.detail = True


def segments(image):
    count, = struct.unpack_from('<I', image, 16)
    offset = 32
    out = []
    for _ in range(count):
        command, size = struct.unpack_from('<II', image, offset)
        if command == 0x19:
            name = image[offset + 8:offset + 24].rstrip(b'\0').decode()
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from(
                '<QQQQ', image, offset + 24)
            out.append((name, vmaddr, vmsize, fileoff, filesize))
        offset += size
    return out


def file_position(segs, va, length=4):
    for _, vmaddr, _, fileoff, filesize in segs:
        if vmaddr <= va < vmaddr + filesize and va + length <= vmaddr + filesize:
            return fileoff + (va - vmaddr)
    return None


def decode_one(image, segs, va):
    position = file_position(segs, va)
    if position is None:
        return None
    insn = next(MD.disasm(image[position:position + 4], va), None)
    return insn


def effective(image, segs, site, pair_site):
    adrp = decode_one(image, segs, site)
    following = decode_one(image, segs, pair_site)
    if adrp is None or following is None:
        return None, 'undecodable'
    if adrp.mnemonic != 'adrp':
        return None, 'not an adrp (%s)' % adrp.mnemonic
    page = adrp.operands[1].imm & 0xFFFFFFFFFFFFFFFF
    target_register = adrp.operands[0].reg
    if not following.operands:
        return None, 'pair has no operands'
    if (following.mnemonic.startswith('add')
            and len(following.operands) == 3
            and following.operands[1].type == 1                 # ARM64_OP_REG
            and following.operands[1].reg == target_register):
        return (page + following.operands[2].imm) & 0xFFFFFFFFFFFFFFFF, None
    memory = next((o for o in following.operands if o.type == 3), None)
    if memory is None:                              # ARM64_OP_MEM
        return None, 'pair does not address memory (%s)' % following.mnemonic
    if memory.mem.base != target_register:
        return None, 'pair base register is not the adrp register'
    return (page + memory.mem.disp) & 0xFFFFFFFFFFFFFFFF, None


def main(argv):
    image_path, report_path = argv[1], argv[2]
    image = open(image_path, 'rb').read()
    segs = segments(image)
    report = json.load(open(report_path))
    entries = [e for e in report['branch_relocations']
               if str(e.get('kind', '')).startswith('adrp')]
    bad = []
    for entry in entries:
        site = int(entry['site'], 16)
        pair_site = int(entry['pair_site'], 16)
        want = int(entry['new_target'], 16)
        got, reason = effective(image, segs, site, pair_site)
        if got is None or got != want:
            bad.append((entry.get('component'), hex(site), hex(pair_site),
                        hex(want), hex(got) if got is not None else reason))
    print('capstone: %d recorded adrp pairs, %d mismatched' % (len(entries), len(bad)))
    for row in bad[:20]:
        print('   component=%s site=%s pair=%s want=%s got=%s' % row)
    return 0 if not bad else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
