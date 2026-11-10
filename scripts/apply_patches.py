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
    """Copy the board storage headers next to every tree that compiles them.

    patches/ShengUfs rewrites NativeUfs.cpp to include NabuBoard.h,
    NabuUfsResources.h and NabuUfsWindowPlan.h, so those headers have to exist in
    the checkout before the patches are applied. Keeping the single copy in
    platform/ avoids a second, drifting copy inside a patch body.
    """
    source = ROOT / 'platform' / 'nabu' / 'macos26' / 'storage'
    if not source.is_dir():
        return
    # Two trees include these headers: the standalone component and the driver
    # the kernel build links out of UniXNU/drivers/NativeUfs.
    # NabuBoard.h is created by the board patch itself; staging it as well would
    # make that hunk fail on an already-present file.
    owned_by_a_patch = {'NabuBoard.h'}
    for target in (CHECKOUTS['ShengUfs'], ROOT / 'UniXNU' / 'drivers' / 'NativeUfs'):
        if not target.is_dir():
            continue
        for header in sorted(source.glob('Nabu*.h')):
            if header.name in owned_by_a_patch:
                continue
            destination = target / header.name
            payload = header.read_bytes()
            if destination.is_file() and destination.read_bytes() == payload:
                continue
            destination.write_bytes(payload)
            print(f'Header staged: {target.name}/{header.name}')


def stacked_reverse_applied(checkout: Path, patches: list[Path]) -> bool:
    """True when the whole tail of a patch series is already in the tree.

    Patches that stack on the same lines make a single-patch reverse check fail
    even though the work is committed to the checkout, so the tail is undone as
    one diff instead.
    """
    if len(patches) < 2:
        return False
    combined = b''.join(patch.read_bytes() for patch in patches)
    if (checkout / '.git').is_dir():
        result = subprocess.run(['git', '-C', str(checkout), 'apply', '--reverse', '--check', '-'],
                                input=combined, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        result = subprocess.run(['patch', '-p1', '-R', '--dry-run', '-s', '-'],
                                cwd=str(checkout), input=combined,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0


stage_board_headers()
for directory in sorted((ROOT / 'patches').iterdir()):
    if not directory.is_dir():
        continue
    checkout = CHECKOUTS.get(directory.name, ROOT / directory.name)
    if not (checkout / '.git').is_dir() and not (checkout / '.source-pin').is_file():
        print(f'Skip {directory.name}: checkout not fetched ({checkout.relative_to(ROOT)})')
        continue
    patches = sorted(directory.glob('*.patch'))
    for index, patch in enumerate(patches):
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
        if stacked_reverse_applied(checkout, patches[index:]):
            print(f'Already applied (stacked): {patch.name} .. {patches[-1].name}')
            break
        if (checkout / '.git').is_dir():
            subprocess.run(['git', '-C', str(checkout), 'apply', '--check', str(patch)], check=True)
            subprocess.run(['git', '-C', str(checkout), 'apply', str(patch)], check=True)
        else:
            subprocess.run(['patch', '-p1', '-N', '-s', '--fuzz=3', '--dry-run',
                            '-i', str(patch)], cwd=str(checkout), check=True)
            subprocess.run(['patch', '-p1', '-N', '-s', '--fuzz=3', '-r', '-',
                            '-i', str(patch)], cwd=str(checkout), check=True)
        print(f'Applied: {patch.name}')
