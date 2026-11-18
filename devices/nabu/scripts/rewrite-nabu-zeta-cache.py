#!/usr/bin/env python3
"""Run pinned zeta shared-cache lowering with macOS ad-hoc signature repair."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from nabu_zeta_pac import ABI, PIN, module, require, selected_directory, rebuilt_signature


def signature_length(signature, shift, limit):
    cd, blobs = selected_directory(signature)
    kept = [blob for slot, blob in blobs.items()
            if slot != 0x10000 and int.from_bytes(blob[:4], 'big') != 0xfade0c02]
    hashes = int.from_bytes(cd[16:20], 'big')
    slots = (limit + (1 << shift) - 1) >> shift
    return 12 + 8 * (len(kept) + 1) + sum(map(len, kept)) + hashes + 32 * slots


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cache', type=Path, required=True)
    p.add_argument('--proof', type=Path, required=True)
    p.add_argument('--input-manifest', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--apple-trust-cache', type=Path, action='append', required=True)
    a = p.parse_args(argv)
    inputs = json.loads(a.input_manifest.read_text())
    require(inputs['abi'] == ABI and inputs['upstream_commit'] == PIN, 'wrong proof policy')
    for entry in inputs['files']:
        source = a.cache.parent / entry['name']
        digest = hashlib.sha256()
        with source.open('rb') as f:
            for block in iter(lambda: f.read(8 << 20), b''):
                digest.update(block)
        require(digest.hexdigest() == entry['sha256'], f'cache input changed: {entry["name"]}')
    summary = json.loads((a.proof / 'summary.json').read_text())
    entries = list(summary['images'].values()) + summary.get('islands', [])
    require(not any(e.get('unlowerable') for e in entries), 'proven cache code has unsupported PAC instructions')
    tool = module('zeta_cache_rewriter_macos', 'rewrite-zeta-dyld-cache.py')
    cache = tool.dc.Cache(a.cache)
    require([f.path.name for f in cache.files] == [e['name'] for e in inputs['files']], 'proof input manifest omits cache files')
    tool.rebuilt_signature = rebuilt_signature
    tool.signature_length = signature_length
    args = ['--cache', str(a.cache), '--proof', str(a.proof), '--output', str(a.output),
            '--report', str(a.report), '--page-shift', '12']
    for trust in a.apple_trust_cache:
        args += ['--apple-trust-cache', str(trust)]
    rc = tool.main(args)
    require(rc in (0, None), f'zeta cache rewrite returned {rc}')
    report = json.loads(a.report.read_text())
    report.update(abi=ABI, upstream_commit=PIN, input_manifest=str(a.input_manifest),
                  input_manifest_sha256=hashlib.sha256(a.input_manifest.read_bytes()).hexdigest(),
                  cms_replaced_with_adhoc=True, platform_identifier_preserved=True)
    a.report.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
