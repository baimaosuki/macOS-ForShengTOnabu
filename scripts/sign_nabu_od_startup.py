#!/usr/bin/env python3
"""Ad-hoc sign only the exact OD queue-fence candidate, without macOS tools.

Keep original XML/DER entitlement blobs and their special-slot hashes. Drop
the original CMS and requirements (which identify Apple's signer), set ADHOC,
and recompute all executable page hashes. A paired owner trust-cache entry is
still required; this does not create an Apple platform signature.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import tempfile

from native_macho import macho
from prepare_nabu_od_startup import INPUT_SHA256, digest, patch, thin_slice


def signature_blobs(data):
    image = macho(data, 'arm64e')
    commands = [at for cmd, at, length in image['commands'] if cmd == 0x1D and length == 16]
    if len(commands) != 1:
        raise ValueError('requires exactly one LC_CODE_SIGNATURE')
    offset, size = struct.unpack_from('<II', data, commands[0] + 8)
    if offset + size != len(data) or size < 12:
        raise ValueError('signature must occupy the original final file extent')
    magic, length, count = struct.unpack_from('>III', data, offset)
    if magic != 0xFADE0CC0 or not 12 + count * 8 <= length <= size:
        raise ValueError('invalid signature SuperBlob')
    blobs, extents = {}, []
    for index in range(count):
        kind, start = struct.unpack_from('>II', data, offset + 12 + index * 8)
        if start < 12 + count * 8 or start + 8 > length:
            raise ValueError('invalid signature index')
        extent = struct.unpack_from('>I', data, offset + start + 4)[0]
        if extent < 8 or start + extent > length or kind in blobs:
            raise ValueError('invalid/duplicate signature blob')
        if any(start < b and a < start + extent for a, b in extents):
            raise ValueError('overlapping signature blobs')
        extents.append((start, start + extent))
        blobs[kind] = data[offset + start:offset + start + extent]
    return offset, size, blobs


def sign(original, candidate):
    expected, patch_report = patch(original)
    if candidate != expected:
        raise ValueError('input is not the exact OD freshcreate queue-fence candidate')
    start, size = thin_slice(candidate)
    raw = candidate[start:start + size]
    offset, sigsize, source = signature_blobs(raw)
    if set(source) != {0, 2, 5, 7, 0x10000}:
        raise ValueError('original 25F71 signature blob set differs')
    cd = bytearray(source[0])
    magic, cdlen, version, flags, hashes, ident, special, pages, limit = struct.unpack_from('>9I', cd)
    hsize, htype, platform, power = struct.unpack_from('>4B', cd, 36)
    if (magic, cdlen, version, flags, special, pages, limit, hsize, htype, power) != (
            0xFADE0C02, len(cd), 0x20500, 0x10000, 7, 263, offset, 32, 2, 12):
        raise ValueError('exact 25F71 CodeDirectory layout differs')
    if hashes < special * hsize or hashes + pages * hsize != len(cd):
        raise ValueError('CodeDirectory slot extents differ')
    if struct.unpack_from('>I', cd, 44)[0] or struct.unpack_from('>Q', cd, 56)[0]:
        raise ValueError('scatter/64-bit codeLimit unsupported for this exact image')
    for slot in (5, 7):
        expected_hash = hashlib.sha256(source[slot]).digest()
        if cd[hashes - slot * hsize:hashes - (slot - 1) * hsize] != expected_hash:
            raise ValueError('original entitlement special-slot hash differs')
    struct.pack_into('>I', cd, 12, flags | 2)  # preserve runtime; identify owner ADHOC
    # The original requirement is anchored to Apple. Remove it, and clear its
    # special hash rather than make an unfulfillable signer identity claim.
    cd[hashes - 2 * hsize:hashes - hsize] = bytes(hsize)
    for slot in range(pages):
        begin = slot << power
        cd[hashes + slot * hsize:hashes + (slot + 1) * hsize] = hashlib.sha256(raw[begin:min(begin + (1 << power), limit)]).digest()
    kept = {0: bytes(cd), 5: source[5], 7: source[7]}
    head = 12 + len(kept) * 8
    indexes, payload = [], bytearray()
    for kind, blob in sorted(kept.items()):
        indexes.append(struct.pack('>II', kind, head + len(payload)))
        payload.extend(blob)
    length = head + len(payload)
    if length > sigsize:
        raise ValueError('ad-hoc signature does not fit original extent')
    signature = struct.pack('>III', 0xFADE0CC0, length, len(kept)) + b''.join(indexes) + bytes(payload)
    signature += bytes(sigsize - len(signature))
    signed_slice = raw[:offset] + signature
    output = candidate[:start] + signed_slice + candidate[start + size:]
    _, _, check = signature_blobs(signed_slice)
    if check[5] != source[5] or check[7] != source[7] or 0x10000 in check:
        raise ValueError('entitlements or CMS removal verification failed')
    from verify_page_hashes import verify
    from codesign_dump import dump
    with tempfile.TemporaryDirectory(prefix='nabu-od-sign-verify-') as directory:
        verified_path = Path(directory) / 'opendirectoryd'
        verified_path.write_bytes(signed_slice)
        pages_verified = verify(str(verified_path))
        signature_verified = dump(verified_path)
    if not pages_verified.get('signed') or not pages_verified.get('ok') or pages_verified.get('code_slots') != pages:
        raise ValueError('independent executable-page signature verification failed')
    cdhash = hashlib.sha256(cd).hexdigest()[:40]
    directories = signature_verified['code_directories']
    if (pages_verified['cdhash'][:40] != cdhash or len(directories) != 1 or
        directories[0]['cdhash'][:40] != cdhash or not directories[0]['adhoc']):
        raise ValueError('independent ad-hoc signature identity verification failed')
    pages_verified.pop('path', None)
    # Slot -1 is Info.plist, not a stored self-hash of the CodeDirectory. The
    # legacy verifier exposes this extra comparison; it is not signature QA.
    pages_verified.pop('cdhash_ok', None)
    pages_verified.pop('cdhash_stored', None)
    signature_verified.pop('path', None)
    return output, dict(patch=patch_report, original_slice_sha256=INPUT_SHA256,
        signed_slice_sha256=digest(signed_slice), sha256=digest(output),
        cdhash=cdhash, hash_type=2,
        cd_flags=flags | 2, xml_entitlements_sha256=digest(check[5]),
        der_entitlements_sha256=digest(check[7]), entitlement_blobs_unchanged=True,
        original_entitlements_preserved=True, signature_valid=True,
        signature_valid_scope='All executable pages and embedded XML/DER hashes verified; trust-cache deployment still required.',
        page_hashes=pages_verified, signature=signature_verified,
        cms_removed=True, apple_signer_requirements_removed=True,
        owner_adhoc=True, apple_platform_signature=False,
        requires_paired_trustcache=True, device_tested=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.report.exists():
        raise ValueError('refusing to overwrite signed output/evidence')
    output, report = sign(args.original.read_bytes(), args.candidate.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(output)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
