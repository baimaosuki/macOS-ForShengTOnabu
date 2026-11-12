# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify the Xiaomi Pad 5 (nabu / SM8150) board-level merge into macOS-ForSheng."""
import ctypes
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from boot_native_experiment import boot_once
from build_macos26_storage import nabu_ufs_contract
from nabu_log_volume import (
    BLOCK, CAPACITY, CONTRACT, FILE_BYTES, SLOT_BYTES,
    check_config, decode_container, encode_slot, make_config, validate_contract,
)
from native_devtree import configure_native_text_state, make_nabu_adt
from native_smp import configure as configure_smp
from package_nabu_fastboot import package_nabu_fastboot
from test_native_devtree import read_tree


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class NabuBoardMergeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.baseline = json.loads((ROOT / 'config/nabu-baseline.json').read_text())

    def test_1_sources_and_baseline_pinning(self):
        lock = json.loads((ROOT / 'sources.lock.json').read_text())
        by_path = {entry['path']: entry for entry in lock['sources']}
        self.assertIn('UniXNU-nabu', by_path)
        self.assertIn('UniXNU-sm8150-common', by_path)
        self.assertEqual(by_path['UniXNU-nabu']['commit'], '54b4f98457f8e9e232cc0a077c20d08d0b2f0ac9')
        self.assertEqual(by_path['UniXNU-sm8150-common']['commit'], 'f363dcda920101a99e440ce3e8b68849122a3c0a')
        self.assertEqual(self.baseline['board'], 'nabu')
        self.assertEqual(self.baseline['soc'], 'SM8150')
        self.assertFalse(self.baseline['hardware_pac'])
        self.assertEqual(sha256_file(ROOT / 'config/nabu_defconfig'), self.baseline['defconfig_sha256'])
        self.assertEqual(sha256_file(ROOT / 'platform/nabu/macos26/dts/nabu.dts'), self.baseline['dts_sha256'])
        self.assertEqual(sha256_file(ROOT / 'platform/nabu/macos26/dts/sm8150.dtsi'), self.baseline['soc_dtsi_sha256'])

    def test_2_sm8150_devtree_and_memory_window_avoid_trustzone_hole(self):
        adt_path = self.work / 'nabu.adt'
        make_nabu_adt(adt_path, native_gop_display=True)
        configure_native_text_state(adt_path, development_root=True, native_gop_display=True)
        tree = read_tree(adt_path.read_bytes())
        root_node = tree[('device-tree',)]
        self.assertEqual(root_node['model'], b'Xiaomi Pad 5\0')
        self.assertEqual(root_node['compatible'], b'xiaomi,nabu\0qcom,sm8150\0')

        chosen = tree[('device-tree', 'chosen')]
        dram_base, = struct.unpack('<Q', chosen['dram-base'])
        dram_size, = struct.unpack('<Q', chosen['dram-size'])
        owned_size, = struct.unpack('<Q', chosen['darwinboot-memory-size'])
        hole_base, hole_size = struct.unpack('<QQ', chosen['trustzone-hole'])
        self.assertEqual(dram_base, 0xc0000000)
        self.assertEqual(dram_size, 0x140000000)
        self.assertEqual(owned_size, 0x40000000)
        self.assertEqual((hole_base, hole_base + hole_size), (0xb9900000, 0xc0000000))
        # Ensure XNU DRAM window does not overlap TrustZone hole [0xb9900000, 0xc0000000)
        self.assertGreaterEqual(dram_base, hole_base + hole_size)

        # Verify GOP framebuffer in Bank 0
        self.assertEqual(struct.unpack('<Q', chosen['framebuffer-base'])[0], 0x9c000000)
        self.assertEqual(struct.unpack('<I', chosen['framebuffer-width'])[0], 1600)
        self.assertEqual(struct.unpack('<I', chosen['framebuffer-height'])[0], 2560)
        self.assertEqual(struct.unpack('<I', chosen['framebuffer-stride'])[0], 6400)

        # Verify GICv3 intc node contract required by ArmDTPlatform & ArmCpu
        intc = tree[('device-tree', 'arm-io', 'interrupt-controller')]
        self.assertEqual(intc['name'], b'interrupt-controller\0')
        self.assertEqual(intc['interrupt-controller'], b'master\0')
        self.assertEqual(struct.unpack('<4Q', intc['reg']),
                         (0x17a00000, 0x10000, 0x17a60000, 0x100000))
        self.assertEqual(struct.unpack('<Q', intc['redistributor-stride'])[0], 0x20000)
        self.assertEqual(struct.unpack('<I', intc['virtual-timer-intid'])[0], 19)

        # Refuses DRAM window overlapping TrustZone hole
        with self.assertRaisesRegex(ValueError, 'TrustZone hole'):
            make_nabu_adt(self.work / 'bad.adt', dram_base=0x80000000, dram_size=0x80000000)

    def test_3_gicv3_sgi_ipi_and_8_core_kryo485_smp(self):
        patch_smp = ROOT / 'platform/nabu/macos26/patches/components/machine-cpu-smp-bootstrap.patch'
        patch_asm = ROOT / 'platform/nabu/macos26/patches/components/machine-cpu-inline-asm.patch'
        patch_timer = ROOT / 'platform/nabu/macos26/patches/components/nabu-armcpu-timer-intid19.patch'
        build = dict(
            cpu_smp_patch_sha256=sha256_file(patch_smp),
            cpu_inline_asm_patch_sha256=sha256_file(patch_asm),
            cpu_timer_intid_patch_sha256=sha256_file(patch_timer),
            cpu_init_completed_patch_sha256=sha256_file(patch_timer.parent / 'machine-cpu-init-completed.patch'),
            nabu_smp_source=dict(reset_affinity_bits=16, completion_after_all_registrations=True,
                                 virtual_timer_intid=19, psci_method='smc'),
            cpu_sgi_instructions={'verified': True},
            virtual_timer_intid=19,
            psci_method='smc',
        )
        result = configure_smp(build, 8, False, '-v cpus=1 keepsyms=1', board='nabu')
        self.assertEqual(result['board'], 'nabu')
        self.assertEqual(result['cpu_count'], 8)
        self.assertEqual(result['affinities'],
                         ['0x000', '0x100', '0x200', '0x300', '0x400', '0x500', '0x600', '0x700'])
        self.assertIn('cpus=8', result['boot_args'].split())
        self.assertIn('armcpu-self-ipi=1', result['boot_args'].split())
        self.assertFalse(result['spi_ipi_supported'])
        with self.assertRaisesRegex(ValueError, 'forbids SPI IPI'):
            configure_smp(build, 8, False, '-v cpus=1 armcpu-spi-ipi=1', board='nabu')

    def test_4_sm8150_ufs_resources_and_lba_windows(self):
        contract = nabu_ufs_contract(ROOT)
        self.assertEqual(contract['compatible'], 'qcom,sm8150-ufshc')
        self.assertEqual(contract['hc_base'], 0x01d84000)
        self.assertEqual(contract['gcc_ufs_window'], 0x00177000)
        self.assertEqual(contract['smmu_base'], 0x15000000)
        self.assertEqual(contract['stream_id'], 0x300)
        self.assertEqual(contract['logical_block_bytes'], 4096)
        self.assertFalse(contract['writes_allowed'])
        self.assertEqual(contract['windows']['os_lba'], 2739968)
        self.assertEqual(contract['windows']['os_sectors_512'], 11718723)
        self.assertEqual(contract['windows']['systemos_lba'], 4204809)
        self.assertEqual(contract['windows']['systemos_sectors_512'], 6451200)

        compiler = shutil.which('g++')
        if compiler:
            src = self.work / 'ufs_check.cpp'
            so = self.work / 'ufs_check.so'
            src.write_text(
                f'#include "{ROOT / "platform/nabu/macos26/storage/NabuBoard.h"}"\n'
                f'#include "{ROOT / "platform/nabu/macos26/storage/NabuUfsResources.h"}"\n'
                'extern "C" bool verify_nabu_ufs() {\n'
                '  NabuUfsResources r = {\n'
                '    {0x01d84000ULL, 0x2500ULL},\n'
                '    {0x00177000ULL, 0x1000ULL},\n'
                '    {0x15000000ULL, 0x100000ULL},\n'
                '    0x300U, 4096U\n'
                '  };\n'
                '  NabuUfsWindow w = {};\n'
                '  return nabuUfsResourcesMatch(r) &&\n'
                '         nabuUfsClocksReady(0x80000000U, 0x00000001U, 0x00000001U) &&\n'
                '         !nabuUfsClocksReady(0x00000000U, 0x00000001U, 0x00000001U) &&\n'
                '         nabu_ufs::kBoard.hcBase == 0x01d84000ULL &&\n'
                '         nabu_ufs::kBoard.streamId == 0x0300 &&\n'
                '         !nabu_ufs::kBoard.writesAllowed &&\n'
                '         nabuUfsWindowFromBytes(4096ULL * 5, 8192, 4096ULL * 1024, 64, w) &&\n'
                '         w.firstLba == 5 && w.blocks == 2 && w.headSkip == 0 && w.tailSlack == 0;\n'
                '}\n'
            )
            subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-shared', '-fPIC', str(src), '-o', str(so)], check=True)
            lib = ctypes.CDLL(str(so))
            lib.verify_nabu_ufs.restype = ctypes.c_bool
            self.assertTrue(lib.verify_nabu_ufs())

    def test_5_esp_nabulog2_contract_and_dual_slot_roundtrip(self):
        validate_contract(CONTRACT)
        token = bytes.fromhex('0123456789abcdef0123456789abcdef')
        boot_id = bytes.fromhex('fedcba9876543210fedcba9876543210')
        cfg = make_config(token)
        self.assertEqual(check_config(cfg), token)
        slot0 = encode_slot(token, 1, boot_id, b'early boot log slot 0\n')
        slot1 = encode_slot(token, 2, boot_id, b'darwin kernel ready on nabu SM8150\n')
        container = cfg + slot0 + slot1
        self.assertEqual(len(container), FILE_BYTES)
        payload, info = decode_container(container)
        self.assertEqual(payload, b'darwin kernel ready on nabu SM8150\n')
        self.assertEqual(info['selected_slot'], 1)
        self.assertEqual(info['sequence'], 2)
        self.assertTrue(info['stored_checksum_valid'])

        compiler = shutil.which('g++')
        if compiler:
            src = self.work / 'log_check.cpp'
            so = self.work / 'log_check.so'
            src.write_text(
                '#define NABU_LOG_IMPLEMENTATION\n'
                f'#include "{ROOT / "platform/nabu/macos26/log/NabuLogCore.h"}"\n'
                'extern "C" bool verify_nabu_log(const uint8_t *cfg, const uint8_t *slot, const uint8_t *tok) {\n'
                '  nabu_capture_append("hello nabu", 10);\n'
                '  struct nb_log_snapshot snap = {};\n'
                '  return nb_config_valid(cfg, tok) && nb_slot_valid(slot, tok) &&\n'
                '         nabu_capture_snapshot(&snap) && snap.length == 10;\n'
                '}\n'
            )
            subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-shared', '-fPIC', str(src), '-o', str(so)], check=True)
            lib = ctypes.CDLL(str(so))
            lib.verify_nabu_log.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
            lib.verify_nabu_log.restype = ctypes.c_bool
            self.assertTrue(lib.verify_nabu_log(cfg, slot1[:BLOCK], token))

    def test_6_nabu_fastboot_boot_temporary_ram_package_and_deploy(self):
        kernel = self.work / 'kernel.macho'
        kernel.write_bytes(b'NABU_KERNEL_PLACEHOLDER')
        out_dir = self.work / 'packaged'
        token = bytes.fromhex('11223344556677889900aabbccddeeff')
        record = package_nabu_fastboot(
            kernel, b'UEFI_FD_PAYLOAD', b'SM8150_NABU_DTB', out_dir,
            cpus=8, log_token=token
        )
        self.assertEqual(record['board'], 'nabu')
        self.assertEqual(record['image_name'], 'nabu-macos26-kernel.img')
        self.assertTrue(record['temporary_ram_boot'])
        self.assertFalse(record['partition_images_written'])
        self.assertIn('armcpu-self-ipi=1', record['boot_args'])
        self.assertIn('native-ufs-os-lba=2739968', record['boot_args'])
        self.assertIn(f'nabu-log-token={token.hex()}', record['boot_args'])

        deploy_out = self.work / 'deploy-record'
        with patch('boot_native_experiment.subprocess.run', side_effect=[
            subprocess.CompletedProcess([], 0, '', 'current-slot: a\n'),
            subprocess.CompletedProcess([], 0, '', 'slot-unbootable:a: no\n'),
            subprocess.CompletedProcess([], 0, 'Downloading boot.img OKAY\nBooting OKAY\n', ''),
        ]) as mocked_run:
            deploy = boot_once(out_dir / 'build.json', 'nabu-fastboot-serial', 'a', deploy_out)
        self.assertEqual(deploy['status'], 'boot-acknowledged')
        self.assertFalse(deploy['partition_writes'])
        self.assertFalse(deploy['slot_changes'])
        self.assertEqual(mocked_run.call_args_list[-1].args[0][-2:],
                         ['boot', str((out_dir / 'nabu-macos26-kernel.img').resolve())])


if __name__ == '__main__':
    unittest.main()
