#!/usr/bin/env python3
"""Audit a linked kernel image for intact canonical crypto constant tables.

The prelinker rebinds arm64 ``B``/``BL`` instructions by matching their 32-bit
encoding anywhere inside a ``__TEXT_EXEC`` blob.  A word inside a constant
table can match that encoding, so a data word gets rewritten into a branch and
the primitive silently computes the wrong digest -- which is exactly how a
correctly signed dyld page can be rejected as INVALID_PAGE.

This tool is the structural counter-check: it anchors on the well-known first
words of each table, then requires every remaining word to equal the canonical
value.  Any deviation is reported with its file offset and word index, so the
report names the broken constant instead of only the symptom.

Tables are stored in memory as little-endian 32-bit words (or raw bytes for the
AES S-box), so a damaged word is reported both as a value and as the byte
sequence present in the file.

Usage:
    audit_kernel_crypto.py IMAGE [IMAGE ...] [--json] [--min-anchor 8]

Exit status is 0 when every anchored table is fully intact.
"""
import argparse
import json
import struct
import sys

SHA256_K = (
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
    0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
    0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
    0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
)

SHA256_H = (
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
)

SHA512_K = (
    0x428a2f98, 0xd728ae22, 0x71374491, 0x23ef65cd, 0xb5c0fbcf, 0xec4d3b2f,
    0xe9b5dba5, 0x8189dbbc, 0x3956c25b, 0xf348b538, 0x59f111f1, 0xb605d019,
    0x923f82a4, 0xaf194f9b, 0xab1c5ed5, 0xda6d8118, 0xd807aa98, 0xa3030242,
    0x12835b01, 0x45706fbe, 0x243185be, 0x4ee4b28c, 0x550c7dc3, 0xd5ffb4e2,
    0x72be5d74, 0xf27b896f, 0x80deb1fe, 0x3b1696b1, 0x9bdc06a7, 0x25c71235,
    0xc19bf174, 0xcf692694, 0xe49b69c1, 0x9ef14ad2, 0xefbe4786, 0x384f25e3,
    0x0fc19dc6, 0x8b8cd5b5, 0x240ca1cc, 0x77ac9c65, 0x2de92c6f, 0x592b0275,
    0x4a7484aa, 0x6ea6e483, 0x5cb0a9dc, 0xbd41fbd4, 0x76f988da, 0x831153b5,
    0x983e5152, 0xee66dfab, 0xa831c66d, 0x2db43210, 0xb00327c8, 0x98fb213f,
    0xbf597fc7, 0xbeef0ee4, 0xc6e00bf3, 0x3da88fc2, 0xd5a79147, 0x930aa725,
    0x06ca6351, 0xe003826f, 0x14292967, 0x0a0e6e70, 0x27b70a85, 0x46d22ffc,
    0x2e1b2138, 0x5c26c926, 0x4d2c6dfc, 0x5ac42aed, 0x53380d13, 0x9d95b3df,
    0x650a7354, 0x8baf63de, 0x766a0abb, 0x3c77b2a8, 0x81c2c92e, 0x47edaee6,
    0x92722c85, 0x1482353b, 0xa2bfe8a1, 0x4cf10364, 0xa81a664b, 0xbc423001,
    0xc24b8b70, 0xd0f89791, 0xc76c51a3, 0x0654be30, 0xd192e819, 0xd6ef5218,
    0xd6990624, 0x5565a910, 0xf40e3585, 0x5771202a, 0x106aa070, 0x32bbd1b8,
    0x19a4c116, 0xb8d2d0c8, 0x1e376c08, 0x5141ab53, 0x2748774c, 0xdf8eeb99,
    0x34b0bcb5, 0xe19b48a8, 0x391c0cb3, 0xc5c95a63, 0x4ed8aa4a, 0xe3418acb,
    0x5b9cca4f, 0x7763e373, 0x682e6ff3, 0xd6b2b8a3, 0x748f82ee, 0x5defb2fc,
    0x78a5636f, 0x43172f60, 0x84c87814, 0xa1f0ab72, 0x8cc70208, 0x1a6439ec,
    0x90befffa, 0x23631e28, 0xa4506ceb, 0xde82bde9, 0xbef9a3f7, 0xb2c67915,
    0xc67178f2, 0xe372532b, 0xca273ece, 0xea26619c, 0xd186b8c7, 0x21c0c207,
    0xeada7dd6, 0xcde0eb1e, 0xf57d4f7f, 0xee6ed178, 0x06f067aa, 0x72176fba,
    0x0a637dc5, 0xa2c898a6, 0x113f9804, 0xbef90dae, 0x1b710b35, 0x131c471b,
    0x28db77f5, 0x23047d84, 0x32caab7b, 0x40c72493, 0x3c9ebe0a, 0x15c9bebc,
    0x431d67c4, 0x9c100d4c, 0x4cc5d4be, 0xcb3e42b6, 0x597f299c, 0xfc657e2a,
    0x5fcb6fab, 0x3ad6faec, 0x6c44198c, 0x4a475817,
)

SHA1_K = (0x5a827999, 0x6ed9eba1, 0x8f1bbcdc, 0xca62c1d6)
SHA1_H = (0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476, 0xc3d2e1f0)

