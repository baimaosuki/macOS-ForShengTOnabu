#!/usr/bin/env python3
"""Host conformance self-test for the nabu ARMv8.2 crypto emulator.

The emulator that ships in osfmk/arm64/nabu_v82_emul.c keeps its whole
decoder and semantics in a kernel-dependency-free region delimited by the
BEGIN/END NABU_V82_CORE comments, so the exact code that runs in the kernel
can be compiled and exercised on the build host:

  1. take the patched osfmk/arm64/nabu_v82_emul.c (or pull it out of
     platform/nabu/macos26/patches/zz-nabu-v82-crypto-emulation.patch when the
     XNU tree is pristine), and cut out the CORE region,
  2. generate random instruction words plus expected results from the
     reference model in nabu_v82_emul_testgen.py,
  3. compile nabu_v82_emul_test.c against that region and run it.

Exit status is 0 only when every vector matches.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PATCH = REPO / "platform/nabu/macos26/patches/zz-nabu-v82-crypto-emulation.patch"
SOURCE = REPO / "sources/macos26/xnu/osfmk/arm64/nabu_v82_emul.c"
EMUL_REL = "osfmk/arm64/nabu_v82_emul.c"
BEGIN = "BEGIN NABU_V82_CORE"
END = "END NABU_V82_CORE"


def extract_from_patch(patch):
    """Return the new file body recorded in the patch."""
    lines = []
    inside = False
    for line in Path(patch).read_text().splitlines():
        if line.startswith("+++ b/"):
            inside = line[len("+++ b/"):].strip() == EMUL_REL
            continue
        if inside and line.startswith("+"):
            lines.append(line[1:])
    if not lines:
        raise SystemExit("%s: no %s hunk" % (patch, EMUL_REL))
    return "\n".join(lines) + "\n"


def core_region(text):
    """Cut the NABU_V82_CORE block out of the emulator source."""
    start_marker = text.index(BEGIN)
    start = text.index("*/", start_marker) + len("*/")
    end_marker = text.index(END)
    end = text.rindex("/*", start_marker, end_marker)
    return text[start:end]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=None,
                        help="patched nabu_v82_emul.c (default: the XNU tree, "
                             "falling back to the patch file)")
    parser.add_argument("--workdir", type=Path, default=None,
                        help="scratch directory (default: out/nabu-v82-selftest)")
    parser.add_argument("--keep", action="store_true",
                        help="keep the scratch directory")
    parser.add_argument("--cc", default=os.environ.get("CC", "cc"))
    parser.add_argument("--vectors", type=int, default=128,
                        help="random vectors per instruction (default 128)")
    args = parser.parse_args()

    if args.source is not None:
        text = args.source.read_text()
    elif SOURCE.exists() and BEGIN in SOURCE.read_text():
        text = SOURCE.read_text()
    else:
        text = extract_from_patch(PATCH)

    workdir = args.workdir or (REPO / "out" / "nabu-v82-selftest")
    workdir = Path(workdir)
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)

    (workdir / "nabu_v82_emul_core.c").write_text(core_region(text))
    shutil.copy(REPO / "scripts" / "nabu_v82_emul_test.c", workdir)
    shutil.copy(REPO / "scripts" / "nabu_v82_emul_testgen.py", workdir)

    subprocess.run([sys.executable, "nabu_v82_emul_testgen.py", "vectors.txt",
                    str(args.vectors)], cwd=workdir, check=True)
    subprocess.run([args.cc, "-O2", "-std=gnu11", "-Wall", "-Wextra",
                    "-o", "nabu_v82_emul_test", "nabu_v82_emul_test.c"],
                   cwd=workdir, check=True)
    result = subprocess.run([str(workdir / "nabu_v82_emul_test"), "vectors.txt"],
                            cwd=workdir)
    if not args.keep and result.returncode == 0:
        shutil.rmtree(workdir, ignore_errors=True)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
