#!/usr/bin/env python3
"""Build the A640 hardware backend for macOS 27.3 with the native XNU recipe.

A6xx and board overlays remain separate licensed inputs. This first archive
contains BO/SMMU/GMU/CP proof code. It deliberately does not publish an ABI-v1
accelerator to the current ABI-v2 Metal runtime. Hardware starts only with
nabu-gpu=1; successful compilation does not qualify hardware acceleration.
"""
from pathlib import Path
import hashlib,json,shutil
CPP=('NativeAdrenoMemory','NativeAdrenoVm','NativeAdrenoCpu','NativeAdrenoKernelTime',
 'NativeAdrenoRing','NativeAdrenoCpInit','NativeAdrenoIrq','NativeAdrenoSmmuMemory',
 'NativeAdrenoSmmuContext','NativeAdrenoGmuMemory','NativeAdrenoGmuContext',
 'NativeAdrenoGpuContext','NativeAdrenoA6xxPower','NativeAdrenoA6xxGmu',
 'NativeAdrenoA6xxHwInit','NativeAdrenoA6xxSmmu','NativeAdrenoA6xxZap',
 'NativeAdrenoService','NativeAdrenoSha256','NativeAdrenoGpuProfile','NabuGpuResources')

def stage(root,out):
    source=root/'kits/nabu/gpu'
    if not source.is_dir():source=Path(__file__).resolve().parents[1]/'package/gpu'
    staged=out/'source';staged.mkdir(parents=True,exist_ok=True)
    inventory={}
    for directory in ('upstream/Adreno6xx','upstream/ArmSmmu4K','upstream/AdrenoPM4','a640','nabu'):
        for path in sorted((source/directory).iterdir()):
            if path.is_file() and path.suffix in ('.h','.c','.cpp','.json'):
                shutil.copyfile(path,staged/path.name)
                inventory[str(path.relative_to(source))]=hashlib.sha256(path.read_bytes()).hexdigest()
    identity=hashlib.sha256(json.dumps(inventory,sort_keys=True).encode()).hexdigest()
    (staged/'NabuGpuBuild.h').write_text('#define NABU_GPU_SOURCE_ID "'+identity+'"\n')
    logger=root/'drivers/ShengUfs'
    if not (logger/'NabuLogCore.h').exists():logger=Path(__file__).resolve().parents[1]/'drivers/ShengUfs'
    for name in ('NabuLogCore.h','NabuLogFormat.h'):shutil.copyfile(logger/name,staged/name)
    return source,staged,identity,inventory

def build(root,out,compile_cpp,compile_c,run):
    root=Path(root);out=Path(out)/'a640';out.mkdir(parents=True,exist_ok=True)
    source,staged,identity,inventory=stage(root,out)
    objects=[]
    extra=['-I',str(staged),'-mgeneral-regs-only','-DNABU_GPU_METAL=0']
    for name in CPP:
        obj=out/(name+'.o');compile_cpp(staged/(name+'.cpp'),obj,[*extra,'-std=gnu++17']);objects.append(obj)
    c_names=sorted(p.name for p in (source/'upstream/ArmSmmu4K').glob('*.c'))+['AdrenoPM4.c','NabuGpuStart.c']
    for name in c_names:
        obj=out/(Path(name).stem+'.o');compile_c(staged/name,obj,extra=extra);objects.append(obj)
    report=dict(target='macos-27.3-26C5035r',architecture='arm64',source_identity=identity,
        sources=inventory,objects=[dict(path=p.name,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in objects],
        a640_chip='0x06040001',gmem_bytes=1048576,trigger='nabu-gpu=1',native_wire_abi=1,
        metal_runtime_wire_abi=2,metal_publication_enabled=False,hardware_tested=False)
    (out/'build-report.json').write_text(json.dumps(report,indent=2)+'\n')
    return objects
