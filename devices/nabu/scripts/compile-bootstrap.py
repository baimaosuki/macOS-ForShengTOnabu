#!/usr/bin/env python3
"""Compile the fresh upstream/PAC source on an Intel Mac before driver linkage.

The official bootstrap pass may stop at the final link because its external
archives do not exist yet. Every kernel translation unit must still succeed.
This command never calls that pass an installable kernel.
"""
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
    args = cli.parser().parse_args([
        "build", "kernel", "-D", str(ROOT / "devices/nabu"), "--os", "macos-27"
    ])
    ctx = Context(args)
    # Bootstrap needs only the selected shared components, never SM8550's GPU.
    selected = ["drivers/ArmDTPlatform", "drivers/ArmCpu", "drivers/RamBlock",
                "drivers/DarwinBoot", "drivers/GopFramebuffer",
                "drivers/AppleKeyStoreState", "drivers/SoftwareAES",
                "kits/XnuPrelink", "kits/VerifiedRoot"]
    subprocess.run(["git", "-C", str(ROOT), "submodule", "update", "--init", "--", *selected], check=True)
    modules = feeds.resolve(ctx)
    wanted = set(cli.REFERENCES["kernel"])
    feeds.fetch(ctx, modules, only={m.name for m in modules.values()
                                   if m.origin == "git" and
                                   (m.kind != "refer" or m.name in wanted)})
    tree.assemble(ctx, modules)
    order = patches.prepare(ctx, modules)
    env = dict(os.environ)
    env.update(kernel.xnu_env(ctx))
    env.update(kernel.version_env(modules, ctx))
    env.update(UNIXNU_PATCH_LIST=str(order), XNU_BOOTSTRAP="1",
               XNU_BOOT_PROGRESS="0", JOBS="4", ZERO_AR_DATE="1")
    result = subprocess.run(["bash", str(ctx.tree / "scripts/build-xnu-4k-baseline.sh")],
                            cwd=ctx.tree, env=env)
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
              "bootstrap_exit": result.returncode, "installable": False,
              "target": "arm64", "host": "x86_64", "os_build": "26C5035r"}
    (ctx.work / "bootstrap-verification.json").write_text(json.dumps(report, indent=2) + "\n")
    if missing:
        raise SystemExit("bootstrap file lists contain missing objects")


if __name__ == "__main__":
    main()
