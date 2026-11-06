#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fetch macOS 26.5 (25F71) BootKC via HTTP Range, decode IM4P+LZFSE, verify SHA-256, and translate for Nabu (SM8150)."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import struct
import urllib.request
import zlib

from nabu_pac_strip import translate_macho_for_nabu

ROOT = Path(__file__).resolve().parents[1]


def _http_range(url: str, start: int, end: int) -> bytes:
    req = urllib.request.Request(url, headers={'Range': f'bytes={start}-{end}'})
    with urllib.request.urlopen(req, timeout=90) as resp:
        if resp.status != 206:
            raise ValueError(f'expected HTTP 206 Partial Content, got {resp.status}')
        return resp.read()


def _locate_zip64_member(url: str, total_size: int, member_name: str) -> dict:
    """Locate a member inside a remote ZIP/ZIP64 archive using HTTP Range requests on the Central Directory."""
    tail_size = min(1 << 20, total_size)
    tail = _http_range(url, total_size - tail_size, total_size - 1)

    eocd_pos = tail.rfind(b'PK\x05\x06')
    if eocd_pos < 0:
        raise ValueError('ZIP End of Central Directory not found in last 1 MiB')

    # Check for ZIP64 EOCD Locator (PK\x06\x07) 20 bytes before EOCD
    loc_pos = eocd_pos - 20
    if loc_pos >= 0 and tail[loc_pos:loc_pos + 4] == b'PK\x06\x07':
        _, _, zip64_eocd_off, _ = struct.unpack_from('<IIQI', tail, loc_pos)
        eocd64 = _http_range(url, zip64_eocd_off, zip64_eocd_off + 56 - 1)
        if eocd64[:4] != b'PK\x06\x06':
            raise ValueError('invalid ZIP64 EOCD signature')
        cd_size, cd_offset = struct.unpack_from('<QQ', eocd64, 40)
    else:
        cd_size, cd_offset = struct.unpack_from('<II', tail, eocd_pos + 12)

    cd_bytes = _http_range(url, cd_offset, cd_offset + cd_size - 1)
    pos = 0
    target_bytes = member_name.encode('utf-8')
    while pos + 46 <= len(cd_bytes):
        if cd_bytes[pos:pos + 4] != b'PK\x01\x02':
            break
        method, _, _, crc32, comp_size, uncomp_size, name_len, extra_len, comment_len, _, _, _, local_off = (
            struct.unpack_from('<3H3I5H2I', cd_bytes, pos + 10)
        )
        name = cd_bytes[pos + 46:pos + 46 + name_len]
        extra = cd_bytes[pos + 46 + name_len:pos + 46 + name_len + extra_len]
        if 0xFFFFFFFF in (comp_size, uncomp_size, local_off):
            ep = 0
            while ep + 4 <= len(extra):
                tag, esz = struct.unpack_from('<HH', extra, ep)
                ep += 4
                if tag == 0x0001:
                    vals = []
                    if uncomp_size == 0xFFFFFFFF:
                        vals.append('uncomp')
                    if comp_size == 0xFFFFFFFF:
                        vals.append('comp')
                    if local_off == 0xFFFFFFFF:
                        vals.append('off')
                    unpacked = struct.unpack_from(f'<{len(vals)}Q', extra, ep)
                    mapping = dict(zip(vals, unpacked))
                    uncomp_size = mapping.get('uncomp', uncomp_size)
                    comp_size = mapping.get('comp', comp_size)
                    local_off = mapping.get('off', local_off)
                    break
                ep += esz
        if name == target_bytes:
            return {
                'name': member_name,
                'method': method,
                'crc32': crc32,
                'compressed_size': comp_size,
                'uncompressed_size': uncomp_size,
                'local_header_offset': local_off,
            }
        pos += 46 + name_len + extra_len + comment_len
    raise KeyError(f'member {member_name!r} not found in IPSW central directory')


def fetch_ipsw_member(url: str, total_size: int, member_name: str, out_path: Path) -> Path:
    if out_path.is_file() and out_path.stat().st_size > 0:
        return out_path
    info = _locate_zip64_member(url, total_size, member_name)
    lhdr = _http_range(url, info['local_header_offset'], info['local_header_offset'] + 30 - 1)
    if lhdr[:4] != b'PK\x03\x04':
        raise ValueError('invalid ZIP local file header')
    name_len, extra_len = struct.unpack_from('<HH', lhdr, 26)
    data_start = info['local_header_offset'] + 30 + name_len + extra_len
    comp_size = info['compressed_size']
    print(f'Fetching {member_name} ({comp_size} bytes) via HTTP Range...', flush=True)
    raw = _http_range(url, data_start, data_start + comp_size - 1)
    if info['method'] == 0:
        payload = raw
    elif info['method'] == 8:
        payload = zlib.decompress(raw, -zlib.MAX_WBITS)
    else:
        raise ValueError(f'unsupported ZIP compression method: {info["method"]}')
    if len(payload) != info['uncompressed_size']:
        raise ValueError('extracted member size mismatch')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(payload)
    return out_path


def _asn1_read_tlv(buf: bytes, pos: int) -> tuple[int, int, int]:
    tag = buf[pos]
    first = buf[pos + 1]
    if first < 0x80:
        return tag, pos + 2, first
    num_bytes = first & 0x7F
    length = int.from_bytes(buf[pos + 2:pos + 2 + num_bytes], 'big')
    return tag, pos + 2 + num_bytes, length


