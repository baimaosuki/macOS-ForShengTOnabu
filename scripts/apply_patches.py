#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Apply integration patches once, refusing incompatible local changes."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CHECKOUTS = {'ShengPreflight': ROOT / 'UniXNU-sheng/drivers/ShengPreflight',
             'VerifiedRoot': ROOT / 'UniXNU/kits/VerifiedRoot',
             'ShengUfs': ROOT / 'sources/unixnu-components/ShengUfs'}


def stage_board_headers() -> None:
    """Copy the board storage headers next to the component they are built with.

    patches/ShengUfs rewrites NativeUfs.cpp to include NabuBoard.h,
    NabuUfsResources.h and NabuUfsWindowPlan.h, so those headers have to exist in
    the checkout before the patches are applied. Keeping the single copy in
    platform/ avoids a second, drifting copy inside a patch body.
    """
    source = ROOT / 'platform' / 'nabu' / 'macos26' / 'storage'
    target = CHECKOUTS['ShengUfs']
    if not source.is_dir() or not target.is_dir():
        return
    # NabuBoard.h is created by patches/ShengUfs/nabu-nativeufs-board.patch itself;
    # staging it as well would make that hunk fail. Everything else is ours.
    owned_by_a_patch = {'NabuBoard.h'}
    for header in sorted(source.glob('Nabu*.h')):
        if header.name in owned_by_a_patch:
            continue
        destination = target / header.name
        payload = header.read_bytes()
        if destination.is_file() and destination.read_bytes() == payload:
            print(f'Header current: {header.name}')
            continue
        destination.write_bytes(payload)
        print(f'Header staged: {header.name}')


stage_board_headers()
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
