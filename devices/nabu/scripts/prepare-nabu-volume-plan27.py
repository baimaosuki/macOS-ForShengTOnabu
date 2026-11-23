#!/usr/bin/env python3
"""Export a read-only volume or merge proven rewrites into its guest-write plan."""
import argparse
import json
from pathlib import Path
from nabu_root27 import export_volume, merge_plan, save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    export = commands.add_parser('export')
    export.add_argument('--root', type=Path, required=True)
    export.add_argument('--output', type=Path, required=True)
    export.add_argument('--plan', type=Path, required=True)
    merge = commands.add_parser('merge')
    merge.add_argument('--plan', type=Path, required=True)
    merge.add_argument('--overlay', type=Path, action='append', default=[])
    merge.add_argument('--cache-report', type=Path)
    merge.add_argument('--cache-prefix')
    merge.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'export':
        if args.plan.exists():
            raise FileExistsError(args.plan)
        result = export_volume(args.root, args.output)
        save(args.plan, result)
    else:
        if bool(args.cache_report) != bool(args.cache_prefix):
            parser.error('--cache-report and --cache-prefix are used together')
        result = merge_plan(json.loads(args.plan.read_text()),
                            [json.loads(p.read_text()) for p in args.overlay],
                            json.loads(args.cache_report.read_text()) if args.cache_report else None,
                            args.cache_prefix)
        save(args.output, result)
    print('Volume plan written; no APFS image or device modified')


if __name__ == '__main__':
    main()
