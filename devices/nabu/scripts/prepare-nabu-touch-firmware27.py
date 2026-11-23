#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Derive NT36523 SRAM partitions/CRC banks from the owner's exact firmware.

Header interpretation follows nvt_bin_header_parser in Xiaomi/Novatek's
nt36523/nt36xxx_fw_update.c, as published by LineageOS/android_kernel_xiaomi_sm8150
at 59220a048d04516e7fe1dfc094507930166a1e60. No firmware bytes are distributed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

EXPECTED = 'd66d15f3dff35292e42e0d301bf53a7fcea6b6fc1b93bca90bb66d765bcfba9b'


def parse(data):
    if len(data) < 0x40:
        raise ValueError('Truncated NT36523 header')
    end = struct.unpack_from('<I', data)[0]
    if end < 0x30 or end > len(data) or end % 16:
        raise ValueError('Invalid firmware header length')
    cascade = bool(data[0x20] & 2)
    limit = end // 2 if cascade else end
    info_count = len(range(0x30, limit, 16)) + int(cascade)
    overlay_count = data[0x28] & 15 if data[0x28] & 16 else 0
    if info_count + overlay_count > 64:
        raise ValueError('Too many firmware partitions')
    parts, banks, header_found = [], [], False
    dlm_offset = struct.unpack_from('<I', data, 12)[0]
    for index in range(2 + info_count + overlay_count):
        if index < 2:
            offset, address, size = struct.unpack_from('<III', data, index * 12)
            crc = struct.unpack_from('<I', data, 0x18 + index * 4)[0]
            banks += [address, size, crc]
        else:
            if index < 2 + info_count:
                position = end - 16 if header_found and cascade else 0x30 + 16 * (index - 2)
            else:
                position = dlm_offset + 16 * (index - 2 - info_count)
            if position + 16 > len(data):
                raise ValueError('Partition header outside firmware')
            address, size, offset, crc = struct.unpack_from('<IIII', data, position)
            if index < 2 + info_count and offset < end and size:
                header_found = True
        # The stock downloader writes size+1 bytes; zero-sized entries are
        # reserved and omitted. CRC bank length retains the original size.
        if size:
            length = size + 1
            if offset + length > len(data) or address + length > 0x400000:
                raise ValueError('Firmware partition exceeds file/SRAM bounds')
            parts.append((address, offset, length))
    return parts, banks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = args.firmware.read_bytes()
    if len(data) != 196608 or hashlib.sha256(data).hexdigest() != EXPECTED:
        raise ValueError('Require the exact qualified nabu CSOT firmware')
    parts, banks = parse(data)
    if len(parts) != 4 or len(banks) != 6:
        raise ValueError('Firmware does not match the current four-partition ADT contract')
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'novatek_nt36523_k82_fw01.bin').write_bytes(data)
    (args.output / 'partitions.bin').write_bytes(b''.join(struct.pack('<III', *item) for item in parts))
    (args.output / 'crc-bank.bin').write_bytes(struct.pack('<6I', *banks))
    (args.output / 'metadata-verification.json').write_text(json.dumps(dict(
        firmware_sha256=EXPECTED, partitions=parts, crc_bank=banks,
        reference_commit='59220a048d04516e7fe1dfc094507930166a1e60', hardware_tested=False), indent=2) + '\n')
    print('Qualified firmware copied; partition/CRC metadata derived, not synthesized')


if __name__ == '__main__':
    main()
