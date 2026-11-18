"""Apply zeta code-proof/lowering to retained kext code before FIPS sealing.

Native kernel code is built arm64. Every retained pointer fixup still passes
through the existing bounds-checked plain-pointer loader. Unproven words stay
unchanged and use the same ordinary-pointer exception semantics if executed.
"""
from collections import Counter
from pathlib import Path
import struct
import sys
sys.path.insert(0, str(Path(__file__).with_name('zeta_pac')))
import zeta_userland_code as code
import zeta_userland_pauth as pauth
from zeta_dyld_cache import Coverage


def sections(image):
    out = []
    for command, at, length in image.commands:
        if command != 0x19:
            continue
        segment = image.data[at + 8:at + 24].split(b'\0', 1)[0].decode()
        for index in range(struct.unpack_from('<I', image.data, at + 64)[0]):
            start = at + 72 + 80 * index
            address, size = struct.unpack_from('<QQ', image.data, start + 32)
            flags = struct.unpack_from('<I', image.data, start + 64)[0]
            if size and flags & 0x80000400:
                out.append(dict(name=image.data[start:start + 16].split(b'\0', 1)[0].decode(),
                                segment=segment, addr=address, size=size,
                                offset=image.offset(address, size), flags=flags))
    return out


def lower_retained(emit):
    from kc_image import Image
    from fileset_emit import inline_data_ranges
    images, ranges = [], []
    for owner, (_, at) in emit.cache.entries.items():
        if owner == 'com.apple.kernel' or emit.closure is not None and owner not in emit.closure:
            continue
        image = Image(emit.cache.data, at)
        spans = sections(image)
        if spans:
            images.append((owner, image, spans))
            ranges += [(s['addr'], s['addr'] + s['size']) for s in spans]
    report, counts = {}, Counter()
    for owner, image, spans in images:
        starts = set(image.function_starts())
        symbols = set(image.symbols())
        all_starts = starts | symbols
        external = Coverage([(begin, end) for begin, end in ranges
                             if not any(begin == s['addr'] and end == s['addr'] + s['size'] for s in spans)]
                            + emit.old_ranges + [(s['va'], s['va'] + s['size']) for s in emit.native.segments if s['size']])
        instructions = code.decode(emit.cache.data, spans, code.llvm_oracle)
        # Existing prelinker already pins these embedded math/SHA tables to hashes.
        data_words = set()
        for span in spans:
            blob = emit.cache.data[span['offset']:span['offset'] + span['size']]
            for begin, end in inline_data_ranges(blob):
                for addr in range(span['addr'] + begin, span['addr'] + end, 4):
                    instructions.pop(addr, None)
                    data_words.add(addr)
        _, proven, tables, _, anomalies, roots = code.prove_roots(
            instructions, emit.cache.data, image.offset, set(), all_starts, all_starts,
            set(), external=external, gaps=True)
        changed, unproven = Counter(), 0
        for span in spans:
            for addr in range(span['addr'], span['addr'] + span['size'], 4):
                at = image.offset(addr, 4)
                word = struct.unpack_from('<I', emit.cache.data, at)[0]
                try:
                    lowered = pauth.lower(word)
                except ValueError:
                    if addr in proven:
                        raise ValueError(f'{owner}: unsupported zeta lowering at {addr:#x}')
                    continue
                if not lowered:
                    continue
                if addr not in proven:
                    if addr not in tables and addr not in data_words:
                        unproven += 1
                    continue
                mnemonic, ordinary = lowered
                if instructions[addr][0] != mnemonic:
                    raise ValueError(f'{owner}: PAC decoder mismatch at {addr:#x}')
                current = struct.unpack_from('<I', emit.data, at)[0]
                # Relocation may already have replaced an authenticated stub.
                if current != word:
                    if pauth.lower(current) is not None:
                        raise ValueError(f'{owner}: changed PAC word at {addr:#x}')
                    continue
                struct.pack_into('<I', emit.data, at, ordinary)
                changed[mnemonic] += 1
        counts.update(changed)
        report[owner] = dict(instruction_edits=sum(changed.values()), instructions=dict(changed),
                             unproven_words=unproven, roots=roots, anomalies=len(anomalies))
    return dict(abi='zeta-ordinary-v1', upstream_commit='eb489285a1d9153872f00c93ebb519a269b249b5',
                instruction_edits=sum(counts.values()), instructions=dict(counts), components=report,
                remaining_words_policy='same ordinary-pointer exception fallback', hardware_tested=False)
