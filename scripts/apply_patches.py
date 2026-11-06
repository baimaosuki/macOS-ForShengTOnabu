#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Apply integration patches once, refusing incompatible local changes."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CHECKOUTS = {'ShengPreflight': ROOT / 'UniXNU-sheng/drivers/ShengPreflight',
             'VerifiedRoot': ROOT / 'UniXNU/kits/VerifiedRoot',
             'ShengUfs': ROOT / 'sources/unixnu-components/ShengUfs'}
for directory in sorted((ROOT / 'patches').iterdir()):
    if not directory.is_dir():
        continue
    checkout = CHECKOUTS.get(directory.name, ROOT / directory.name)
    if not (checkout / '.git').is_dir() and not (checkout / '.source-pin').is_file():
        print(f'Skip {directory.name}: checkout not fetched ({checkout.relative_to(ROOT)})')
        continue
    for patch in sorted(directory.glob('*.patch')):
        if (checkout / '.git').is_dir():
            reverse = subprocess.run(['git', '-C', str(checkout), 'apply', '--reverse', '--check', str(patch)],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            reverse = subprocess.run(['patch', '-p1', '-R', '--dry-run', '-s', '-i', str(patch)],
                                     cwd=str(checkout), stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        if reverse.returncode == 0:
            print(f'Already applied: {patch.name}')
            continue
        if (checkout / '.git').is_dir():
            subprocess.run(['git', '-C', str(checkout), 'apply', '--check', str(patch)], check=True)
            subprocess.run(['git', '-C', str(checkout), 'apply', str(patch)], check=True)
        else:
            subprocess.run(['patch', '-p1', '-N', '-s', '--fuzz=3', '--dry-run',
                            '-i', str(patch)], cwd=str(checkout), check=True)
            subprocess.run(['patch', '-p1', '-N', '-s', '--fuzz=3', '-r', '-',
                            '-i', str(patch)], cwd=str(checkout), check=True)
        print(f'Applied: {patch.name}')
