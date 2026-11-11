"""Exercise real GPT/BPB verification and the installer's write boundary."""
from pathlib import Path
import copy
import json
import shlex
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import install_nabu_esp_log as I


def gpt_pair(*, name='esp', type_guid=I.ESP_TYPE):
    blocks = 100000
    entries = bytearray(128 * 128)
    at = 30 * 128
    entries[at:at+16] = type_guid
    entries[at+16:at+32] = uuid.UUID(int=1).bytes_le
    struct.pack_into('<QQ', entries, at+32, 1024, 1024+76800-1)
    encoded = name.encode('utf-16-le')
    entries[at+56:at+56+len(encoded)] = encoded
    def window(current, other, table, first):
        data = bytearray(I.GPT_WINDOW_BLOCKS * I.BLOCK)
        header = bytearray(I.BLOCK)
        struct.pack_into('<8sIIII4Q16sQIII', header, 0, b'EFI PART', 0x10000, 92, 0, 0,
                         current, other, 6, blocks-6, uuid.UUID(int=2).bytes_le,
                         table, 128, 128, zlib.crc32(entries))
        struct.pack_into('<I', header, 16, zlib.crc32(header[:92]))
        off = (current-first) * I.BLOCK
        data[off:off+I.BLOCK] = header
        off = (table-first) * I.BLOCK
        data[off:off+len(entries)] = entries
        return bytes(data)
    return window(1, blocks-1, 2, 0), window(blocks-1, 1, blocks-5, blocks-I.GPT_WINDOW_BLOCKS), blocks


def bpb(blocks=76800):
    data = bytearray(I.BLOCK)
    struct.pack_into('<HBHB', data, 11, I.BLOCK, 1, 32, 2)
    struct.pack_into('<II', data, 32, blocks, 128)
    data[82:87] = b'FAT32'
    data[510:512] = b'\x55\xaa'
    return bytes(data)


class GptValidation(unittest.TestCase):
    def test_real_primary_and_backup_crc_entries_and_dynamic_extent(self):
        primary, backup, blocks = gpt_pair()
        result = I.validate_gpt(primary, backup, blocks)
        self.assertEqual(result['esp']['index'], 31)
        self.assertEqual(result['esp']['first_lba'], 1024)
        self.assertEqual(result['esp']['bytes'], 300 << 20)
        I.validate_bpb(bpb(), result['esp']['blocks'])

    def test_corruption_in_each_header_and_entry_array_is_rejected(self):
        primary, backup, blocks = gpt_pair()
        for which, offset in [('primary', I.BLOCK+32), ('primary', 2*I.BLOCK+30*128+56),
                              ('backup', len(backup)-I.BLOCK+32), ('backup', len(backup)-5*I.BLOCK+30*128+56)]:
            a, b = bytearray(primary), bytearray(backup)
            (a if which == 'primary' else b)[offset] ^= 1
            with self.subTest(which=which, offset=offset), self.assertRaisesRegex(ValueError, 'CRC'):
                I.validate_gpt(bytes(a), bytes(b), blocks)

    def test_individually_valid_but_disagreeing_copies_are_rejected(self):
        primary, _, blocks = gpt_pair()
        _, backup, _ = gpt_pair(name='esp-other')
        with self.assertRaisesRegex(ValueError, 'copies disagree'):
            I.validate_gpt(primary, backup, blocks)

    def test_wrong_type_or_name_is_rejected_with_valid_crcs(self):
        for options in (dict(name='ESP'), dict(type_guid=uuid.UUID(int=4).bytes_le)):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, 'type ESP and name esp'):
                I.validate_gpt(*gpt_pair(**options))

    def test_bpb_must_match_existing_partition_no_resize(self):
        with self.assertRaisesRegex(ValueError, 'no resize'):
            I.validate_bpb(bpb(98304), 76800)

    def test_resolved_partition_must_match_gpt_entry(self):
        primary, backup, blocks = gpt_pair()
        device = I.Recovery.__new__(I.Recovery)
        device.esp, device.devnum = '', ''
        replies = {'cat /sys/block/sda/queue/logical_block_size': '4096',
                   'blockdev --getsize64 /dev/block/sda': str(blocks*I.BLOCK),
                   'readlink -f /dev/block/by-name/esp': '/dev/block/sda30'}
        device.shell = lambda command: replies[command]
        device.read_blocks = lambda path, first, count: primary if first == 0 else backup
        with self.assertRaisesRegex(ValueError, 'resolved esp block node'):
            device.identity()

    def test_serial_auto_selection_and_ambiguity(self):
        with patch.object(I.subprocess, 'check_output', return_value='List of devices attached\nunit1\trecovery\n'):
            self.assertEqual(I.select_serial('adb', None), 'unit1')
        with patch.object(I.subprocess, 'check_output', return_value='List of devices attached\na\tdevice\nb\trecovery\n'):
            with self.assertRaisesRegex(ValueError, 'exactly one'):
                I.select_serial('adb', None)
        self.assertEqual(I.select_serial('adb', 'selected'), 'selected')


