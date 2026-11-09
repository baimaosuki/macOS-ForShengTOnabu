#!/usr/bin/env python3
"""Restore the two ESP binaries that cannot live in Git.

kernel.macho (107 MiB) and kernel.prev.macho (28 MiB) exceed GitHub's 100 MiB
blob limit, and this project's remote is a public fork, which refuses Git LFS
uploads. They are therefore pinned by sha256 in NABU-ESP-MANIFEST.json and
restored here from one of these sources, in order:

  1. --from PATH        a file or directory to copy from (CI output, staging tree)
  2. --adb              pull from a booted TWRP/recovery device (/esp/EFI/UniXNU/...)
  3. --out-dir PATH     the local build output tree

Every restored file is verified against the manifest; a mismatch is fatal.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PAYLOADS = ROOT / "EFI" / "UniXNU" / "Payloads"
DEVICE_ROOT = "/esp/EFI/UniXNU/Payloads"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def external_entries() -> list[dict]:
    manifest = json.loads((ROOT / "NABU-ESP-MANIFEST.json").read_text(encoding="utf-8"))
    return [item for item in manifest["files"] if item.get("storage") == "external"]


def from_path(source: Path, name: str) -> Path | None:
    candidate = source / name if source.is_dir() else source
    if candidate.is_file():
        return candidate
    if source.is_dir():
        for found in source.rglob(name):
            return found
    return None


def from_adb(name: str) -> Path | None:
    adb = shutil.which("adb")
    if adb is None:
        print("adb not found on PATH", file=sys.stderr)
        return None
    target = Path("/tmp") / f"esp-{name}"
    result = subprocess.run(
        [adb, "pull", f"{DEVICE_ROOT}/{name}", str(target)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not target.is_file():
        print(f"adb pull failed for {name}: {result.stderr.strip()}", file=sys.stderr)
        return None
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="source", type=Path, help="file or tree to copy from")
    parser.add_argument("--out-dir", dest="out_dir", type=Path, help="local build output tree")
    parser.add_argument("--adb", action="store_true", help="pull from a booted recovery device")
    parser.add_argument("--check", action="store_true", help="only verify what is present")
    args = parser.parse_args()

    entries = external_entries()
    if not entries:
        print("manifest lists no external binaries")
        return 0

    failures = 0
    for entry in entries:
        name = Path(entry["path"]).name
        target = PAYLOADS / name
        if target.is_file() and sha256_of(target) == entry["sha256"]:
            print(f"ok      {name} (already present, sha256 matches)")
            continue
        if args.check:
            print(f"missing {name} (sha256 {entry['sha256'][:16]}…)")
            failures += 1
            continue

        candidate: Path | None = None
        if args.source is not None:
            candidate = from_path(args.source, name)
        if candidate is None and args.adb:
            candidate = from_adb(name)
        if candidate is None and args.out_dir is not None:
            candidate = from_path(args.out_dir, name)
        if candidate is None:
            print(f"missing {name}: no source provided (--from / --adb / --out-dir)", file=sys.stderr)
            failures += 1
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate, target)
        actual = sha256_of(target)
        if actual != entry["sha256"]:
            print(
                f"MISMATCH {name}: expected {entry['sha256']}, got {actual} "
                f"(source {candidate})",
                file=sys.stderr,
            )
            failures += 1
            continue
        print(f"ok      {name} restored from {candidate}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
