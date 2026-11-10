#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Assemble the paired 25F71 desktop windows (never a Recovery BaseSystem)."""
import argparse
import json
from pathlib import Path

from prepare_macos27_root import prepare_desktop


def prepare(spec: Path, destination: Path, *, assemble_image: bool = True) -> dict:
    return prepare_desktop(spec, destination, build='25F71', assemble_image=assemble_image)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('spec', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--no-image', action='store_true')
    args = parser.parse_args()
    print(json.dumps(prepare(args.spec, args.destination, assemble_image=not args.no_image), indent=2))