CONFIG = b'\xef\xbb\xbfversion = 1\n[boot]\ndefault = "nabu"\n[[driver]]\npath = "Drivers/DarwinBoot.efi"\n[[entry]]\nid = "nabu"\ntype = "xnu"\npath = "Payloads/kernel.macho"\nargs = "-v cpus=8 native-ufs-root=1"\n'
TOKEN = bytes(range(16))


class FakeRecovery:
    """Model mount, raw write, config publication and readback independently."""
    serial, esp = 'nabu-test', '/dev/block/sda31'
    def __init__(self):
        self.raw = b'old-esp'.ljust(I.BLOCK, b'\0')
        self.files = {I.CONFIG: CONFIG}
        self.events = []
        self.mounted = False
        self.stale = self.bad_readback = False
        self.wrote = False
    def identity(self):
        return dict(esp=dict(bytes=I.BLOCK, device=self.esp), disk_blocks=100000), b'primary-gpt', b'backup-gpt'
    def mount(self, readonly):
        self.mounted = True
        self.events.append(('mount', readonly))
    def unmount(self):
        self.mounted = False
        self.events.append(('unmount',))
    def ensure_unmounted(self):
        if self.mounted:
            raise ValueError('mounted')
    def pull(self, remote, path):
        self.events.append(('pull', remote))
        path.write_bytes(self.files[remote])
    def digest(self, remote):
        if remote == self.esp:
            if self.stale or self.bad_readback and self.wrote:
                return 'f' * 64
            return I.sha(self.raw)
        return I.sha(self.files[remote])
    def backup_esp(self, path, length):
        self.ensure_unmounted()
        assert length == len(self.raw)
        path.write_bytes(self.raw)
    def write_esp(self, path, length):
        self.ensure_unmounted()
        assert path.stat().st_size == length
        self.events.append(('write', self.esp))
        self.raw = path.read_bytes()
        self.wrote = True
    def push(self, path, remote):
        self.events.append(('push', remote))
        self.files[remote] = path.read_bytes()
    def shell(self, command):
        self.events.append(('shell', command))
        if command == 'sync':
            return ''
        if command.startswith('test '):
            for part in command.split(' && '):
                assert shlex.split(part)[-1] not in self.files
            return ''
        fields = shlex.split(command)
        if fields[0] == 'cp':
            self.files[fields[-1]] = self.files[fields[-2]]
        elif fields[0] == 'mv':
            self.files[fields[-1]] = self.files.pop(fields[-2])
        else:
            raise AssertionError(command)
        return ''


def mock_prepare(image):
    candidate = b'candidate'.ljust(len(image), b'\0')
    return candidate, dict(input_sha256=I.sha(image), output_sha256=I.sha(candidate), token_hex=TOKEN.hex())


class AdbBinaryCommands(unittest.TestCase):
    def device(self):
        device = I.Recovery.__new__(I.Recovery)
        device.adb = ['adb', '-s', 'nabu-test']
        device.esp = '/dev/block/sda31'
        device.ensure_unmounted = lambda: None
        return device

    def test_read_uses_one_complete_remote_command(self):
        device = self.device()
        raw = bytes(I.BLOCK)
        with patch.object(I.subprocess, 'check_output', return_value=raw) as run:
            self.assertEqual(device.read_blocks(I.LUN, 3, 1), raw)
        run.assert_called_once_with(
            ['adb', '-s', 'nabu-test', 'exec-out',
             'dd if=/dev/block/sda bs=4096 skip=3 count=1 2>/dev/null'], timeout=180)

    def test_backup_stream_is_binary_and_remote_dd_is_not_split(self):
        device = self.device()
        raw = b'\x00\xff$(not-executed)\n'.ljust(I.BLOCK, b'\x80')
        device.digest = lambda remote: I.sha(raw)
        def run(command, **kwargs):
            self.assertEqual(command, ['adb', '-s', 'nabu-test', 'exec-out',
                                      'dd if=/dev/block/sda31 bs=1048576 2>/dev/null'])
            self.assertIn('b', kwargs['stdout'].mode)
            self.assertNotIn('shell', kwargs)
            kwargs['stdout'].write(raw)
        with tempfile.TemporaryDirectory() as directory, patch.object(I.subprocess, 'run', side_effect=run) as mocked:
            path = Path(directory)/'esp.img'
            device.backup_esp(path, len(raw))
            self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(mocked.call_count, 1)

    def test_write_stream_uses_exec_in_dd_without_gnu_extensions(self):
        device = self.device()
        raw = b'\x00\xff$()\n'.ljust(I.BLOCK, b'\x80')
        commands = []
        device.shell = lambda command: commands.append(command) or ''
        def run(command, **kwargs):
            self.assertEqual(command, ['adb', '-s', 'nabu-test', 'exec-in', 'dd',
                                      'of=/dev/block/sda31', 'bs=1048576'])
            self.assertNotIn('shell', kwargs)
            self.assertEqual(kwargs['stdin'].mode, 'rb')
            self.assertEqual(kwargs['stdin'].read(), raw)
            self.assertTrue(kwargs['check'])
        with tempfile.TemporaryDirectory() as directory, patch.object(I.subprocess, 'run', side_effect=run) as mocked:
            path = Path(directory)/'esp.img'
            path.write_bytes(raw)
            device.write_esp(path, len(raw))
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(commands, ['sync', 'blockdev --flushbufs /dev/block/sda31'])

    def test_wrong_input_length_never_starts_dd(self):
        device = self.device()
        with tempfile.TemporaryDirectory() as directory, patch.object(I.subprocess, 'run') as run:
            path = Path(directory)/'esp.img'
            path.write_bytes(b'too short')
            with self.assertRaisesRegex(ValueError, 'candidate size'):
                device.write_esp(path, I.BLOCK)
        run.assert_not_called()


