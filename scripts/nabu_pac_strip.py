#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Translate Apple arm64e Mach-O binaries & Format-7/Format-8 chained fixups for SM8150 (ARMv8.2-A without hardware PAC)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct

from native_macho import macho

AARCH64_NOP = 0xD503201F
AARCH64_RET = 0xD65F03C0
AARCH64_ERET = 0xD69F03E0
CPU_SUBTYPE_ARM64_ALL = 0x00000000
CPU_SUBTYPE_ARM64E = 0x80000002


def translate_arm64e_insn(word: int) -> tuple[int, str | None]:
    """Translate a single non-NOP-space AArch64 PAC instruction to its ARMv8.2-A equivalent."""
    # 1. RETAA (0xd65f0bff) / RETAB (0xd65f0fff) -> RET (0xd65f03c0)
    if word in (0xD65F0BFF, 0xD65F0FFF):
        return AARCH64_RET, 'RETAA/RETAB->RET'

    # 2. ERETAA (0xd69f0bff) / ERETAB (0xd69f0fff) -> ERET (0xd69f03e0)
    if word in (0xD69F0BFF, 0xD69F0FFF):
        return AARCH64_ERET, 'ERETAA/ERETAB->ERET'

    # 3. BLRAA / BLRAB Xn, Xm -> BLR Xn
    if (word & 0xFFFFF800) in (0xD73F0800, 0xD73F0C00):
        rn = (word >> 5) & 0x1F
        return 0xD63F0000 | (rn << 5), 'BLRAA/BLRAB->BLR'

    # 4. BLRAAZ / BLRABZ Xn -> BLR Xn
    if (word & 0xFFFFF81F) in (0xD63F081F, 0xD63F0C1F):
        rn = (word >> 5) & 0x1F
        return 0xD63F0000 | (rn << 5), 'BLRAAZ/BLRABZ->BLR'

    # 5. BRAA / BRAB Xn, Xm -> BR Xn
    if (word & 0xFFFFF800) in (0xD71F0800, 0xD71F0C00):
        rn = (word >> 5) & 0x1F
        return 0xD61F0000 | (rn << 5), 'BRAA/BRAB->BR'

    # 6. BRAAZ / BRABZ Xn -> BR Xn
    if (word & 0xFFFFF81F) in (0xD61F081F, 0xD61F0C1F):
        rn = (word >> 5) & 0x1F
        return 0xD61F0000 | (rn << 5), 'BRAAZ/BRABZ->BR'

    # 7. LDRAA / LDRAB Xt, [Xn, #simm10<<3]{!} -> LDR / LDUR Xt, [Xn, #imm]
    #    Encoding: bits[31:24]=0xf8, bit[23]=M, bit[22]=S, bit[21]=1, bits[20:12]=imm9, bit[11]=W, bit[10]=1
    if (word & 0xFF200400) == 0xF8200400:
        s = (word >> 22) & 0x1
        imm9 = (word >> 12) & 0x1FF
        w = (word >> 11) & 0x1
        rn = (word >> 5) & 0x1F
        rt = word & 0x1F
        simm10 = (imm9 | (s << 9)) - (1024 if s else 0)
        offset_bytes = simm10 * 8
        if w == 0 and 0 <= simm10 <= 4095:
            return 0xF9400000 | (simm10 << 10) | (rn << 5) | rt, 'LDRAA/LDRAB->LDR'
        clamped = max(-256, min(255, offset_bytes)) & 0x1FF
        if w == 1:
            return 0xF8400C00 | (clamped << 12) | (rn << 5) | rt, 'LDRAA/LDRAB->LDR_PRE'
        return 0xF8400000 | (clamped << 12) | (rn << 5) | rt, 'LDRAA/LDRAB->LDUR'

    # 8. Data-processing (1 source) PAC/AUT/XPAC:
    #    PACIA/PACIB/PACDA/PACDB/AUTIA/AUTIB/AUTDA/AUTDB/PACIZA..AUTDZB (0xdac10000..0xdac13fff)
    #    XPACI/XPACD (0xdac14000..0xdac147ff)
    if (word & 0xFFFF8000) == 0xDAC10000 and ((word >> 10) & 0x1F) <= 0x11:
        return AARCH64_NOP, 'PAC/AUT/XPAC->NOP'

    # 9. PACGA Xd, Xn, Xm (0x9ac03000) -> EOR Xd, Xn, Xm (0xca000000)
    if (word & 0xFFE0FC00) == 0x9AC03000:
        rm = (word >> 16) & 0x1F
        rn = (word >> 5) & 0x1F
        rd = word & 0x1F
        return 0xCA000000 | (rm << 16) | (rn << 5) | rd, 'PACGA->EOR'

    return word, None


