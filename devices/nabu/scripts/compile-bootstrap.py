#!/usr/bin/env python3
"""Compile the fresh upstream/PAC source on an Intel Mac before driver linkage.

The official bootstrap pass may stop at the final link because its external
archives do not exist yet. Every kernel translation unit must still succeed.
This command never calls that pass an installable kernel.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
from unixnu import cli, feeds, kernel, patches, tree
from unixnu.config import Context


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full', action='store_true', help='also compile archives and link the native kernel')
    options = parser.parse_args()
    subprocess.run([sys.executable, str(ROOT / 'devices/nabu/tests/test_zeta_macos_sign27.py')], check=True)
    subprocess.run([sys.executable, str(ROOT / 'devices/nabu/tests/test_cpu_trace27.py')], check=True)
    import tempfile
    with tempfile.TemporaryDirectory(prefix='nabu-touch-host-') as temporary:
        pointer_test = Path(temporary) / 'pointer-test'
        subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                        str(ROOT / 'devices/nabu/tests/test_touch_pointer27.cpp'),
                        '-o', str(pointer_test)], check=True)
        subprocess.run([str(pointer_test)], check=True)
    with tempfile.TemporaryDirectory(prefix='nabu-input-host-') as temporary:
        test = Path(temporary) / 'hardware-test'
        subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                        '-I', str(ROOT / 'devices/nabu/package'),
                        str(ROOT / 'devices/nabu/tests/test_input_hardware27.cpp'), '-o', str(test)], check=True)
        subprocess.run([str(test)], check=True)
    subprocess.run([sys.executable, str(ROOT / 'devices/nabu/tests/test_power_hold27.py')], check=True)
    subprocess.run([sys.executable, str(ROOT / 'devices/nabu/tests/check-gpu-smmu27.py'),
                    '--output', str(ROOT / 'out/nabu27-gpu-smmu-host')], check=True)
    args = cli.parser().parse_args([
        "build", "kernel", "-D", str(ROOT / "devices/nabu"), "--os", "macos-27"
    ])
    ctx = Context(args)
    # Bootstrap needs only the selected shared components, never SM8550's GPU.
    selected = ["drivers/ArmDTPlatform", "drivers/ArmCpu", "drivers/RamBlock",
                "drivers/DarwinBoot", "drivers/GopFramebuffer",
                "drivers/AppleKeyStoreState", "drivers/SoftwareAES", "drivers/VirtioMmioBlock",
                "kits/XnuPrelink", "kits/VerifiedRoot"]
    subprocess.run(["git", "-C", str(ROOT), "submodule", "update", "--init", "--", *selected], check=True)
    modules = feeds.resolve(ctx)
    wanted = set(cli.REFERENCES["kernel"])
    feeds.fetch(ctx, modules, only={m.name for m in modules.values()
                                   if m.origin == "git" and
                                   (m.kind != "refer" or m.name in wanted)})
    tree.assemble(ctx, modules)
    order = patches.prepare(ctx, modules)
    subprocess.run([sys.executable, str(ROOT / 'devices/nabu/tests/test_desktop_stability27.py'),
                    '--xnu', str(ctx.tree / '.cache/xnu-work')], check=True)
    subprocess.run([sys.executable, str(ROOT / 'devices/nabu/tests/test_zeta_pac27.py'),
                    '--xnu', str(ctx.tree / '.cache/xnu-work'),
                    '--output', str(ctx.work / 'zeta-pac-verification')], check=True)
    subprocess.run([sys.executable, str(ROOT / 'devices/nabu/tests/test_user_va.py'),
                    '--xnu', str(ctx.tree / '.cache/xnu-work'),
                    '--work', str(ctx.work / 'user-va-verification')], check=True)
    subprocess.run([sys.executable, str(ROOT / 'devices/nabu/tests/test_lockdown_public.py'),
                    '--xnu', str(ctx.tree / '.cache/xnu-work'),
                    '--output', str(ctx.work / 'lockdown-public-verification')], check=True)
    subprocess.run([sys.executable, str(ROOT / 'devices/nabu/tests/test_windowserver_abort.py'),
                    '--xnu', str(ctx.tree / '.cache/xnu-work'),
                    '--output', str(ctx.work / 'windowserver-abort-verification')], check=True)
    subprocess.run([sys.executable, str(ROOT / 'devices/nabu/drivers/ShengUfs/tests/check_esp_log.py'),
                    '--output', str(ctx.work / 'esp-log-verification')], check=True)
    subprocess.run([sys.executable, str(ROOT / 'devices/nabu/tests/test_gpr_dispatch27.py'),
                    '--xnu', str(ctx.tree / '.cache/xnu-work'),
                    '--output', str(ctx.work / 'gpr-dispatch-verification')], check=True)
    env = dict(os.environ)
    env.update(kernel.xnu_env(ctx))
    env.update(kernel.version_env(modules, ctx))
    env.update(UNIXNU_PATCH_LIST=str(order), XNU_BOOTSTRAP="1",
               XNU_BOOT_PROGRESS="0", JOBS="4", ZERO_AR_DATE="1")
    if options.full:
        # SHENG 27 kernel.build bootstraps only when compile records/private
        # headers are missing. With a warm cache it compiles components first.
        kernel.build(ctx, modules)
        build_exit = 0
    else:
        result = subprocess.run(["bash", str(ctx.tree / "scripts/build-xnu-4k-baseline.sh")],
                                cwd=ctx.tree, env=env)
        build_exit = result.returncode
    obj = ctx.tree / "build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837"
    required = [obj / "osfmk/DEVELOPMENT/nabu_v82_emul.o",
                obj / "bsd/DEVELOPMENT/kern_exec.o",
                obj / "iokit/DEVELOPMENT/IOPlatformExpert.cpo",
                obj / "link.filelist"]
    if any(not path.is_file() for path in required):
        raise SystemExit("bootstrap did not compile the required production source objects")
    lists = list(obj.glob("*.filelist"))
    missing = []
    for listing in lists:
        for name in listing.read_text().splitlines():
            path = Path(name.strip())
            if path.suffix in (".o", ".cpo") and not path.is_file():
                missing.append(str(path))
    report = {"source_compile_passed": not missing, "missing_objects": missing,
              "bootstrap_exit": build_exit, "installable": False,
              "target": "arm64", "host": "x86_64", "os_build": "26C5035r"}
    (ctx.work / "bootstrap-verification.json").write_text(json.dumps(report, indent=2) + "\n")
    if missing:
        raise SystemExit("bootstrap file lists contain missing objects")
    if options.full:
        # The official full recipe has already built and linked the kernel.
        native = obj / 'mach.development.bcm2837.unstripped.noctf'
        if not native.is_file():
            raise SystemExit('full build did not produce the linked native kernel')
        import struct
        raw = native.read_bytes()
        position, commands = 32, set()
        for _ in range(struct.unpack_from('<I', raw, 16)[0]):
            command, length = struct.unpack_from('<II', raw, position)
            commands.add(command)
            position += length
        if 0x1e not in commands:
            raise SystemExit('native fileset input requires split-segment records')
        sys.path.insert(0, str(ROOT / 'kits/XnuPrelink'))
        from kc_image import Image
        from nabu_arm64_relocations import evidence
        relocation = evidence(Image(raw)) if 0x80000034 not in commands else {'format': 'chained'}
        import hashlib
        report.update(native_link_passed=True,
                      has_split_segment_relocations=True, has_chained_fixups=0x80000034 in commands,
                      native_relocation_input=relocation,
                      native_sha256=hashlib.sha256(native.read_bytes()).hexdigest(),
                      installable=False)
        (ctx.work / 'native-verification.json').write_text(json.dumps(report, indent=2) + '\n')
        subprocess.run([sys.executable, str(ROOT / 'devices/nabu/tests/check-a640-ring27.py'),
                        '--kernel', str(native), '--output', str(ctx.work / 'a640-ring-verification.json')], check=True)


if __name__ == "__main__":
    main()
