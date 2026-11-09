#!/usr/bin/env python3
"""Regenerate NABU-ESP-MANIFEST.json for the tracked UniXNU ESP tree.

Two files exceed GitHub's 100 MiB blob limit and cannot live in the repository
(the project remote is a public fork, which also refuses Git LFS uploads), so
they are recorded as external artifacts: pinned by sha256/md5, with the exact
command that reproduces or fetches them. Everything else is tracked normally.

Run from this directory: python3 make_manifest.py
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

PURPOSE = {
    "EFI/Boot/bootaa64.efi": (
        "rEFInd main boot loader at the UEFI default path \\EFI\\BOOT\\BOOTAA64.EFI; "
        "enumerates volumes and hands off to the UniXNU application"
    ),
    "EFI/Boot/bootmgfw-orig.efi": (
        "pristine Windows Boot Manager copy kept only for rollback; not used by the boot chain"
    ),
    "EFI/Boot/refind.conf": (
        "rEFInd configuration: menu entries, timeout, driver loading, "
        "UniXNU entry pointing at \\EFI\\UniXNU\\UniXNU.efi"
    ),
    "EFI/Boot/refind.conf.bak": "previous refind.conf revision kept for rollback",
    "EFI/Boot/vars/PreviousBoot": (
        "rEFInd runtime state recording the last booted entry; safe to delete"
    ),
    "EFI/UniXNU/UniXNU.efi": (
        "UEFI application: parses unixnu.toml, loads [[driver]] images, prepares the device tree, "
        "calls the loader protocol Boot() callback, owns the menu and countdown"
    ),
    "EFI/UniXNU/Drivers/DarwinBoot.efi": (
        "DXE driver exposing UNIXNU_LOADER_PROTOCOL: reads <kernel>.adt, validates the 16-byte "
        "/chosen/memory-map slots (RAMDisk, TrustCache, GPUAddon), allocates the owned window, "
        "lays out Mach-O segments, fills DarwinBootArgs and jumps into XNU"
    ),
    "EFI/UniXNU/Drivers/UniXNUDxe.efi": (
        "UniXNU DXE support driver: protocol registration and injection helpers"
    ),
    "EFI/UniXNU/HelloPayload.efi": (
        "smoke payload that only proves LoadImage/StartImage on the loader protocol works; "
        "not part of the boot chain"
    ),
    "EFI/UniXNU/Payloads/kernel.macho": (
        "current boot kernel: fileset kernel collection produced by the Uniform Slide emitter "
        "(uniform15); DarwinBoot places it at window + (vmaddr - min_va)"
    ),
    "EFI/UniXNU/Payloads/kernel.macho.adt": (
        "device tree paired with kernel.macho: /chosen/memory-map, darwinboot-memory-size = 1 GiB, "
        "panic-log node and native text console state; generated with the two-step recipe"
    ),
    "EFI/UniXNU/Payloads/kernel.prev.macho": (
        "previous kernel revision kept for rollback; not part of the boot chain"
    ),
    "EFI/UniXNU/Payloads/kernel.macho.adt.bak64": (
        "historical ADT variant, not used by the boot chain"
    ),
    "EFI/UniXNU/Payloads/kernel.macho.adt.new11424": (
        "historical ADT variant, not used by the boot chain"
    ),
    "EFI/UniXNU/unixnu.toml": (
        "authoritative UniXNU configuration: [[driver]] path Drivers/DarwinBoot.efi and the 12 boot "
        "arguments required by the kernel/ADT pair"
    ),
    "EFI/UniXNU/unixnu.toml.bak56": (
        "previous configuration revision; lacks nabu-source-text=1"
    ),
}

# Files that cannot be stored in the repository, pinned by content.
EXTERNAL = {
    "EFI/UniXNU/Payloads/kernel.macho": {
        "bytes": 112390144,
        "md5": "6c8fa3b38a9d47b2b1ff545a4e945f4e",
        "sha256": "16ced8e2796cc5b217aefb25483bcdcaf51ef873460147e8f876831191dff872",
        "obtain": (
            "rebuild with scripts/build_macos26_xnu.py && scripts/prelink_fileset_kernel.py "
            "(output out/nabu-uniform15.nabu), or copy out/EFI/UniXNU/Payloads/kernel.macho "
            "from an existing ESP; verify with fetch_payloads.py"
        ),
    },
    "EFI/UniXNU/Payloads/kernel.prev.macho": {
        "bytes": 29949952,
        "md5": "485cedc8a435be423908228671a4bb97",
        "sha256": "85a978c2cdc2f9a72a837135c429cdc9c1bd2f54f7e466e2610d52f55b3775d2",
        "obtain": "previous CI artifact kept on the device ESP; copy from an existing ESP and verify",
    },
}

ROLE = (
    "nabu macOS 26.5 ESP staging for the UniXNU route: rEFInd, UniXNU application, "
    "DarwinBoot loader, kernel collection and its paired device tree"
)


def digests(path: Path) -> tuple[str, str]:
    h256, hmd5 = hashlib.sha256(), hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h256.update(chunk)
            hmd5.update(chunk)
    return h256.hexdigest(), hmd5.hexdigest()


def main() -> None:
    files = []
    seen = set()
    for path in sorted(p for p in ROOT.rglob("*") if p.is_file()):
        rel = path.relative_to(ROOT).as_posix()
        if rel in {"NABU-ESP-MANIFEST.json", "README.md", "make_manifest.py", "fetch_payloads.py", ".gitignore"}:
            continue
        if rel not in PURPOSE:
            raise SystemExit(f"missing purpose for {rel}")
        seen.add(rel)
        entry = {"path": rel, "purpose": PURPOSE[rel]}
        sha256, md5 = digests(path)
        entry.update({"bytes": path.stat().st_size, "md5": md5, "sha256": sha256, "storage": "git"})
        if rel in EXTERNAL:
            pinned = EXTERNAL[rel]
            if entry["sha256"] != pinned["sha256"] or entry["bytes"] != pinned["bytes"]:
                raise SystemExit(
                    f"{rel} does not match the pinned artifact; update EXTERNAL deliberately"
                )
            entry["storage"] = "external"
        files.append(entry)

    for rel, pinned in EXTERNAL.items():
        if rel in seen:
            continue
        files.append(
            {
                "path": rel,
                "purpose": PURPOSE[rel],
                "bytes": pinned["bytes"],
                "md5": pinned["md5"],
                "sha256": pinned["sha256"],
                "storage": "external",
                "obtain": pinned["obtain"],
            }
        )
    files.sort(key=lambda item: item["path"])

    missing = sorted(set(PURPOSE) - {f["path"] for f in files})
    if missing:
        raise SystemExit(f"documented but absent: {missing}")

    manifest = {
        "role": ROLE,
        "flashing": False,
        "source": (
            "device /dev/block/sda31 (GPT partition 31, sectors 2654272-2731071, 300 MiB vfat), "
            "captured byte-for-byte"
        ),
        "external_binaries": (
            "kernel.macho (107 MiB) and kernel.prev.macho (28 MiB) exceed GitHub's 100 MiB blob "
            "limit and the fork refuses LFS uploads; they are pinned by hash and restored with "
            "fetch_payloads.py"
        ),
        "files": files,
    }
    out = ROOT / "NABU-ESP-MANIFEST.json"
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tracked = sum(1 for f in files if f["storage"] == "git")
    print(f"wrote NABU-ESP-MANIFEST.json: {len(files)} entries ({tracked} in git, "
          f"{len(files) - tracked} external)")


if __name__ == "__main__":
    main()
