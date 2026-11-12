# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the deployed NABU CPU source series and its registration barrier."""
import hashlib
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from audit_adt import parse_adt
from native_devtree import make_nabu_adt
from native_smp import audit_nabu_source, configure


class NabuSmp(unittest.TestCase):
    def test_real_source_series_and_registration_barrier(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            components = ROOT / 'platform/nabu/macos26/patches/components'
            for name in ('ArmCpu', 'ArmDTPlatform'):
                (work / name).mkdir()
                shutil.copy2(ROOT / 'UniXNU/drivers' / name / (name + '.cpp'), work / name)
            names = ('machine-driver-declarations.patch', 'machine-cpu-registration.patch',
                     'machine-cpu-interrupt-order.patch', 'machine-cpu-smp-bootstrap.patch',
                     'machine-cpu-inline-asm.patch', 'machine-cpu-gic-group.patch',
                     'nabu-armcpu-timer-intid19.patch', 'machine-cpu-init-completed.patch')
            for name in names:
                subprocess.run(['git', 'apply', '--unsafe-paths', '--directory=' + directory,
                                str(components / name)], check=True, capture_output=True)
            reset = work / 'osfmk/arm64/start.s'
            reset.parent.mkdir(parents=True)
            shutil.copy2(ROOT / 'sources/macos26/xnu/osfmk/arm64/start.s', reset)
            shutil.copy2(ROOT / 'sources/macos26/xnu/osfmk/arm64/machine_routines.c', reset.parent)
            subprocess.run(['git', 'apply', '--unsafe-paths', '--directory=' + directory,
                            str(ROOT / 'platform/nabu/macos26/patches/0005-nabu-secondary-reset-affinity.patch')],
                           check=True, capture_output=True)
            source_path = work / 'ArmCpu/ArmCpu.cpp'
            evidence = audit_nabu_source(work, source_path)
            sha = lambda name: hashlib.sha256((components / name).read_bytes()).hexdigest()
            build = dict(cpu_smp_patch_sha256=sha(names[3]), cpu_inline_asm_patch_sha256=sha(names[4]),
                         cpu_timer_intid_patch_sha256=sha(names[6]),
                         cpu_init_completed_patch_sha256=sha(names[7]),
                         cpu_sgi_instructions={'verified': True}, nabu_smp_source=evidence)
            for count in (2, 4, 8):
                result = configure(build, count, False, '-v cpus=1', board='nabu')
                self.assertIn('sheng-smp=1', result['boot_args'].split())
                self.assertIn(f'cpus={count}', result['boot_args'].split())
                self.assertFalse(result['hardware_qualified'])

            # Compile the actual barrier body, replacing only kernel consumers
            # with observable host stubs. Delayed/failing registrations must not
            # publish completion; concurrent final registration must publish once.
            compiler = shutil.which('g++')
            if not compiler:
                self.skipTest('requires a host C++ compiler for CPU barrier execution')
            source = source_path.read_text()
            helper = source[source.index('void ArmCpu::registrationReturned (void)'):
                            source.index('void ArmCpu::drainPending (void)')]
            harness = work / 'barrier.cpp'
            harness.write_text('''#include <cassert>
#include <cstdint>
#include <thread>
#include <vector>
volatile uint32_t armcpu_registration_count, armcpu_registration_complete;
volatile uint32_t armcpu_smp_fail[8];
volatile int32_t armcpu_smp_psci_rc[8];
unsigned maximum, completed, published;
int ml_get_max_cpu_number() { return maximum; }
void ml_cpu_init_completed() { ++completed; }
const char *gIOAllCPUInitializedKey = "IOAllCPUInitialized";
const bool kOSBooleanTrue = true;
struct IOService { static void publishResource(const char *, bool) { ++published; } };
struct ArmCpu { static void registrationReturned(); };
''' + helper + '''
void reset(unsigned count) {
 maximum=count-1; armcpu_registration_count=0; armcpu_registration_complete=0;
 completed=0; published=0;
 for (unsigned i=0;i<8;++i) { armcpu_smp_fail[i]=0; armcpu_smp_psci_rc[i]=0; }
}
int main() {
 for (unsigned n : {1U,2U,4U,8U}) {
  reset(n);
  for(unsigned i=0;i<n-1;++i) { ArmCpu::registrationReturned(); assert(completed==0); }
  ArmCpu::registrationReturned(); assert(completed==1 && published==1 && armcpu_registration_complete==1);
 }
 reset(8); armcpu_smp_psci_rc[7]=-3;
 for(unsigned i=0;i<8;++i) ArmCpu::registrationReturned();
 assert(completed==0 && published==0 && armcpu_registration_complete==0);
 reset(8); armcpu_smp_fail[3]=2;
 for(unsigned i=0;i<8;++i) ArmCpu::registrationReturned();
 assert(completed==0 && published==0);
 reset(8);
 std::vector<std::thread> workers;
 for(unsigned i=0;i<8;++i) workers.emplace_back(ArmCpu::registrationReturned);
 for(auto &worker: workers) worker.join();
 assert(completed==1 && published==1 && armcpu_registration_complete==1);
}
''')
            subprocess.run([compiler, '-std=c++17', '-pthread', '-Werror', str(harness),
                            '-o', str(work / 'barrier')], check=True, capture_output=True)
            subprocess.run([str(work / 'barrier')], check=True)

    def test_paired_adt_contains_real_cpu_affinities(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'nabu.adt'
            for count in (1, 2, 4, 8):
                make_nabu_adt(path, cpu_count=count)
                tree, size = parse_adt(path.read_bytes())
                self.assertEqual(size, path.stat().st_size)
                cpus = [props for name, props in tree.items() if name.startswith('/device-tree/cpus/cpu@')]
                self.assertEqual(len(cpus), count)
                for cpu in range(count):
                    props = tree[f'/device-tree/cpus/cpu@{cpu << 8:x}']
                    affinity, = struct.unpack('<I', props['reg'])
                    self.assertEqual(affinity, cpu << 8)
                    self.assertEqual(props['state'], b'running\0' if cpu == 0 else b'stopped\0')
                    self.assertEqual(props['compatible'], b'arm,cortex-a55\0' if cpu < 4 else b'arm,cortex-a76\0')
                    # The old low-byte reset mask aliases every secondary to CPU0.
                    self.assertEqual(affinity & 0xff, 0)
                    self.assertEqual(affinity & 0xffff, affinity)


if __name__ == '__main__':
    unittest.main()
