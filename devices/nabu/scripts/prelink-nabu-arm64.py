#!/usr/bin/env python3
"""Use the upstream prelinker with NABU's exact plain-ARM64 local relocations."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'kits/XnuPrelink'))
from kc_image import Image
from nabu_arm64_relocations import install_reader, evidence

install_reader(Image)
import fileset_emit

upstream_emit = fileset_emit.emit_images


def emit_images(raw, native, *args, **kwargs):
    image, report = upstream_emit(raw, native, *args, **kwargs)
    source = Image(native)
    if source.command(0x80000034) is None:
        report['native_relocation_input'] = evidence(source)
    return image, report


fileset_emit.emit_images = emit_images
from emit_prelink import main

if __name__ == '__main__':
    main()
