#!/usr/bin/env python3
"""Build the NABU 512 MiB desktop test loader without modifying its checkout."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def build(output, llvm):
    output.mkdir(parents=True, exist_ok=False)
    source = output / 'source'
    checkout = ROOT / 'UniXNU/drivers/DarwinBoot'
    shutil.copytree(checkout, source, ignore=shutil.ignore_patterns('.git'))
    original = (source / 'DarwinBootPkg/Driver/DarwinBoot.c').read_text()
    changes = {'#define WINDOW_SIZE  SIZE_256MB':'#define WINDOW_SIZE  SIZE_512MB',
               'Request->KernelPath, SIZE_128MB':'Request->KernelPath, SIZE_512MB'}
    text = original
    for old,new in changes.items():
        if text.count(old)!=1: raise ValueError('unreviewed loader source: '+old)
        text=text.replace(old,new)
    (source / 'DarwinBootPkg/Driver/DarwinBoot.c').write_text(text)
    dsc=source/'DarwinBootPkg/DarwinBoot.dsc'
    dsc.write_text(dsc.read_text().replace('OUTPUT_DIRECTORY = build/DarwinBoot',
        'OUTPUT_DIRECTORY = '+str(output/'build')).replace('  Test/Validation.inf\n',''))
    conf=output/'conf';conf.mkdir()
    edk=ROOT/'uefi-sheng/Mu_Basecore/BaseTools'
    for name in ('target','tools_def','build_rule'):
        shutil.copy2(edk/'Conf'/(name+'.template'),conf/(name+'.txt'))
    env={**os.environ,'CLANG_BIN':str(llvm.resolve())+'/', 'WORKSPACE':str(ROOT),
        'EDK_TOOLS_PATH':str(edk),'PACKAGES_PATH':':'.join(map(str,(source,ROOT/'UniXNU/package',ROOT/'uefi-sheng/Mu_Basecore'))),
        'CONF_PATH':str(conf),'PYTHON_COMMAND':'python3',
        'PATH':str(edk/'BinWrappers/PosixLike')+':'+str(edk/'Source/C/bin')+':'+os.environ['PATH']}
    with (output/'build.log').open('w') as log:
        subprocess.run(['build','-p',str(dsc),'-a','AARCH64','-b','RELEASE','-t','CLANGPDB','-n','4'],
                       cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    binary=output/'build/RELEASE_CLANGPDB/AARCH64/DarwinBoot.efi'
    result=dict(source_revision=subprocess.check_output(['git','-C',checkout,'rev-parse','HEAD'],text=True).strip(),
        source_preimage_sha256=hashlib.sha256(original.encode()).hexdigest(),
        source_sha256=hashlib.sha256(text.encode()).hexdigest(),
        adaptations=changes,adaptations_sha256=hashlib.sha256(json.dumps(changes,sort_keys=True).encode()).hexdigest(),
        efi_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),efi_bytes=binary.stat().st_size,
        kernel_file_limit_bytes=512<<20,default_window_bytes=512<<20,
        actual_window_from_chosen=True,actual_conventional_memory_allocation_required=True,
        hardware_executed=False)
    (output/'build.json').write_text(json.dumps(result,indent=2)+'\n')
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--llvm',type=Path,default=Path('/usr/lib/llvm-17/bin'))
    a=p.parse_args();print(json.dumps(build(a.output.resolve(),a.llvm),indent=2))
