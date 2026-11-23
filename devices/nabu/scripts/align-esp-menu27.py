#!/usr/bin/env python3
"""Install the pinned nabu menu into an extracted ESP; does not access partitions."""
import argparse
import json
import tomllib
from pathlib import Path
from nabu_boot_config27 import install, MENU_PATH


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--esp', required=True, type=Path, help='Existing extracted ESP root')
    parser.add_argument('--output', type=Path, help='Optional menu verification JSON')
    parser.add_argument('--local-boot-config', type=Path, help='Private deployed menu containing the existing log allocation identity')
    parser.add_argument('--without-persistent-log', action='store_true', help='Explicitly disable the ESP journal for a first build without provisioned logging')
    args = parser.parse_args()
    menu = args.esp / MENU_PATH
    reference = tomllib.loads(menu.read_text())
    if not any(entry.get('id') == 'a640-metal' for entry in reference.get('entry', [])):
        raise ValueError('Use an ESP with the integrated A640 GPU payload')
    report = install(args.esp, reference, args.local_boot_config, args.without_persistent_log)
    report['original_package_manifest_needs_update'] = True
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
