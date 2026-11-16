#!/usr/bin/env python3
"""Embed current UniXNU and DarwinBoot in a pristine Mu-Silicium NABU port."""
import hashlib
import json
import os
from pathlib import Path


root = Path(os.environ['UNIXNU_UEFI']).resolve()
board = root / 'Platforms/Xiaomi/nabuPkg'
modules = ['UniXNUPkg/Application/UniXNU/UniXNU.inf',
           'UniXNUPkg/Driver/UniXNUDxe/UniXNUDxe.inf',
           'UniXNUPkg/Driver/UniXNURamDiskDxe/UniXNURamDiskDxe.inf',
           'DarwinBootPkg/Driver/DarwinBoot.inf']
dsc = board / 'nabu.dsc'
text = dsc.read_text()
for module in modules:
    if module not in text:
        text += '\n[Components]\n  ' + module + '\n'
for entry in ('UniXNULib|UniXNUPkg/Library/UniXNULib/UniXNULib.inf',
              'FdtLib|MdePkg/Library/BaseFdtLib/BaseFdtLib.inf'):
    if entry not in text:
        text += '\n[LibraryClasses]\n  ' + entry + '\n'
dsc.write_text(text)
fdf = board / 'nabu.fdf'
text = fdf.read_text()
marker = '  !include Include/RAW.inc'
assert marker in text
extra = ''.join('\n  INF ' + module for module in modules if module not in text)
if extra:
    fdf.write_text(text.replace(marker, marker + '\n' + extra))
report = dict(modules=modules, device='nabu', sm8550_modules_added=False,
              source_sha256={str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                             for path in (dsc, fdf, root / 'Resources/DTBs/nabu.dtb')})
(board / 'unixnu-import.json').write_text(json.dumps(report, indent=2) + '\n')
print('NABU firmware: current UniXNU, initrd filesystem and DarwinBoot integrated')