MD5_T = (
    0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee, 0xf57c0faf, 0x4787c62a,
    0xa8304613, 0xfd469501, 0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be,
    0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821, 0xf61e2562, 0xc040b340,
    0x265e5a51, 0xe9b6c7aa, 0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8,
    0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed, 0xa9e3e905, 0xfcefa3f8,
    0x676f02d9, 0x8d2a4c8a, 0xfffa3942, 0x8771f681, 0x6d9d6122, 0xfde5380c,
    0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70, 0x289b7ec6, 0xeaa127fa,
    0xd4ef3085, 0x04881d05, 0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665,
    0xf4292244, 0x432aff97, 0xab9423a7, 0xfc93a039, 0x655b59c3, 0x8f0ccc92,
    0xffeff47d, 0x85845dd1, 0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1,
    0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391,
)

AES_SBOX = bytes.fromhex(
    '637c777bf26b6fc53001672bfed7ab76ca82c97dfa5947f0add4a2af9ca472c0'
    'b7fd9326363ff7cc34a5e5f171d8311504c723c31896059a071280e2eb27b275'
    '09832c1a1b6e5aa0523bd6b329e32f8453d100ed20fcb15b6acbbe394a4c58cf'
    'd0efaafb434d338545f9027f503c9fa851a3408f929d38f5bcb6da2110fff3d2'
    'cd0c13ec5f974417c4a77e3d645d197360814fdc222a908846eeb814de5e0bdb'
    'e0323a0a4906245cc2d3ac629195e479e7c8376d8dd54ea96c56f4ea657aae08'
    'ba78252e1ca6b4c6e8dd741f4bbd8b8a703eb5664803f60e613557b986c11d9e'
    'e1f8981169d98e949b1e87e9ce5528df8ca1890dbfe6426841992d0fb054bb16')

# (name, table words/bytes as packed little-endian bytes, element size in bytes)
TABLES = [
    ('sha256_k', b''.join(struct.pack('<I', w) for w in SHA256_K), 4),
    ('sha256_h', b''.join(struct.pack('<I', w) for w in SHA256_H), 4),
    ('sha512_k', b''.join(struct.pack('<I', w) for w in SHA512_K), 4),
    ('sha1_k', b''.join(struct.pack('<I', w) for w in SHA1_K), 4),
    ('sha1_h', b''.join(struct.pack('<I', w) for w in SHA1_H), 4),
    ('md5_t', b''.join(struct.pack('<I', w) for w in MD5_T), 4),
    ('aes_sbox', AES_SBOX, 1),
]


def audit(data, anchor_words=8):
    findings = []
    for name, table, element in TABLES:
        anchor = table[:anchor_words * element]
        start = 0
        occurrences = []
        while True:
            at = data.find(anchor, start)
            if at < 0:
                break
            start = at + 1
            body = data[at:at + len(table)]
            bad = []
            for index in range(len(table) // element):
                lo = index * element
                want = table[lo:lo + element]
                got = body[lo:lo + element]
                if got != want:
                    bad.append(dict(element=index, offset=at + lo,
                                    expected=want.hex(), actual=got.hex(),
                                    expected_value=hex(struct.unpack('<I', want)[0])
                                    if element == 4 else want.hex(),
                                    actual_value=hex(struct.unpack('<I', got)[0])
                                    if element == 4 and len(got) == 4 else got.hex()))
            occurrences.append(dict(offset=at, intact=not bad, bad=bad))
        status = ('ABSENT' if not occurrences else
                  'INTACT' if any(o['intact'] for o in occurrences) else 'BROKEN')
        findings.append(dict(table=name, elements=len(table) // element,
                             occurrences=occurrences, status=status,
                             intact=status == 'INTACT'))
    return findings


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('images', nargs='+')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--min-anchor', type=int, default=8,
                    help='number of leading words used to anchor a table')
    args = ap.parse_args(argv)

    ok = True
    report = {}
    for path in args.images:
        data = open(path, 'rb').read()
        findings = audit(data, args.min_anchor)
        report[path] = findings
        say = (lambda *a: None) if args.json else print
        say('%s  (%d bytes)' % (path, len(data)))
        for item in findings:
            anchors = item['occurrences']
            corrupt = [o for o in anchors if not o['intact']]
            if item.get('status') == 'ABSENT':
                say('  %-10s ABSENT   (no copy in this image; not a failure)'
                      % item['table'])
                continue
            if item['intact'] and not corrupt:
                say('  %-10s INTACT   (%d anchor site(s): %s)'
                      % (item['table'], len(anchors),
                         ', '.join('0x%x' % o['offset'] for o in anchors[:4])))
                continue
            ok = False
            say('  %-10s BROKEN   anchors=%d intact=%s'
                  % (item['table'], len(anchors),
                     [hex(o['offset']) for o in anchors if o['intact']]))
            for occ in corrupt:
                for entry in occ['bad'][:8]:
                    print('      site 0x%x element %d: %s -> found %s (bytes %s)'
                          % (occ['offset'], entry['element'],
                             entry['expected_value'], entry['actual_value'],
                             entry['actual']))
    if args.json:
        print(json.dumps(report, indent=1))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