def rewrite_text_buffer(code: bytes) -> tuple[bytes, dict[str, int]]:
    """Scan a 4-byte aligned AArch64 executable buffer and rewrite all non-NOP PAC instructions."""
    if len(code) % 4 != 0:
        raise ValueError('AArch64 executable section size must be a multiple of 4 bytes')
    out = bytearray(code)
    counts: dict[str, int] = {}
    for offset in range(0, len(out), 4):
        word, = struct.unpack_from('<I', out, offset)
        replaced, kind = translate_arm64e_insn(word)
        if kind is not None:
            struct.pack_into('<I', out, offset, replaced)
            counts[kind] = counts.get(kind, 0) + 1
    return bytes(out), counts


def strip_relocation_auth(relocations: list[dict], high8: int = 0xFF) -> list[dict]:
    """Convert authenticated format-7 relocation descriptors into unauthenticated rebase descriptors."""
    stripped = []
    for item in relocations:
        entry = dict(item)
        if entry.get('authenticated'):
            entry['authenticated'] = False
            entry['nabu_stripped_auth'] = True
            entry.setdefault('high8', high8)
        stripped.append(entry)
    return stripped


def _extract_fileset_entries(data: bytes, parsed: dict) -> list[dict]:
    """Return embedded LC_FILESET_ENTRY (0x80000035) descriptors for MH_FILESET BootKC images."""
    entries = []
    for cmd, pos, length in parsed['commands']:
        if cmd == 0x80000035 and length >= 32:
            va, fileoff, nameoff = struct.unpack_from('<QQI', data, pos + 8)
            end = data.find(b'\0', pos + nameoff, pos + length)
            name = data[pos + nameoff:end].decode('utf-8', errors='replace') if end > 0 else f'entry_{hex(fileoff)}'
            entries.append({'name': name, 'va': va, 'fileoff': fileoff})
    return entries


