#!/usr/bin/env python3
"""Reference model and vector generator for the nabu ARMv8.2 crypto
instruction emulation.

Ground truth:
  * FEAT_SHA3 / FEAT_SHA512 semantics: Arm ARM (2026-03_rel) pseudo-code,
    as published for EOR3, BCAX, RAX1, XAR, SHA512H, SHA512H2,
    SHA512SU0 and SHA512SU1.
  * The SHA-512 primitives used by the round-conformance vectors are
    validated end to end against hashlib.sha512 below, so those vectors do
    not depend on the pseudo-code transcription.
"""
import hashlib
import random

M64 = (1 << 64) - 1


# ---------------------------------------------------------------- SHA-512
def rotr(x, n):
    n %= 64
    x &= M64
    return x if n == 0 else ((x >> n) | (x << (64 - n))) & M64


def rotl(x, n):
    n %= 64
    x &= M64
    return x if n == 0 else ((x << n) | (x >> (64 - n))) & M64


def sha_ch(e, f, g):
    return (e & f) ^ ((~e & M64) & g)


def sha_maj(a, b, c):
    return (a & b) ^ (a & c) ^ (b & c)


def sha_big_sigma0(x):           # FIPS 180-4 section 4.1.3
    return rotr(x, 28) ^ rotr(x, 34) ^ rotr(x, 39)


def sha_big_sigma1(x):
    return rotr(x, 14) ^ rotr(x, 18) ^ rotr(x, 41)


def sha_small_sigma0(x):
    return rotr(x, 1) ^ rotr(x, 8) ^ (x >> 7)


def sha_small_sigma1(x):
    return rotr(x, 19) ^ rotr(x, 61) ^ (x >> 6)


