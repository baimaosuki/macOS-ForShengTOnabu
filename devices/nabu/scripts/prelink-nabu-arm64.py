#!/usr/bin/env python3
"""Use the upstream prelinker with NABU's exact plain-ARM64 local relocations."""
from pathlib import Path
import hashlib
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'kits/XnuPrelink'))
from kc_image import Image
from nabu_arm64_relocations import install_reader, evidence

install_reader(Image)
import fileset_emit

upstream_emit = fileset_emit.emit_images

# This module lives beside the copied prelink script in the assembled tree.
from fileset_segmented import SegmentedEmit
from nabu_zeta_fileset import lower_retained
upstream_seal = SegmentedEmit.seal_components


def seal_components(self):
    self.report['zeta_pac'] = lower_retained(self)
    upstream_seal(self)


SegmentedEmit.seal_components = seal_components


def require_zeta_runtime():
    report = os.environ.get('NABU_ZETA_RUNTIME_REPORT')
    if not report:
        raise ValueError('zeta PAC prelink requires NABU_ZETA_RUNTIME_REPORT for the converted runtime; old arm64e root is incompatible')
    evidence = json.loads(Path(report).read_text())
    if evidence.get('abi') != 'zeta-ordinary-v1' or not evidence.get('complete'):
        raise ValueError('zeta runtime conversion is incomplete')
    root = Path(evidence['root_image'])
    digest = hashlib.sha256()
    with root.open('rb') as f:
        for block in iter(lambda: f.read(8 << 20), b''):
            digest.update(block)
    if digest.hexdigest() != evidence['root_image_sha256']:
        raise ValueError('converted zeta runtime root hash mismatch')
    return evidence


def emit_images(raw, native, *args, **kwargs):
    runtime = require_zeta_runtime()
    image, report = upstream_emit(raw, native, *args, **kwargs)
    report['zeta_runtime'] = runtime
    source = Image(native)
    if source.command(0x80000034) is None:
        report['native_relocation_input'] = evidence(source)
    return image, report


fileset_emit.emit_images = emit_images
from emit_prelink import main

if __name__ == '__main__':
    main()
