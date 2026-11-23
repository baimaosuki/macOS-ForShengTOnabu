#!/usr/bin/env python3
"""Rebind only a moved ROOT path after rehashing the complete immutable image."""
import argparse
import json
from pathlib import Path
from nabu_root27 import save, sha


def relocate(report, image):
    if not report.get('complete') or report.get('abi') != 'zeta-ordinary-v1':
        raise ValueError('Only a complete conversion report can be relocated')
    if sha(image) != report['root_image_sha256']:
        raise ValueError('Moved ROOT differs from the original conversion report')
    return dict(report, root_image=str(image.resolve()),
                previous_root_image=report['root_image'], relocated_image_rehashed=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--root-image', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = relocate(json.loads(args.report.read_text()), args.root_image)
    result['source_report_sha256'] = sha(args.report)
    save(args.output, result)
    print('Moved ROOT rehashed; only the local prelink path was rebound')


if __name__ == '__main__':
    main()