def _iroot(n, k):
    """floor(n ** (1/k)) for integers."""
    if n < 2:
        return n
    x = 1 << ((n.bit_length() + k - 1) // k)
    while True:
        y = ((k - 1) * x + n // x ** (k - 1)) // k
        if y >= x:
            return x
        x = y


def _primes(count):
    primes = []
    n = 2
    while len(primes) < count:
        if all(n % p for p in primes if p * p <= n):
            primes.append(n)
        n += 1
    return primes


def _fips512_constants():
    """K[i] = frac(cbrt(p_i)) * 2**64, IV[i] = frac(sqrt(p_i)) * 2**64."""
    primes = _primes(80)
    k = [(_iroot(p << 192, 3) & M64) for p in primes]
    iv = [(_iroot(p << 128, 2) & M64) for p in primes[:8]]
    assert k[0] == 0x428a2f98d728ae22, hex(k[0])
    assert k[79] == 0x6c44198c4a475817, hex(k[79])
    assert iv[0] == 0x6a09e667f3bcc908, hex(iv[0])
    assert iv[7] == 0x5be0cd19137e2179, hex(iv[7])
    return k, iv


K512, IV512 = _fips512_constants()


def sha512_compress(state, block):
    """Textbook FIPS 180-4 SHA-512 compression, used to validate the
    Sigma/Ch/Maj primitives against hashlib."""
    w = [int.from_bytes(block[i * 8:(i + 1) * 8], 'big') for i in range(16)]
    for t in range(16, 80):
        w.append((sha_small_sigma1(w[t - 2]) + w[t - 7] +
                  sha_small_sigma0(w[t - 15]) + w[t - 16]) & M64)
    a, b, c, d, e, f, g, h = state
    for t in range(80):
        t1 = (h + sha_big_sigma1(e) + sha_ch(e, f, g) + K512[t] + w[t]) & M64
        t2 = (sha_big_sigma0(a) + sha_maj(a, b, c)) & M64
        h, g, f, e, d, c, b, a = g, f, e, (d + t1) & M64, c, b, a, (t1 + t2) & M64
    return [(x + y) & M64 for x, y in zip(state, [a, b, c, d, e, f, g, h])]


def sha512_digest(data):
    state = list(IV512)
    msg = bytearray(data)
    length = len(msg) * 8
    msg.append(0x80)
    while len(msg) % 128 != 112:
        msg.append(0)
    msg += length.to_bytes(16, 'big')
    for i in range(0, len(msg), 128):
        state = sha512_compress(state, msg[i:i + 128])
    return b''.join(x.to_bytes(8, 'big') for x in state)


def validate_reference():
    for text in (b'', b'abc', b'a' * 111, b'a' * 112, b'a' * 1000,
                 bytes(range(256))):
        mine = sha512_digest(text)
        theirs = hashlib.sha512(text).digest()
        assert mine == theirs, 'SHA-512 reference does not match hashlib'
    return True


# -------------------------------------------------------------- instruction
def read_round(a, b, c, d, e, f, g, h):
    t1 = (h + sha_big_sigma1(e) + sha_ch(e, f, g)) & M64
    t2 = (sha_big_sigma0(a) + sha_maj(a, b, c)) & M64
    return t1, t2


class Vec:
    __slots__ = ('lo', 'hi')

    def __init__(self, lo, hi):
        self.lo = lo & M64
        self.hi = hi & M64

    def __repr__(self):
        return 'Vec(0x%016x, 0x%016x)' % (self.lo, self.hi)


def rand_vec(rng):
    return Vec(rng.getrandbits(64), rng.getrandbits(64))


# Arm ARM pseudo-code, transcribed from the published instruction pages.
def ref_sha512h(vn, vm, vd):
    tmp_hi = (sha_ch(vm.hi, vn.lo, vn.hi) + sha_big_sigma1(vm.hi) + vd.hi) & M64
    tmp = (tmp_hi + vm.lo) & M64
    tmp_lo = (sha_ch(tmp, vm.hi, vn.lo) + sha_big_sigma1(tmp) + vd.lo) & M64
    return Vec(tmp_lo, tmp_hi)


def ref_sha512h2(vn, vm, vd):
    tmp_hi = (sha_maj(vn.lo, vm.hi, vm.lo) + sha_big_sigma0(vm.lo) + vd.hi) & M64
    tmp_lo = (sha_maj(tmp_hi, vm.lo, vm.hi) + sha_big_sigma0(tmp_hi) + vd.lo) & M64
    return Vec(tmp_lo, tmp_hi)


def ref_sha512su0(vn, vd):
    lo = (vd.lo + sha_small_sigma0(vd.hi)) & M64
    hi = (vd.hi + sha_small_sigma0(vn.lo)) & M64
    return Vec(lo, hi)


def ref_sha512su1(vn, vm, vd):
    hi = (vd.hi + sha_small_sigma1(vn.hi) + vm.hi) & M64
    lo = (vd.lo + sha_small_sigma1(vn.lo) + vm.lo) & M64
    return Vec(lo, hi)


# ------------------------------------------------------------- encodings
def enc_eor3(rd, rn, rm, ra):
    return 0xce000000 | (rm << 16) | (ra << 10) | (rn << 5) | rd


def enc_bcax(rd, rn, rm, ra):
    return 0xce200000 | (rm << 16) | (ra << 10) | (rn << 5) | rd


def enc_rax1(rd, rn, rm):
    return 0xce608c00 | (rm << 16) | (rn << 5) | rd


def enc_xar(rd, rn, rm, imm6):
    return 0xce800000 | (rm << 16) | ((imm6 & 0x3f) << 10) | (rn << 5) | rd


def enc_sha512h(rd, rn, rm):
    return 0xce608000 | (rm << 16) | (rn << 5) | rd


def enc_sha512h2(rd, rn, rm):
    return 0xce608400 | (rm << 16) | (rn << 5) | rd


def enc_sha512su0(rd, rn):
    return 0xcec08000 | (rn << 5) | rd


def enc_sha512su1(rd, rn, rm):
    return 0xce608800 | (rm << 16) | (rn << 5) | rd


def gen(seed=0x6e616275, per_op=128):
    """Return a list of (op, insn, vd, vn, vm, va, expected) tuples."""
    rng = random.Random(seed)
    regs = list(range(32))
    out = []

    def reg3():
        return rng.sample(regs, 3)

    for _ in range(per_op):
        rd, rn, rm, ra = rng.sample(regs, 4)
        vd, vn, vm, va = (rand_vec(rng) for _ in range(4))
        exp = Vec(vn.lo ^ vm.lo ^ va.lo, vn.hi ^ vm.hi ^ va.hi)
        out.append(('eor3', enc_eor3(rd, rn, rm, ra), vd, vn, vm, va, exp))

        rd, rn, rm, ra = rng.sample(regs, 4)
        vd, vn, vm, va = (rand_vec(rng) for _ in range(4))
        exp = Vec(vn.lo ^ (vm.lo & ~va.lo), vn.hi ^ (vm.hi & ~va.hi))
        out.append(('bcax', enc_bcax(rd, rn, rm, ra), vd, vn, vm, va, exp))

        rd, rn, rm = reg3()
        vd, vn, vm, va = (rand_vec(rng) for _ in range(4))
        exp = Vec(vn.lo ^ rotl(vm.lo, 1), vn.hi ^ rotl(vm.hi, 1))
        out.append(('rax1', enc_rax1(rd, rn, rm), vd, vn, vm, va, exp))

        rd, rn, rm = reg3()
        imm6 = rng.randrange(64)
        vd, vn, vm, va = (rand_vec(rng) for _ in range(4))
        exp = Vec(rotr(vn.lo ^ vm.lo, imm6), rotr(vn.hi ^ vm.hi, imm6))
        out.append(('xar', enc_xar(rd, rn, rm, imm6), vd, vn, vm, va, exp))

        rd, rn, rm = reg3()
        vd, vn, vm, va = (rand_vec(rng) for _ in range(4))
        out.append(('sha512h', enc_sha512h(rd, rn, rm), vd, vn, vm, va,
                    ref_sha512h(vn, vm, vd)))

        rd, rn, rm = reg3()
        vd, vn, vm, va = (rand_vec(rng) for _ in range(4))
        out.append(('sha512h2', enc_sha512h2(rd, rn, rm), vd, vn, vm, va,
                    ref_sha512h2(vn, vm, vd)))

        rd, rn = rng.sample(regs, 2)
        vd, vn, vm, va = (rand_vec(rng) for _ in range(4))
        out.append(('sha512su0', enc_sha512su0(rd, rn), vd, vn, vm, va,
                    ref_sha512su0(vn, vd)))

        rd, rn, rm = reg3()
        vd, vn, vm, va = (rand_vec(rng) for _ in range(4))
        out.append(('sha512su1', enc_sha512su1(rd, rn, rm), vd, vn, vm, va,
                    ref_sha512su1(vn, vm, vd)))

    # ---- round-conformance vectors: expected values come from the FIPS
    # 180-4 round function (validated against hashlib), not from the
    # instruction pseudo-code.
    for _ in range(64):
        a, b, c, d, e, f, g, h = (rng.getrandbits(64) for _ in range(8))
        k0, k1 = rng.getrandbits(64), rng.getrandbits(64)
        w0, w1 = rng.getrandbits(64), rng.getrandbits(64)

        # Vn = (f, g), Vm = (d, e), Vd = (g+K1+W1, h+K0+W0)
        vn = Vec(f, g)
        vm = Vec(d, e)
        vd = Vec((g + k1 + w1) & M64, (h + k0 + w0) & M64)
        t1_r0 = (h + sha_big_sigma1(e) + sha_ch(e, f, g) + k0 + w0) & M64
        e1 = (d + t1_r0) & M64
        t1_r1 = (g + sha_big_sigma1(e1) + sha_ch(e1, e, f) + k1 + w1) & M64
        exp = Vec(t1_r1, t1_r0)
        assert ref_sha512h(vn, vm, vd).lo == t1_r1
        assert ref_sha512h(vn, vm, vd).hi == t1_r0
        out.append(('sha512h-round', enc_sha512h(0, 1, 2), vd, vn, vm,
                    Vec(0, 0), exp))

        # Vn = (c, -), Vm = (a, b), Vd = (w.lo, 0).
        # High lane: T2 of the FIPS round.  Low lane: T2 of the shifted state
        # (a' = T2, b' = a, c' = b) plus the round-key material in Vd.
        t2_r0 = (sha_big_sigma0(a) + sha_maj(a, b, c)) & M64
        w_lo = rng.getrandbits(64)
        t2_shift = (sha_big_sigma0(t2_r0) + sha_maj(t2_r0, a, b) + w_lo) & M64
        vn = Vec(c, 0)
        vm = Vec(a, b)
        vd = Vec(w_lo, 0)
        exp = Vec(t2_shift, t2_r0)
        assert ref_sha512h2(vn, vm, vd).lo == t2_shift
        assert ref_sha512h2(vn, vm, vd).hi == t2_r0
        out.append(('sha512h2-round', enc_sha512h2(3, 4, 5), vd, vn, vm,
                    Vec(0, 0), exp))

    return out


def write_vectors(path, cases):
    with open(path, 'w') as fh:
        fh.write('# op insn vd_lo vd_hi vn_lo vn_hi vm_lo vm_hi va_lo va_hi '
                 'exp_lo exp_hi\n')
        for op, insn, vd, vn, vm, va, exp in cases:
            fh.write('%s %08x %016x %016x %016x %016x %016x %016x %016x %016x '
                     '%016x %016x\n' %
                     (op, insn, vd.lo, vd.hi, vn.lo, vn.hi, vm.lo, vm.hi,
                      va.lo, va.hi, exp.lo, exp.hi))


if __name__ == '__main__':
    import sys
    from collections import Counter

    path = sys.argv[1] if len(sys.argv) > 1 else 'vectors.txt'
    per_op = int(sys.argv[2]) if len(sys.argv) > 2 else 128
    validate_reference()
    print('reference SHA-512 matches hashlib: OK')
    cases = gen(per_op=per_op)
    write_vectors(path, cases)
    counts = Counter(c[0] for c in cases)
    for name in sorted(counts):
        print('%-16s %d vectors' % (name, counts[name]))
    print('%d vectors written to %s' % (len(cases), path))
