#!/usr/bin/env python3
"""Back up and prepare the existing NABU ESP through root TWRP ADB.

Default: save GPT/ESP/config backups and an offline candidate, without writing
ESP contents. --install writes only the existing ESP partition, verifies its
hash, then atomically replaces the boot config and leaves ESP unmounted.
No GPT edits, repartitioning or reboot. Logging requires a kernel containing
the new NABULOG2 ESP logger; these arguments cannot add it to an older kernel.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import struct
import subprocess
import sys
import uuid
import zlib

from prepare_nabu_esp_log import prepare, enable_config
from nabu_log_volume import BLOCK

LUN = '/dev/block/sda'
CONFIG = '/esp/EFI/UniXNU/unixnu.toml'
GPT_WINDOW_BLOCKS = (1 << 20) // BLOCK
ESP_TYPE = uuid.UUID('c12a7328-f81f-11d2-ba4b-00a0c93ec93b').bytes_le
MAX_ESP_BYTES = 512 << 20


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def parse_gpt_copy(raw: bytes, disk_blocks: int, window_first: int) -> dict:
    """Validate one complete 4K GPT header and its entry array in a saved window."""
    current = 1 if window_first == 0 else disk_blocks - 1
    offset = (current - window_first) * BLOCK
    if offset < 0 or offset + BLOCK > len(raw):
        raise ValueError('GPT header outside captured window')
    h = bytearray(raw[offset:offset + BLOCK])
    if h[:8] != b'EFI PART' or struct.unpack_from('<I', h, 8)[0] != 0x10000:
        raise ValueError('GPT signature/revision differs')
    size, checksum, reserved = struct.unpack_from('<III', h, 12)
    if not 92 <= size <= BLOCK or reserved:
        raise ValueError('invalid GPT header size/reserved field')
    struct.pack_into('<I', h, 16, 0)
    if zlib.crc32(h[:size]) != checksum:
        raise ValueError('GPT header CRC mismatch')
    here, other, first, last = struct.unpack_from('<4Q', h, 24)
    expected_other = disk_blocks - 1 if current == 1 else 1
    if here != current or other != expected_other or not 2 < first <= last < disk_blocks - 1:
        raise ValueError('invalid GPT disk geometry')
    table, count, stride, array_crc = struct.unpack_from('<QIII', h, 72)
    # Match the existing kernel logger's admitted GPT format.
    if not 1 <= count <= 128 or stride != 128:
        raise ValueError('GPT entry geometry is unsupported by the NABU logger')
    table_blocks = (count * stride + BLOCK - 1) // BLOCK
    if current == 1:
        valid_table = 2 <= table and table + table_blocks <= first
    else:
        valid_table = last < table and table + table_blocks <= current
    begin = (table - window_first) * BLOCK
    end = begin + count * stride
    if not valid_table or begin < 0 or end > len(raw):
        raise ValueError('GPT entry array outside metadata/captured window')
    entries = raw[begin:end]
    if zlib.crc32(entries) != array_crc:
        raise ValueError('GPT entry-array CRC mismatch')
    parts = []
    identities = set()
    for index in range(count):
        e = entries[index * stride:(index + 1) * stride]
        if e[:16] == bytes(16):
            continue
        lo, hi = struct.unpack_from('<QQ', e, 32)
        if not first <= lo <= hi <= last:
            raise ValueError('GPT partition outside usable disk')
        if not any(e[16:32]) or e[16:32] in identities:
            raise ValueError('GPT partition GUID missing/duplicated')
        identities.add(e[16:32])
        name = e[56:128].decode('utf-16-le').split('\0', 1)[0]
        parts.append(dict(index=index + 1, name=name, first_lba=lo, last_lba=hi,
                          type_guid=str(uuid.UUID(bytes_le=e[:16])),
                          unique_guid=str(uuid.UUID(bytes_le=e[16:32]))))
    ordered = sorted(parts, key=lambda p: p['first_lba'])
    if any(a['last_lba'] >= b['first_lba'] for a, b in zip(ordered, ordered[1:])):
        raise ValueError('GPT partitions overlap')
    return dict(current=here, other=other, first_usable=first, last_usable=last,
                disk_guid=h[56:72].hex(), count=count, stride=stride,
                entries=entries, partitions=parts)


def validate_gpt(primary: bytes, backup: bytes, disk_blocks: int) -> dict:
    count = len(primary) // BLOCK
    if len(primary) % BLOCK or len(backup) != len(primary) or disk_blocks <= 2 * count:
        raise ValueError('invalid GPT capture windows/disk size')
    a = parse_gpt_copy(primary, disk_blocks, 0)
    b = parse_gpt_copy(backup, disk_blocks, disk_blocks - count)
    if any(a[key] != b[key] for key in ('disk_guid', 'first_usable', 'last_usable', 'count', 'stride', 'entries')):
        raise ValueError('primary and backup GPT copies disagree')
    # Both the sole ESP type and the sole exact esp name must identify one entry.
    candidates = [p for p in a['partitions'] if p['name'] == 'esp' or p['type_guid'] == str(uuid.UUID(bytes_le=ESP_TYPE))]
    if len(candidates) != 1 or candidates[0]['name'] != 'esp' or candidates[0]['type_guid'] != str(uuid.UUID(bytes_le=ESP_TYPE)):
        raise ValueError('requires exactly one GPT ESP with type ESP and name esp')
    esp = dict(candidates[0])
    esp['blocks'] = esp['last_lba'] - esp['first_lba'] + 1
    esp['bytes'] = esp['blocks'] * BLOCK
    if not 0 < esp['bytes'] <= MAX_ESP_BYTES:
        raise ValueError('ESP exceeds the bounded 512 MiB backup/install limit')
    return dict(esp=esp, disk_blocks=disk_blocks, logical_block_bytes=BLOCK,
                disk_guid=a['disk_guid'], primary_sha256=sha(primary), backup_sha256=sha(backup))


def validate_bpb(boot: bytes, partition_blocks: int) -> None:
    if len(boot) != BLOCK:
        raise ValueError('short ESP boot-sector read')
    bps, spc, reserved, fats = struct.unpack_from('<HBHB', boot, 11)
    total, fat_blocks = struct.unpack_from('<II', boot, 32)
    if (bps != BLOCK or spc != 1 or fats != 2 or not reserved or not 1 <= fat_blocks <= 256
            or total != partition_blocks or reserved + 2 * fat_blocks >= total
            or any(boot[17:21]) or any(boot[22:24]) or any(boot[40:44])
            or boot[82:87] != b'FAT32' or boot[510:512] != b'\x55\xaa'):
        raise ValueError('ESP FAT32 BPB geometry differs from its existing GPT extent; no resize attempted')


def select_serial(adb: str, requested: str | None) -> str:
    if requested:
        return requested
    listing = subprocess.check_output([adb, 'devices'], text=True, timeout=15)
    devices = [line.split()[0] for line in listing.splitlines()
               if len(line.split()) >= 2 and line.split()[1] in ('device', 'recovery')]
    if len(devices) != 1:
        raise ValueError('requires exactly one online ADB device or an explicit --serial')
    return devices[0]


def mounts(text: str) -> list[dict]:
    result = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 10 or '-' not in fields:
            raise ValueError('malformed recovery mountinfo')
        # Decode the standard mountinfo octal escapes before comparing /esp.
        target = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4])
        result.append(dict(device=fields[2], target=target, options=fields[5].split(',')))
    return result


class Recovery:
    def __init__(self, serial: str | None = None, adb: str = 'adb'):
        self.serial = select_serial(adb, serial)
        self.adb = [adb, '-s', self.serial]
        state = subprocess.check_output(self.adb + ['get-state'], text=True, timeout=15).strip()
        if (state not in ('device', 'recovery') or self.shell('id -u') != '0'
                or self.shell('getprop ro.product.device') != 'nabu'
                or not self.shell('getprop ro.twrp.version')):
            raise ValueError('requires root TWRP on nabu')
        self.esp = ''
        self.devnum = ''

    def shell(self, command: str) -> str:
        return subprocess.check_output(self.adb + ['shell', command], text=True, timeout=180).strip()

    def read_blocks(self, path: str, first: int, count: int) -> bytes:
        command = f'dd if={shlex.quote(path)} bs={BLOCK} skip={first} count={count} 2>/dev/null'
        data = subprocess.check_output(self.adb + ['exec-out', command], timeout=180)
        if len(data) != count * BLOCK:
            raise ValueError('incomplete raw device read')
        return data

    def identity(self) -> tuple[dict, bytes, bytes]:
        if int(self.shell('cat /sys/block/sda/queue/logical_block_size')) != BLOCK:
            raise ValueError('sda logical block size must be 4096')
        disk_bytes = int(self.shell(f'blockdev --getsize64 {LUN}'))
        if disk_bytes % BLOCK:
            raise ValueError('sda capacity is not whole 4096-byte blocks')
        disk_blocks = disk_bytes // BLOCK
        if disk_blocks <= 2 * GPT_WINDOW_BLOCKS:
            raise ValueError('sda is too small for the GPT capture windows')
        primary = self.read_blocks(LUN, 0, GPT_WINDOW_BLOCKS)
        backup = self.read_blocks(LUN, disk_blocks - GPT_WINDOW_BLOCKS, GPT_WINDOW_BLOCKS)
        result = validate_gpt(primary, backup, disk_blocks)
        esp = self.shell('readlink -f /dev/block/by-name/esp')
        if esp != f"{LUN}{result['esp']['index']}":
            raise ValueError('resolved esp block node differs from its GPT entry')
        node = esp.rsplit('/', 1)[-1]
        if (int(self.shell(f'blockdev --getsize64 {esp}')) != result['esp']['bytes']
                or int(self.shell(f'cat /sys/class/block/{node}/start')) * 512 != result['esp']['first_lba'] * BLOCK):
            raise ValueError('kernel partition extent differs from GPT')
        device = self.shell(f'stat -c %t:%T {esp}')
        if not re.fullmatch(r'[0-9a-fA-F]+:[0-9a-fA-F]+', device):
            raise ValueError('cannot identify ESP block device')
        devnum = ':'.join(str(int(n, 16)) for n in device.split(':'))
        if self.esp and (esp != self.esp or devnum != self.devnum):
            raise ValueError('ESP device identity changed')
        self.esp, self.devnum = esp, devnum
        validate_bpb(self.read_blocks(esp, 0, 1), result['esp']['blocks'])
        result['esp']['device'] = esp
        return result, primary, backup

    def mountinfo(self) -> list[dict]:
        return mounts(self.shell('cat /proc/self/mountinfo'))

    def ensure_unmounted(self) -> None:
        if any(m['device'] == self.devnum or m['target'] == '/esp' for m in self.mountinfo()):
            raise ValueError('ESP must be unmounted for the raw backup/write')

    def mount(self, readonly: bool) -> None:
        observed = self.mountinfo()
        relevant = [m for m in observed if m['device'] == self.devnum or m['target'] == '/esp']
        if any(m['target'] != '/esp' or m['device'] != self.devnum for m in relevant) or len(relevant) > 1:
            raise ValueError('ESP has another mountpoint or /esp belongs to another device')
        if relevant:
            self.shell('sync')
            self.shell('umount /esp')
        self.shell('mkdir -p /esp')
        mode = 'ro' if readonly else 'rw'
        self.shell(f'mount -t vfat -o {mode},nodev,nosuid {self.esp} /esp')
        actual = [m for m in self.mountinfo() if m['target'] == '/esp']
        if len(actual) != 1 or actual[0]['device'] != self.devnum or mode not in actual[0]['options']:
            raise ValueError('ESP mount mode/device verification failed')

    def unmount(self) -> None:
        observed = [m for m in self.mountinfo() if m['target'] == '/esp']
        if observed:
            if len(observed) != 1 or observed[0]['device'] != self.devnum:
                raise ValueError('refusing to unmount another device at /esp')
            self.shell('sync')
            self.shell('umount /esp')
        self.ensure_unmounted()

    def pull(self, remote: str, path: Path) -> None:
        subprocess.run(self.adb + ['pull', remote, str(path)], check=True, timeout=180,
                       stdout=subprocess.DEVNULL)

    def push(self, path: Path, remote: str) -> None:
        subprocess.run(self.adb + ['push', str(path), remote], check=True, timeout=180,
                       stdout=subprocess.DEVNULL)

    def digest(self, remote: str) -> str:
        output = self.shell('sha256sum ' + shlex.quote(remote)).split()
        if not output or not re.fullmatch('[0-9a-f]{64}', output[0]):
            raise ValueError('invalid recovery SHA256 result')
        return output[0]

    def backup_esp(self, path: Path, length: int) -> None:
        self.ensure_unmounted()
        command = f'dd if={self.esp} bs=1048576 2>/dev/null'
        with path.open('xb') as stream:
            subprocess.run(self.adb + ['exec-out', command], stdout=stream,
                           check=True, timeout=300)
        if path.stat().st_size != length or file_sha(path) != self.digest(self.esp):
            raise ValueError('complete ESP backup length/hash differs from the device')

    def write_esp(self, path: Path, length: int) -> None:
        self.ensure_unmounted()
        if path.stat().st_size != length:
            raise ValueError('candidate size differs from the existing partition')
        # One transfer only. An uncertain write is recorded and never retried.
        with path.open('rb') as stream:
            subprocess.run(self.adb + ['exec-in', 'dd', 'of=' + self.esp, 'bs=1048576'],
                           stdin=stream, check=True, timeout=300)
        self.shell('sync')
        self.shell(f'blockdev --flushbufs {self.esp}')


def install_config(device: Recovery, path: Path, before_hash: str, run_id: str) -> dict:
    backup = CONFIG + '.nabulog-' + run_id + '.bak'
    temporary = CONFIG + '.nabulog-' + run_id + '.tmp'
    backup_tmp = backup + '.tmp'
    try:
        device.mount(readonly=False)
        if device.digest(CONFIG) != before_hash:
            raise ValueError('installed boot config changed since backup')
        # Publish a verified backup and replacement through same-directory renames.
        device.shell(f'test ! -e {backup} && test ! -e {backup_tmp} && test ! -e {temporary}')
        device.shell(f'cp -p {CONFIG} {backup_tmp}')
        if device.digest(backup_tmp) != before_hash:
            raise ValueError('device boot config backup hash differs')
        device.shell('sync')
        device.shell(f'mv {backup_tmp} {backup}')
        device.push(path, temporary)
        expected = file_sha(path)
        if device.digest(temporary) != expected:
            raise ValueError('staged boot config hash differs')
        device.shell('sync')
        device.shell(f'mv -f {temporary} {CONFIG}')
        device.shell('sync')
        if device.digest(CONFIG) != expected:
            raise ValueError('installed boot config readback differs')
        return dict(backup=backup, sha256=expected, backup_sha256=before_hash)
    finally:
        device.unmount()


def execute(device: Recovery, output: Path, *, boot_config: Path | None = None, install: bool = False) -> dict:
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = dict(serial=device.serial, status='backing-up', installed=False, gpt_writes=False,
                  rebooted=False, requires_new_logger_kernel=True)
    manifest = output / 'deployment.json'
    def save():
        manifest.write_text(json.dumps(report, indent=2) + '\n')
    save()
    try:
        identity, primary, backup = device.identity()
        report['identity'] = identity
        (output / 'gpt-primary.before.bin').write_bytes(primary)
        (output / 'gpt-backup.before.bin').write_bytes(backup)
        # Read the actual active config intentionally, even with a supplied new one.
        before_config = output / 'unixnu.before.toml'
        try:
            device.mount(readonly=True)
            device.pull(CONFIG, before_config)
            if file_sha(before_config) != device.digest(CONFIG):
                raise ValueError('pulled boot config hash differs from device')
        finally:
            device.unmount()
        original_path = output / 'esp.before.img'
        device.backup_esp(original_path, identity['esp']['bytes'])
        report['before_esp_sha256'] = file_sha(original_path)
        report['before_config_sha256'] = file_sha(before_config)
        # A full FAT walk and ownership proof run only on the offline backup.
        candidate, prepared = prepare(original_path.read_bytes())
        if len(candidate) != identity['esp']['bytes'] or prepared['input_sha256'] != report['before_esp_sha256']:
            raise ValueError('offline preparer input/output extent differs')
        candidate_path = output / 'esp.candidate.img'
        candidate_path.write_bytes(candidate)
        del candidate
        seed = boot_config.read_bytes() if boot_config else before_config.read_bytes()
        if boot_config:
            (output / 'unixnu.package-source.toml').write_bytes(seed)
        patched = enable_config(seed.decode('utf-8-sig'), bytes.fromhex(prepared['token_hex'])).encode('utf-8')
        patched_path = output / 'unixnu.candidate.toml'
        patched_path.write_bytes(patched)
        report.update(status='prepared', preparation=prepared, config_source=str(boot_config) if boot_config else 'installed',
                      candidate_config_sha256=sha(patched), candidate_esp_sha256=file_sha(candidate_path))
        if report['candidate_esp_sha256'] != prepared['output_sha256']:
            raise ValueError('saved offline candidate hash differs')
        save()
        if not install:
            return report
        device.ensure_unmounted()
        now, current_primary, current_backup = device.identity()
        if now != identity or current_primary != primary or current_backup != backup:
            raise ValueError('GPT/ESP identity changed since backup')
        if device.digest(device.esp) != report['before_esp_sha256']:
            raise ValueError('ESP changed since backup; refusing stale candidate')
        report['status'] = 'esp-write-outcome-unknown'
        save()
        device.write_esp(candidate_path, identity['esp']['bytes'])
        actual = device.digest(device.esp)
        if actual != report['candidate_esp_sha256']:
            raise ValueError('written ESP full readback SHA256 differs; preserve backup and do not boot')
        report['esp_readback_sha256'] = actual
        # Confirm no write reached either GPT window before touching config files.
        after, after_primary, after_backup = device.identity()
        if after != identity or after_primary != primary or after_backup != backup:
            raise ValueError('GPT/ESP identity differs after ESP write')
        report['status'] = 'esp-verified-config-pending'
        save()
        report['config_install'] = install_config(device, patched_path, report['before_config_sha256'], uuid.uuid4().hex[:12])
        # Verify the config after unmount/remount, not only the cached rename.
        try:
            device.mount(readonly=True)
            installed_config = output / 'unixnu.installed.toml'
            device.pull(CONFIG, installed_config)
            if file_sha(installed_config) != report['candidate_config_sha256']:
                raise ValueError('persisted config readback differs after remount')
        finally:
            device.unmount()
        final, final_primary, final_backup = device.identity()
        if final != identity or final_primary != primary or final_backup != backup:
            raise ValueError('final GPT/ESP identity differs')
        report.update(status='installed-verified', installed=True, esp_unmounted=True)
        save()
        return report
    except BaseException as error:
        report['error'] = str(error)
        save()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', help='ADB serial; default is the sole online device')
    parser.add_argument('--adb', default='adb', help='ADB executable')
    parser.add_argument('--output', type=Path, required=True, help='new backup/candidate directory')
    parser.add_argument('--boot-config', type=Path, help='new package TOML as configuration source; actual device config is still backed up')
    parser.add_argument('--install', action='store_true', help='write and verify the prepared ESP and boot config')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('--output must be a new directory')
    try:
        result = execute(Recovery(args.serial, args.adb), args.output, boot_config=args.boot_config, install=args.install)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'ESP log setup failed: {error}', file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    print('Logging requires the new NABULOG2 ESP logger kernel. No reboot performed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
