# SPDX-License-Identifier: GPL-3.0-or-later
"""Baseline verification and board contract tests for Xiaomi Pad 5 (nabu / SM8150)."""
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from build_macos26_storage import nabu_ufs_contract
from nabu_esp_log import (
    BLOCK as LOG_BLOCK, CAPACITY as LOG_CAPACITY, FILE_BYTES as LOG_FILE_BYTES,
    check_config, decode_container, encode_slot, make_config, validate_contract,
)
from native_devtree import configure_native_text_state, make_nabu_adt
from native_smp import configure as configure_smp
from test_native_devtree import read_tree


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class NabuBaseline(unittest.TestCase):
    def setUp(self):
        self.baseline = json.loads((ROOT / 'config/nabu-baseline.json').read_text())
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)

    def test_sources_and_defconfig_integrity(self):
        """Verify nabu baseline sources, lock pinning, and defconfig options."""
        lock = json.loads((ROOT / 'sources.lock.json').read_text())
        by_path = {entry['path']: entry for entry in lock['sources']}
        self.assertIn('UniXNU-nabu', by_path)
        self.assertIn('UniXNU-sm8150-common', by_path)
        self.assertEqual(by_path['UniXNU-nabu']['commit'], '54b4f98457f8e9e232cc0a077c20d08d0b2f0ac9')
        self.assertEqual(by_path['UniXNU-sm8150-common']['commit'], 'f363dcda920101a99e440ce3e8b68849122a3c0a')
        self.assertEqual(by_path['UniXNU-sm8150-common'].get('name'), 'sm8150')

        self.assertEqual(self.baseline['board'], 'nabu')
        self.assertEqual(self.baseline['soc'], 'SM8150')
        self.assertEqual(self.baseline['model'], 'Xiaomi Pad 5')
        self.assertEqual(self.baseline['compatible'], ['xiaomi,nabu', 'qcom,sm8150'])

        actual_defconfig = (ROOT / 'config/nabu_defconfig').read_bytes()
        self.assertEqual(hashlib.sha256(actual_defconfig).hexdigest(), self.baseline['defconfig_sha256'])

        upstream_nabu = ROOT / 'UniXNU-nabu'
        if upstream_nabu.is_dir():
            expected_defconfig = (upstream_nabu / 'configs/nabu_ufs_gpu_metal_defconfig').read_bytes()
            self.assertEqual(actual_defconfig, expected_defconfig)
            board_data = (upstream_nabu / 'board.toml').read_bytes()
            self.assertEqual(hashlib.sha256(board_data).hexdigest(), self.baseline['board_sha256'])
            board = tomllib.loads(board_data.decode())
            self.assertEqual(board['board']['name'], 'nabu')
            self.assertEqual(board['board']['soc'], 'SM8150')
            self.assertEqual(board['board']['description'], 'Xiaomi Pad 5')

        # Verify DTS files
        self.assertEqual(sha256_file(ROOT / 'dts/nabu.dts'), self.baseline['dts_sha256'])
        self.assertEqual(sha256_file(ROOT / 'dts/sm8150.dtsi'), self.baseline['soc_dtsi_sha256'])
        self.assertEqual(sha256_file(ROOT / 'platform/nabu/macos26/dts/nabu.dts'), self.baseline['dts_sha256'])
        self.assertEqual(sha256_file(ROOT / 'platform/nabu/macos26/dts/sm8150.dtsi'), self.baseline['soc_dtsi_sha256'])

        # Verify key defconfig symbols
        defconfig_lines = set(actual_defconfig.splitlines())
        for option in (
            'CONFIG_XNU_BUILD_ARCHIVES=y',
            'CONFIG_XNU_SOURCE_CORECRYPTO14=y',
            'CONFIG_XNU_SOURCE_PTHREAD=y',
            'CONFIG_XNU_ARM_DT_PLATFORM=y',
            'CONFIG_XNU_ARMCPU=y',
            'CONFIG_XNU_NATIVE_ARCH_TIMER=y',
            'CONFIG_XNU_NATIVE_PAN=y',
            'CONFIG_XNU_DT_PANIC_REGION=y',
            'CONFIG_XNU_NATIVE_UFS=y',
            'CONFIG_XNU_NATIVE_LEGACY_DISPLAY=y',
            'CONFIG_XNU_NATIVE_ADRENO=y',
            'CONFIG_XNU_SHENG_PMGR=n',
        ):
            self.assertIn(option.encode('utf-8'), defconfig_lines)

    def test_adt_generation_and_memory_window(self):
        """Verify SM8150 Apple DeviceTree generation, 3 DRAM banks, and TrustZone hole avoidance."""
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
        darwinboot_mem, = struct.unpack('<Q', chosen['darwinboot-memory-size'])
        hole_base, hole_size = struct.unpack('<QQ', chosen['trustzone-hole'])

        self.assertEqual(dram_base, 0xc0000000)
        self.assertEqual(dram_size, 0x140000000)
        self.assertEqual(darwinboot_mem, 0x40000000)
        self.assertEqual((hole_base, hole_base + hole_size), (0xb9900000, 0xc0000000))
        # XNU DRAM window must not overlap TrustZone carveout [0xb9900000, 0xc0000000)
        self.assertGreaterEqual(dram_base, hole_base + hole_size)

        # 3 physical DRAM banks recorded
        banks = struct.unpack('<6Q', chosen['nabu-dram-banks'])
        self.assertEqual(banks, (
            0x80000000, 0x3cc00000,
            0xc0000000, 0x80000000,
            0x140000000, 0xc0000000,
        ))

        # Stock UEFI GOP Framebuffer in Bank 0
        fb_base, = struct.unpack('<Q', chosen['framebuffer-base'])
        fb_size, = struct.unpack('<Q', chosen['framebuffer-size'])
        fb_w, = struct.unpack('<I', chosen['framebuffer-width'])
        fb_h, = struct.unpack('<I', chosen['framebuffer-height'])
        fb_stride, = struct.unpack('<I', chosen['framebuffer-stride'])
        self.assertEqual(fb_base, 0x9c000000)
        self.assertEqual(fb_size, 0x01000000)
        self.assertEqual(fb_w, 1600)
        self.assertEqual(fb_h, 2560)
        self.assertEqual(fb_stride, 6400)

        # GICv3 intc node contract
        intc = tree[('device-tree', 'arm-io', 'interrupt-controller')]
        self.assertEqual(intc['name'], b'interrupt-controller\0')
        self.assertEqual(intc['interrupt-controller'], b'master\0')
        self.assertEqual(struct.unpack('<4Q', intc['reg']),
                         (0x17a00000, 0x10000, 0x17a60000, 0x100000))
        self.assertEqual(struct.unpack('<Q', intc['redistributor-stride'])[0], 0x20000)
        self.assertEqual(struct.unpack('<I', intc['virtual-timer-intid'])[0], 19)

        # GPU ZAP region
        zap = tree[('device-tree', 'arm-io', 'zap-shader')]
        zap_base, zap_size = struct.unpack('<QQ', zap['reg'])
        self.assertEqual(zap_base, 0x98f15000)
        self.assertEqual(zap_size, 0x2000)

        # Reject illegal window overlapping TrustZone hole
        with self.assertRaisesRegex(ValueError, 'TrustZone hole'):
            make_nabu_adt(self.work / 'overlap.adt', dram_base=0x80000000, dram_size=0x80000000)

    def test_gicv3_sgi_ipi_and_8_core_smp(self):
        """Verify Nabu's 8-core Kryo 485 SMP, SMC PSCI, and GICv3 SGI IPI route."""
        patch_smp = ROOT / 'platform/nabu/macos26/patches/components/machine-cpu-smp-bootstrap.patch'
        patch_asm = ROOT / 'platform/nabu/macos26/patches/components/machine-cpu-inline-asm.patch'
        patch_timer = ROOT / 'platform/nabu/macos26/patches/components/nabu-armcpu-timer-intid19.patch'

        build = dict(
            cpu_smp_patch_sha256=sha256_file(patch_smp),
            cpu_inline_asm_patch_sha256=sha256_file(patch_asm),
            cpu_timer_intid_patch_sha256=sha256_file(patch_timer),
            cpu_init_completed_patch_sha256=sha256_file(patch_timer.parent / 'machine-cpu-init-completed.patch'),
            cpu_cold_entry_patch_sha256=sha256_file(patch_timer.parent / 'machine-cpu-cold-entry.patch'),
            nabu_smp_source=dict(reset_affinity_bits=16, completion_after_all_registrations=True,
                                 cold_entry_levels=[1, 2], translation_tables_published_to_poc=True,
                                 virtual_timer_intid=19, psci_method='smc'),
            cpu_sgi_instructions={'verified': True},
            virtual_timer_intid=19,
            psci_method='smc',
        )

        res = configure_smp(build, 8, False, '-v cpus=1 keepsyms=1', board='nabu')
        self.assertEqual(res['board'], 'nabu')
        self.assertEqual(res['soc'], 'SM8150')
        self.assertEqual(res['cpu_count'], 8)
        self.assertEqual(res['affinities'],
                         ['0x000', '0x100', '0x200', '0x300', '0x400', '0x500', '0x600', '0x700'])
        self.assertEqual(res['psci_method'], 'smc')
        self.assertEqual(res['virtual_timer_intid'], 19)
        self.assertIn('armcpu-self-ipi=1', res['boot_args'].split())
        self.assertIn('cpus=8', res['boot_args'].split())
        self.assertFalse(res['spi_ipi_supported'])

        # SPI IPI is strictly forbidden on Nabu GICv3
        with self.assertRaisesRegex(ValueError, 'forbids SPI IPI'):
            configure_smp(build, 8, False, '-v cpus=1 armcpu-spi-ipi=1', board='nabu')

    def test_sm8150_ufs_contract_and_lba_windows(self):
        """Verify SM8150 UFS host controller resources and unallocated LUN0 LBA windows."""
        contract = nabu_ufs_contract(ROOT)
        self.assertEqual(contract['board'], 'nabu')
        self.assertEqual(contract['soc'], 'SM8150')
        self.assertEqual(contract['compatible'], 'qcom,sm8150-ufshc')
        self.assertEqual(contract['hc_base'], 0x01d84000)
        self.assertEqual(contract['gcc_ufs_window'], 0x00177000)
        self.assertEqual(contract['smmu_base'], 0x15000000)
        self.assertEqual(contract['stream_id'], 0x300)
        self.assertEqual(contract['logical_block_bytes'], 4096)
        self.assertFalse(contract['writes_allowed'])

        windows = contract['windows']
        self.assertEqual(windows['os_lba'], 2739968)
        self.assertEqual(windows['os_sectors_512'], 11718723)
        self.assertEqual(windows['systemos_lba'], 4204809)
        self.assertEqual(windows['systemos_sectors_512'], 6451200)

        storage_headers = (
            'NabuBoard.h', 'NabuUfsResources.h', 'NabuUfsHostSafety.h',
            'NabuUfsQueue.h', 'NabuUfsService.h'
        )
        for h in storage_headers:
            path = ROOT / 'platform/nabu/macos26/storage' / h
            self.assertTrue(path.is_file(), f'Missing storage header {h}')

    def test_esp_nabulog2_contract_and_dual_slot(self):
        """Verify pre-allocated 334 MiB ESP NABULOG2 dual-slot CRC layout."""
        contract = json.loads((ROOT / 'config/nabulog.json').read_text())
        validate_contract(contract)
        self.assertEqual(contract['format'], 'NABULOG2')
        self.assertEqual(contract['esp_size_mib'], 334)
        self.assertEqual(contract['filename'], 'XNU2LOG.BIN')
        self.assertEqual(contract['file_bytes'], LOG_FILE_BYTES)
        self.assertEqual(contract['capacity_bytes'], LOG_CAPACITY)

        token = bytes.fromhex('00112233445566778899aabbccddeeff')
        boot_id = bytes.fromhex('ffeeddccbbaa99887766554433221100')
        cfg = make_config(token)
        self.assertEqual(check_config(cfg), token)

        slot0 = encode_slot(token, 1, boot_id, b'Early boot on Xiaomi Pad 5 (nabu)\n')
        slot1 = encode_slot(token, 2, boot_id, b'XNU kernel initialized with 8 cores\n')
        container = cfg + slot0 + slot1
        self.assertEqual(len(container), LOG_FILE_BYTES)

        payload, info = decode_container(container)
        self.assertEqual(payload, b'XNU kernel initialized with 8 cores\n')
        self.assertEqual(info['selected_slot'], 1)
        self.assertEqual(info['sequence'], 2)
        self.assertTrue(info['stored_checksum_valid'])


if __name__ == '__main__':
    unittest.main()