def rewrite_chained_fixups(image_buf: bytearray, parsed: dict, high8: int = 0xFF) -> dict[str, int]:
    """Convert in-place Format-7 (MH_EXECUTE) or Format-8 (MH_FILESET BootKC) auth rebase pointers to non-auth rebases."""
    commands = [at for cmd, at, size in parsed['commands'] if cmd == 0x80000034 and size == 16]
    if not commands:
        return {'format': 0, 'chains_scanned': 0, 'pointers_total': 0, 'auth_converted': 0}

    off, size = struct.unpack_from('<II', image_buf, commands[0] + 8)
    blob = bytes(image_buf[off:off + size])
    version, starts, _, _, imports, imports_format, symbols_format = struct.unpack_from('<7I', blob)
    if version or not 28 <= starts <= size - 4:
        raise ValueError('unsupported chained fixup header for Nabu translation')

    count, = struct.unpack_from('<I', blob, starts)
    pointers_total = 0
    auth_converted = 0
    chains_scanned = 0
    detected_format = 0
    userland: dict[str, int] = {}
    key_counts = {'IA': 0, 'IB': 0, 'DA': 0, 'DB': 0}

    for index, segment in enumerate(parsed['segments'][:count]):
        relative, = struct.unpack_from('<I', blob, starts + 4 + index * 4)
        if not relative:
            continue
        pos = starts + relative
        span, page_size, fmt, _, _, pages = struct.unpack_from('<IHHQIH', blob, pos)
        if fmt in (9, 12):
            # Userspace arm64e chains: bits are bind(63), auth(62), next(51..61);
            # an auth rebase carries a 32-bit vm offset, not the kernel cache's
            # 30-bit target, so the kernel-cache conversion below does not apply.
            stats = {'auth_rebase': 0, 'bind': 0, 'rebase': 0}
            for page in range(pages):
                start, = struct.unpack_from('<H', blob, pos + 22 + 2 * page)
                if start == 0xFFFF:
                    continue
                site = page * page_size + start
                while True:
                    raw, = struct.unpack_from('<Q', image_buf, segment['fileoff'] + site)
                    if (raw >> 63) & 1:
                        stats['bind'] += 1
                    elif (raw >> 62) & 1:
                        stats['auth_rebase'] += 1
                    else:
                        stats['rebase'] += 1
                    following = (raw >> 51) & 0x7FF
                    if not following:
                        break
                    site += following * 4
            userland['pointer_format'] = fmt
            for key, value in stats.items():
                userland[key] = userland.get(key, 0) + value
            continue
        if fmt not in (7, 8) or page_size not in (4096, 16384):
            raise ValueError(f'expected format-7 or format-8 chained fixups, got format={fmt}, page_size={page_size}')
        detected_format = fmt
        chains_scanned += 1
        next_mask = 0xFFF if fmt == 8 else 0x7FF
        for page in range(pages):
            start, = struct.unpack_from('<H', blob, pos + 22 + 2 * page)
            if start == 0xFFFF:
                continue
            site = page * page_size + start
            while True:
                file_pos = segment['fileoff'] + site
                raw, = struct.unpack_from('<Q', image_buf, file_pos)
                pointers_total += 1
                auth = (raw >> 63) & 1
                following = (raw >> 51) & next_mask
                if auth:
                    key_idx = (raw >> 49) & 0x3
                    key_counts[('IA', 'IB', 'DA', 'DB')[key_idx]] += 1
                    target = raw & 0xFFFFFFFF
                    if fmt == 8:
                        # Format-8 (DYLD_CHAINED_PTR_64_KERNEL_CACHE):
                        # Clear isAuth (bit 63) and key/addrDiv/diversity (bits 50..32);
                        # preserve next (bits 62..51), cacheLevel (bits 31..30), target (bits 29..0).
                        new_raw = ((following & 0xFFF) << 51) | target
                    else:
                        # Format-7 (DYLD_CHAINED_PTR_ARM64E_KERNEL):
                        # Convert auth_rebase -> non-auth rebase with high8
                        new_raw = (
                            ((following & 0x7FF) << 51)
                            | ((high8 & 0xFF) << 43)
                            | (target & ((1 << 43) - 1))
                        )
                    struct.pack_into('<Q', image_buf, file_pos, new_raw)
                    auth_converted += 1
                if not following:
                    break
                site += following * 4

    return {
        'userland_chains': userland,
        'imports_count': imports,
        'imports_format': imports_format,
        'symbols_format': symbols_format,
        'format': detected_format,
        'chains_scanned': chains_scanned,
        'pointers_total': pointers_total,
        'auth_converted': auth_converted,
        'pac_keys_stripped': key_counts,
    }


# Keep backwards-compatible alias
rewrite_format7_chained_fixups = rewrite_chained_fixups