class DeploymentBoundary(unittest.TestCase):
    def test_default_prepares_backups_and_config_but_never_writes_or_pushes(self):
        device = FakeRecovery()
        before = copy.deepcopy(device.files)
        with tempfile.TemporaryDirectory() as directory, patch.object(I, 'prepare', side_effect=mock_prepare):
            out = Path(directory) / 'new'
            report = I.execute(device, out)
            self.assertEqual(report['status'], 'prepared')
            self.assertEqual((out/'esp.before.img').read_bytes(), device.raw)
            self.assertEqual((out/'unixnu.before.toml').read_bytes(), CONFIG)
            self.assertEqual((out/'gpt-primary.before.bin').read_bytes(), b'primary-gpt')
            config = (out/'unixnu.candidate.toml').read_text()
            self.assertIn('Drivers/DarwinBoot.efi', config)
            self.assertIn('cpus=8 native-ufs-root=1 nabu-log=1 nabu-log-token='+TOKEN.hex(), config)
            self.assertTrue(report['requires_new_logger_kernel'])
            self.assertFalse(report['installed'])
        self.assertEqual(device.files, before)
        self.assertFalse(device.mounted)
        self.assertFalse(any(e[0] in ('write', 'push') for e in device.events))

    def test_install_verifies_raw_then_backs_up_and_atomically_publishes_config(self):
        device = FakeRecovery()
        with tempfile.TemporaryDirectory() as directory, patch.object(I, 'prepare', side_effect=mock_prepare):
            base = Path(directory)
            supplied = base/'package.toml'
            supplied.write_bytes(CONFIG.replace(b'Payloads/kernel.macho', b'Payloads/kernel.new.macho'))
            out = base/'new'
            report = I.execute(device, out, install=True, boot_config=supplied)
            self.assertEqual(report['status'], 'installed-verified')
            self.assertEqual(report['esp_readback_sha256'], I.file_sha(out/'esp.candidate.img'))
            self.assertEqual(device.files[report['config_install']['backup']], CONFIG)
            self.assertEqual(device.files[I.CONFIG], (out/'unixnu.installed.toml').read_bytes())
            self.assertIn(b'kernel.new.macho', device.files[I.CONFIG])
            self.assertEqual((out/'unixnu.package-source.toml').read_bytes(), supplied.read_bytes())
            self.assertTrue(report['esp_unmounted'])
            self.assertFalse(report['rebooted'])
        self.assertFalse(device.mounted)
        self.assertEqual(sum(e[0] == 'write' for e in device.events), 1)
        self.assertLess(next(i for i,e in enumerate(device.events) if e[0]=='write'),
                        next(i for i,e in enumerate(device.events) if e[0]=='push'))
        self.assertTrue(any(e[0]=='shell' and e[1].startswith('mv -f ') for e in device.events))

    def test_changed_esp_is_refused_before_writing(self):
        device = FakeRecovery()
        device.stale = True
        with tempfile.TemporaryDirectory() as directory, patch.object(I, 'prepare', side_effect=mock_prepare):
            out = Path(directory)/'new'
            with self.assertRaisesRegex(ValueError, 'ESP changed since backup'):
                I.execute(device, out, install=True)
            self.assertFalse(json.loads((out/'deployment.json').read_text())['installed'])
        self.assertFalse(device.wrote)

    def test_failed_raw_readback_never_changes_boot_config(self):
        device = FakeRecovery()
        device.bad_readback = True
        with tempfile.TemporaryDirectory() as directory, patch.object(I, 'prepare', side_effect=mock_prepare):
            out = Path(directory)/'new'
            with self.assertRaisesRegex(ValueError, 'full readback SHA256 differs'):
                I.execute(device, out, install=True)
            report = json.loads((out/'deployment.json').read_text())
            self.assertEqual(report['status'], 'esp-write-outcome-unknown')
            self.assertEqual((out/'unixnu.before.toml').read_bytes(), CONFIG)
        self.assertEqual(device.files[I.CONFIG], CONFIG)
        self.assertFalse(any(e[0]=='push' for e in device.events))


if __name__ == '__main__':
    unittest.main()