def extract_im4p_payload(im4p: bytes) -> bytes:
    tag, hdr_end, seq_len = _asn1_read_tlv(im4p, 0)
    if tag != 0x30:
        raise ValueError('IM4P root is not an ASN.1 SEQUENCE')
    pos = hdr_end
    # Field 0: "IM4P", Field 1: fourcc ("krnl"), Field 2: description, Field 3: OCTET STRING payload
    for idx in range(4):
        ftag, fstart, flen = _asn1_read_tlv(im4p, pos)
        if idx == 3:
            if ftag != 0x04:
                raise ValueError(f'expected OCTET STRING (0x04) for IM4P payload, got {hex(ftag)}')
            return im4p[fstart:fstart + flen]
        pos = fstart + flen
    raise ValueError('truncated IM4P container')


def lzfse_decompress(payload: bytes) -> bytes:
    lib_path = ROOT / 'out' / 'host-tools' / 'liblzfse.so'
    if not lib_path.is_file():
        raise FileNotFoundError(f'missing {lib_path}; compile liblzfse.so first')
    lib = ctypes.CDLL(str(lib_path))
    lib.lzfse_decode_buffer.argtypes = [
        ctypes.c_char_p,
        ctypes.c_size_t,
        ctypes.c_char_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
    ]
    lib.lzfse_decode_buffer.restype = ctypes.c_size_t

    # Allocate 256 MiB output buffer for decoded BootKernelCollection.kc
    dst_capacity = max(len(payload) * 6, 256 << 20)
    dst = ctypes.create_string_buffer(dst_capacity)
    written = lib.lzfse_decode_buffer(dst, dst_capacity, payload, len(payload), None)
    if written == 0 or written == dst_capacity:
        raise RuntimeError('LZFSE decompression failed or exceeded buffer')
    return dst.raw[:written]


def prepare_and_translate_bootkc(output_dir: Path, *, downgrade_subtype: bool = False) -> dict:
    profile = json.loads((ROOT / 'config' / 'macos26.json').read_text(encoding='utf-8'))
    kernel_cfg = profile['kernels']['j313ap']
    member_name = kernel_cfg['path']
    expected_sha256 = kernel_cfg['decoded_sha256']
    expected_version = kernel_cfg['darwin_version'].encode('ascii')

    apple_dir = ROOT / 'private' / 'apple' / 'macos26'
    im4p_path = apple_dir / member_name
    decoded_path = apple_dir / f'{member_name}.decoded'

    if not decoded_path.is_file():
        fetch_ipsw_member(profile['ipsw']['url'], profile['ipsw']['bytes'], member_name, im4p_path)
        im4p_payload = extract_im4p_payload(im4p_path.read_bytes())
        if im4p_payload[:4] in (b'bvx2', b'bvx-', b'bvx1', b'bvxn'):
            print('Decompressing LZFSE (bvx2) BootKC payload...', flush=True)
            decoded = lzfse_decompress(im4p_payload)
        else:
            decoded = im4p_payload
        decoded_path.parent.mkdir(parents=True, exist_ok=True)
        decoded_path.write_bytes(decoded)
    else:
        decoded = decoded_path.read_bytes()

    actual_sha256 = hashlib.sha256(decoded).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError(f'BootKC decoded SHA-256 mismatch: got {actual_sha256}, expected {expected_sha256}')
    if expected_version not in decoded:
        raise ValueError('BootKC does not contain the pinned Darwin Kernel Version 25.5.0 string')

    print(f'Verified {member_name}.decoded ({len(decoded)} bytes, sha256={actual_sha256[:16]}...)', flush=True)
    print('Translating macOS 26.5 BootKC (ARM64E -> SM8150 ARMv8.2-A)...', flush=True)
    nabu_kc, report = translate_macho_for_nabu(
        decoded,
        convert_auth_fixups=True,
        downgrade_subtype=downgrade_subtype,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    out_kc_path = output_dir / f'{member_name}.nabu.kc'
    out_report_path = output_dir / 'nabu_bootkc_translation_report.json'
    out_kc_path.write_bytes(nabu_kc)

    full_report = {
        'board': 'nabu',
        'soc': 'SM8150',
        'macos_version': profile['version'],
        'macos_build': profile['build'],
        'xnu_version': profile['source']['xnu_version'],
        'source_bootkc_path': str(decoded_path.relative_to(ROOT)),
        'source_bootkc_bytes': len(decoded),
        'source_bootkc_sha256': actual_sha256,
        'darwin_version': kernel_cfg['darwin_version'],
        'output_nabu_bootkc_path': str(out_kc_path.relative_to(ROOT)),
        'output_nabu_bootkc_bytes': len(nabu_kc),
        'output_nabu_bootkc_sha256': hashlib.sha256(nabu_kc).hexdigest(),
        'translation': report,
    }
    out_report_path.write_text(json.dumps(full_report, indent=2) + '\n', encoding='utf-8')
    return full_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'out' / 'nabu-bootkc')
    parser.add_argument('--downgrade-subtype', action='store_true')
    args = parser.parse_args()
    report = prepare_and_translate_bootkc(args.output_dir, downgrade_subtype=args.downgrade_subtype)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