def translate_macho_for_nabu(
    data: bytes,
    *,
    convert_auth_fixups: bool = True,
    downgrade_subtype: bool = False,
    high8: int = 0xFF,
) -> tuple[bytes, dict]:
    """Rewrite an arm64e Mach-O or MH_FILESET BootKC binary so it executes cleanly on SM8150 (ARMv8.2-A without PAC)."""
    parsed = macho(data)
    buf = bytearray(data)

    insn_totals: dict[str, int] = {}
    sections_rewritten = 0
    rewritten_ranges: list[tuple[int, int]] = []

    for segment in parsed['segments']:
        is_exec_seg = bool(segment['initprot'] & 0x4) or segment['name'] in ('__TEXT_EXEC', '__PRELINK_TEXT')
        if not is_exec_seg:
            if segment['name'] == '__TEXT' and segment['sections']:
                for section in segment['sections']:
                    if section['name'] in ('__text', '__stubs', '__init_offsets'):
                        start = section['fileoff']
                        end = start + section['size']
                        if start and end <= len(buf) and section['size'] % 4 == 0:
                            rewritten, counts = rewrite_text_buffer(bytes(buf[start:end]))
                            buf[start:end] = rewritten
                            sections_rewritten += 1
                            rewritten_ranges.append((start, end))
                            for k, v in counts.items():
                                insn_totals[k] = insn_totals.get(k, 0) + v
            continue

        if segment['sections']:
            for section in segment['sections']:
                start = section['fileoff']
                end = start + section['size']
                if start and end <= len(buf) and section['size'] > 0 and section['size'] % 4 == 0:
                    rewritten, counts = rewrite_text_buffer(bytes(buf[start:end]))
                    buf[start:end] = rewritten
                    sections_rewritten += 1
                    rewritten_ranges.append((start, end))
                    for k, v in counts.items():
                        insn_totals[k] = insn_totals.get(k, 0) + v
        elif segment['filesize'] > 0 and segment['filesize'] % 4 == 0:
            start = segment['fileoff']
            end = start + segment['filesize']
            if start + 32 <= len(buf):
                rewritten, counts = rewrite_text_buffer(bytes(buf[start:end]))
                buf[start:end] = rewritten
                sections_rewritten += 1
                rewritten_ranges.append((start, end))
                for k, v in counts.items():
                    insn_totals[k] = insn_totals.get(k, 0) + v

    fileset_entries = _extract_fileset_entries(data, parsed)

    fixup_stats = (
        rewrite_chained_fixups(buf, parsed, high8=high8)
        if convert_auth_fixups
        else {'format': 0, 'chains_scanned': 0, 'pointers_total': 0, 'auth_converted': 0}
    )

    if downgrade_subtype:
        struct.pack_into('<I', buf, 8, CPU_SUBTYPE_ARM64_ALL)
        for entry in fileset_entries:
            foff = entry['fileoff']
            if 0 <= foff + 12 <= len(buf):
                magic, cpu = struct.unpack_from('<II', buf, foff)
                if magic == 0xFEEDFACF and cpu == 0x0100000C:
                    struct.pack_into('<I', buf, foff + 8, CPU_SUBTYPE_ARM64_ALL)

    report = {
        'kind': parsed['kind'],
        'is_fileset_bootkc': parsed['kind'] == 12,
        'fileset_entry_count': len(fileset_entries),
        'original_architecture': parsed['architecture'],
        'final_architecture': 'arm64' if downgrade_subtype else parsed['architecture'],
        'sections_rewritten': sections_rewritten,
        'executable_bytes_scanned': sum(end - start for start, end in rewritten_ranges),
        'instructions_rewritten': sum(insn_totals.values()),
        'instruction_breakdown': insn_totals,
        'chained_fixups': fixup_stats,
    }
    return bytes(buf), report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='Input arm64e Mach-O or BootKC binary')
    parser.add_argument('--output', type=Path, required=True, help='Output Nabu-compatible Mach-O / BootKC binary')
    parser.add_argument('--report', type=Path, help='Optional JSON translation report output')
    parser.add_argument('--keep-auth-fixups', action='store_true', help='Keep format-7/8 auth bit set for soft-PAC kernel fixup path')
    parser.add_argument('--downgrade-subtype', action='store_true', help='Downgrade cpusubtype from arm64e to arm64')
    args = parser.parse_args()

    out_bytes, report = translate_macho_for_nabu(
        args.input.read_bytes(),
        convert_auth_fixups=not args.keep_auth_fixups,
        downgrade_subtype=args.downgrade_subtype,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(out_bytes)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
