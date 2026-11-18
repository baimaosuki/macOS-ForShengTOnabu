#!/usr/bin/env python3
"""macOS adapter for the pinned zeta ordinary-pointer ABI.

Code proof and instruction lowering are upstream zeta. This adapter selects
an arm64e slice from a universal executable and replaces its CMS signature
with an ad-hoc SHA-256/4K signature, retaining signed entitlement metadata.
Inputs are read-only; original trust-cache membership is mandatory.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys

VENDOR = Path(__file__).with_name('zeta_pac')
sys.path.insert(0, str(VENDOR))

def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, VENDOR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

upstream = module('zeta_rewriter', 'rewrite-zeta-userland.py')
ABI = 'zeta-ordinary-v1'
PIN = 'eb489285a1d9153872f00c93ebb519a269b249b5'
CD_MAGIC, SUPER_MAGIC, ADHOC = 0xfade0c02, 0xfade0cc0, 2


def require(condition, message):
    if not condition:
        raise ValueError(message)


def arm64e_slice(data):
    require(len(data) >= 32, 'truncated executable')
    if data[:4] == b'\xcf\xfa\xed\xfe':
        upstream.parse(data)
        return data, dict(offset=0, size=len(data), universal=False)
    magic = struct.unpack_from('>I', data)[0]
    require(magic in (0xcafebabe, 0xcafebabf), 'unsupported executable container')
    count = struct.unpack_from('>I', data, 4)[0]
    width = 32 if magic == 0xcafebabf else 20
    require(0 < count <= 64 and 8 + width * count <= len(data), 'invalid universal architecture table')
    selected, ranges = [], []
    for i in range(count):
        at = 8 + width * i
        cpu, subtype = struct.unpack_from('>II', data, at)
        off, size = struct.unpack_from('>QQ' if width == 32 else '>II', data, at + 8)
        require(off >= 8 + width * count and size >= 32 and off + size <= len(data), 'universal slice out of bounds')
        require(not any(off < end and begin < off + size for begin, end in ranges), 'overlapping universal slices')
        ranges.append((off, off + size))
        if cpu == 0x100000c and subtype & 0xffffff == 2:
            selected.append((off, size))
    require(len(selected) == 1, 'require exactly one supported arm64e ABI slice')
    off, size = selected[0]
    thin = data[off:off + size]
    upstream.parse(thin)
    return thin, dict(offset=off, size=size, universal=True)


def signature_blobs(signature):
    require(len(signature) >= 12, 'truncated signature')
    magic, length, count = struct.unpack_from('>III', signature)
    require(magic == SUPER_MAGIC and 12 + 8 * count <= length <= len(signature), 'bad signature superblob')
    blobs, ranges = {}, []
    for i in range(count):
        slot, at = struct.unpack_from('>II', signature, 12 + 8 * i)
        require(slot not in blobs and at >= 12 + 8 * count and at + 8 <= length, 'invalid signature index')
        _, size = struct.unpack_from('>II', signature, at)
        require(size >= 8 and at + size <= length, 'signature blob out of bounds')
        require(not any(at < end and start < at + size for start, end in ranges), 'overlapping signature blobs')
        ranges.append((at, at + size))
        blobs[slot] = signature[at:at + size]
    return blobs


def selected_directory(signature):
    blobs = signature_blobs(signature)
    directories = []
    for slot, blob in blobs.items():
        if struct.unpack_from('>I', blob)[0] != CD_MAGIC:
            continue
        require(len(blob) >= 44, 'short CodeDirectory')
        if blob[36:38] == b'\x20\x02':
            directories.append((slot, blob))
    require(len(directories) == 1, 'require exactly one original SHA-256 CodeDirectory')
    return directories[0][1], blobs


def validate_original(code, signature):
    cd, blobs = selected_directory(signature)
    _, _, version, _, hashes, _, special, slots, limit = struct.unpack_from('>9I', cd)
    shift = cd[39]
    require(shift in (12, 14), 'unsupported signature page size')
    if version >= 0x20300 and struct.unpack_from('>Q', cd, 56)[0]:
        limit = struct.unpack_from('>Q', cd, 56)[0]
    require(limit == len(code) and slots == (limit + (1 << shift) - 1) >> shift, 'signature code limit/slots mismatch')
    require(hashes >= special * 32 and hashes + slots * 32 <= len(cd), 'signature hashes out of bounds')
    require(version < 0x20100 or struct.unpack_from('>I', cd, 44)[0] == 0, 'scatter CodeDirectory unsupported')
    for i in range(slots):
        require(hashlib.sha256(code[i << shift:min((i + 1) << shift, limit)]).digest() == cd[hashes + i * 32:hashes + (i + 1) * 32], f'original code page {i} hash mismatch')
    for slot, blob in blobs.items():
        if 0 < slot <= special:
            require(hashlib.sha256(blob).digest() == cd[hashes - slot * 32:hashes - (slot - 1) * 32], f'original special slot {slot} hash mismatch')
    return hashlib.sha256(cd).digest()[:20], cd, blobs


def rebuilt_signature(code, signature, page_shift=12):
    """Ad-hoc signature retaining the selected CD prefix and special blobs.

    Original CMS and alternate CodeDirectories cannot authenticate changed
    bytes. Entitlements, requirements and launch constraints are retained;
    the original special-slot hashes remain valid because those blobs do not
    change. The caller sets all load/header sizes before hashing code.
    """
    cd, blobs = selected_directory(signature)
    hashes = struct.unpack_from('>I', cd, 16)[0]
    require(44 <= hashes <= len(cd), 'invalid CodeDirectory hash offset')
    slots = (len(code) + (1 << page_shift) - 1) >> page_shift
    new_cd = bytearray(cd[:hashes]) + bytearray(slots * 32)
    struct.pack_into('>I', new_cd, 4, len(new_cd))
    flags = struct.unpack_from('>I', new_cd, 12)[0]
    struct.pack_into('>I', new_cd, 12, flags | ADHOC)
    struct.pack_into('>II', new_cd, 28, slots, len(code))
    new_cd[39] = page_shift
    if struct.unpack_from('>I', new_cd, 8)[0] >= 0x20300:
        struct.pack_into('>Q', new_cd, 56, 0)
    for i in range(slots):
        new_cd[hashes + 32 * i:hashes + 32 * (i + 1)] = hashlib.sha256(code[i << page_shift:min((i + 1) << page_shift, len(code))]).digest()
    kept = {slot: blob for slot, blob in blobs.items()
            if slot != 0x10000 and struct.unpack_from('>I', blob)[0] != CD_MAGIC}
    kept[0] = bytes(new_cd)
    cursor = 12 + 8 * len(kept)
    index, body = [], bytearray()
    for slot, blob in sorted(kept.items()):
        index.append(struct.pack('>II', slot, cursor))
        body += blob
        cursor += len(blob)
    output = struct.pack('>III', SUPER_MAGIC, cursor, len(kept)) + b''.join(index) + body
    return output, hashlib.sha256(new_cd).hexdigest()[:40]


def lower_proven(data, info):
    """Use zeta's code proof; retain unproven words just as its cache tool does.

    Retained real PAC code uses the matching identity/zero kernel fallback,
    never the previous canonicalization/XOR ABI. Unsupported load offsets
    stay on the fault-aware fallback instead of changing their semantics.
    """
    sections = upstream.instruction_sections(info)
    if not sections:
        return [], dict(proven_instructions=0, unproven_pac_words=0, fallback_instructions=[])
    decoder, proven, table_data, tables, anomalies, roots = upstream.code_proof.prove(
        data, info, sections, upstream.va_to_off(info), upstream.code_proof.llvm_oracle, gaps=True)
    dice = {at for off, size, _ in info['dice'] for at in range(off, off + size, 4)}
    edits, fallback, unproven, kept = [], [], 0, Counter()
    for section in sections:
        for address in range(section['addr'], section['addr'] + section['size'], 4):
            off = section['offset'] + address - section['addr']
            word = struct.unpack_from('<I', data, off)[0]
            try:
                lowered = upstream.pauth.lower(word)
            except ValueError as error:
                if address in proven and off not in dice:
                    # Only authenticated loads can need more than one ordinary
                    # word at EL0. ERET and invalid encodings remain errors.
                    require(word & 0xff200400 == 0xf8200400, str(error))
                    fallback.append(dict(va=hex(address), word=hex(word), reason=str(error)))
                continue
            if lowered is None:
                continue
            name, replacement = lowered
            if address in proven and off not in dice:
                require(decoder[address][0] == name, f'PAC decoder mismatch at {address:#x}')
                edits.append(dict(fileoff=off, original_word=hex(word), ordinary_word=hex(replacement),
                                  mnemonic=name, evidence=proven[address]))
            elif address in table_data or off in dice:
                kept[name] += 1
            else:
                unproven += 1
    return edits, dict(proven_instructions=len(proven), roots=roots, tables=len(tables),
                       anomalies=len(anomalies), unproven_pac_words=unproven,
                       fallback_instructions=fallback, kept=dict(kept),
                       remaining_words_policy='zeta-ordinary-v1 kernel fallback')


def rewrite(original, trusted):
    thin, container = arm64e_slice(original)
    info = upstream.parse(thin)
    off, size = info['signature']
    signature = thin[off:off + size]
    old_hash, old_cd, old_blobs = validate_original(thin[:off], signature)
    require(old_hash in trusted, 'original CodeDirectory absent from supplied Apple trust caches')
    edits, proof = lower_proven(thin, info)
    data = bytearray(thin[:off])
    for edit in edits:
        at = edit['fileoff']
        require(0 <= at <= off - 4 and data[at:at + 4] == int(edit['original_word'], 16).to_bytes(4, 'little'), 'stale or out-of-bounds instruction edit')
        data[at:at + 4] = int(edit['ordinary_word'], 16).to_bytes(4, 'little')
    struct.pack_into('<I', data, 8, 0)
    provisional, _ = rebuilt_signature(data, signature)
    new_size = (len(provisional) + 15) & ~15
    cursor = 32
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        command, length = struct.unpack_from('<II', data, cursor)
        if command == 0x1d:
            struct.pack_into('<II', data, cursor + 8, off, new_size)
        if command == 0x19 and bytes(data[cursor + 8:cursor + 24]).split(b'\0', 1)[0] == b'__LINKEDIT':
            vm, vmsize, fileoff = struct.unpack_from('<3Q', data, cursor + 24)
            filesize = off + new_size - fileoff
            struct.pack_into('<Q', data, cursor + 32, max(vmsize, (filesize + 16383) & ~16383))
            struct.pack_into('<Q', data, cursor + 48, filesize)
        cursor += length
    signed, new_hash = rebuilt_signature(data, signature)
    output = bytes(data) + signed + bytes(new_size - len(signed))
    verified, cd, blobs = validate_original(output[:off], output[off:])
    require(verified.hex() == new_hash and 0x10000 not in blobs, 'new ad-hoc signature validation failed')
    for slot in (5, 7, 8, 9, 10, 11):
        require(old_blobs.get(slot) == blobs.get(slot), f'changed entitlement/constraint slot {slot}')
    attrs = trusted[old_hash]
    return output, dict(schema='nabu-zeta-userland-v1', abi=ABI, upstream_commit=PIN,
                        container=container, original_sha256=hashlib.sha256(original).hexdigest(),
                        input_slice_sha256=hashlib.sha256(thin).hexdigest(),
                        sha256=hashlib.sha256(output).hexdigest(), original_cdhash=old_hash.hex(),
                        cdhash=new_hash, signature_page_size=4096, platform_identifier=cd[38],
                        instruction_edits=len(edits), code_proof=proof, cms_removed=True,
                        special_blobs_preserved=True, chained_fixups_unchanged=True,
                        trust_cache_entry=dict(cdhash=new_hash, hash_type=attrs[0], flags=attrs[1], constraint_category=attrs[2]),
                        hardware_tested=False)


def load_trust(paths):
    entries = {}
    for path in paths:
        raw = Path(path).read_bytes()
        if raw[:4] != b'\x02\0\0\0':
            _, raw = upstream.im4p_payload(raw)
        entries.update(upstream.trust_cache_entries(raw))
    return entries


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--apple-trust-cache', type=Path, action='append', required=True)
    a = p.parse_args(argv)
    require(not a.output.exists() and not a.report.exists(), 'output already exists')
    data, report = rewrite(a.input.read_bytes(), load_trust(a.apple_trust_cache))
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_bytes(data)
    a.report.write_text(json.dumps(report, indent=2) + '\n')
    print(f"{a.input.name}: {report['instruction_edits']} PAC instructions lowered; {report['cdhash']}")


if __name__ == '__main__':
    main()
