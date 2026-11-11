"""Recognize the complete original 25F71 Libm log10 literal pool.

The assembler places constants and a bounded 129-pair lookup table inside
__text. A table word happens to decode as BL; it must remain data. Recognition
requires the entire pinned implementation and pool, plus its actual consumer
instructions. No symbol names, absolute addresses or opcode-shaped exceptions
are accepted as evidence.
"""
import hashlib
import struct


_POOL_PROLOGUE = bytes.fromhex('00000000001018c00ee526157bcbdb3f')
_POOL_BYTES = 0x888
_CODE_BYTES = 0x170
_POOL_SHA256 = '747d8fd1b75aaa671ce2a5b32576235f4ac560f9497aa1b6dc8d7a5f8a736632'
_IMPLEMENTATION_SHA256 = '13d22f33f3487eb0e101828ebab25fffdb4fc1535ff9b496c499b0bc1dcca8aa'
_ANCHORS = {
    0x24: 0x58000a63,  # ldr x3, first scalar literal
    0x30: 0xd2c20001,  # mov x1, 2**44
    0x34: 0x9240cc00,  # and x0, x0, 2**52-1
    0x38: 0x8b010002,  # add x2, x0, x1
    0x3c: 0xd36dfc42,  # lsr x2, x2, 45: index 0..128
    0x4c: 0x10000b61,  # adr x1, polynomial constants
    0x50: 0x0cdf2c30,  # ld1 four doubles, postincrement 32
    0x54: 0x6cc15434,  # ldp two doubles, postincrement 16
    0x58: 0x8b021021,  # add x1, x1, x2, lsl 4
    0x5c: 0x6cc10c22,  # ldp selected coefficient pair
    0x16c: 0xd65f03c0,  # final ret immediately before literal pool
}


def _signed(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def libm_literal_tables(data, segments):
    """Return proven exclusions in prelink reachable_instructions format.

    Segment/section dictionaries use prelink_kcg_bind.read_segments fields.
    Candidate offsets are found from bytes; every claimed range must be fully
    backed by its executable section. A changed implementation returns none.
    """
    excluded = []
    for segment in segments:
        for section in segment['sections']:
            if not section['flags'] & 0x80000400:
                continue
            lo = section['offset']
            hi = lo + section['size']
            if lo < 0 or hi > len(data):
                continue
            at = data.find(_POOL_PROLOGUE, lo, hi)
            while at >= 0:
                code = at - _CODE_BYTES
                end = at + _POOL_BYTES
                if code >= lo and end <= hi:
                    pool = data[at:end]
                    implementation = data[code:end]
                    if (hashlib.sha256(pool).hexdigest() == _POOL_SHA256 and
                            hashlib.sha256(implementation).hexdigest() == _IMPLEMENTATION_SHA256 and
                            all(struct.unpack_from('<I', data, code + offset)[0] == word
                                for offset, word in _ANCHORS.items())):
                        pool_va = section['addr'] + at - lo
                        code_va = pool_va - _CODE_BYTES
                        # Confirm the literal and ADR consumers point at this
                        # pool, and the maximum indexed pair ends at its end.
                        literal = _ANCHORS[0x24]
                        literal_va = code_va + 0x24 + _signed((literal >> 5) & 0x7ffff, 19) * 4
                        adr = _ANCHORS[0x4c]
                        immediate = ((adr >> 5) & 0x7ffff) << 2 | (adr >> 29) & 3
                        polynomial_va = code_va + 0x4c + _signed(immediate, 21)
                        table_va = polynomial_va + 32 + 16
                        if (literal_va == pool_va and polynomial_va == pool_va + 0x48 and
                                table_va + 129 * 16 == pool_va + _POOL_BYTES):
                            excluded.append(dict(
                                start=pool_va, end=pool_va + _POOL_BYTES,
                                section=section['name'], sha256=_POOL_SHA256,
                                kind='complete-pinned-25F71-libm-log10-literals',
                                implementation_sha256=_IMPLEMENTATION_SHA256,
                                consumer_start=code_va, consumer_end=pool_va,
                                scalar_constants_bytes=0x48,
                                polynomial_constants_bytes=48,
                                indexed_table_start=table_va,
                                indexed_table_entries=129, indexed_entry_bytes=16))
                at = data.find(_POOL_PROLOGUE, at + 4, hi)
    return excluded
