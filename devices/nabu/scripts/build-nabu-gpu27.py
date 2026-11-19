#!/usr/bin/env python3
"""Build the A640 hardware backend for macOS 27.3 with the native XNU recipe.

A6xx and board overlays remain separate licensed inputs. The archive includes the v2 BO/submit client and macOS accelerator, plus the
board GOP handoff callbacks. Hardware and publication start only with
nabu-gpu=1; successful compilation does not qualify hardware acceleration.
"""
from pathlib import Path
import hashlib,json,shutil,importlib.util,sys
CPP=('NativeAdrenoMemory','NativeAdrenoVm','NativeAdrenoCpu','NativeAdrenoKernelTime',
 'NativeAdrenoRing','NativeAdrenoCpInit','NativeAdrenoIrq','NativeAdrenoSmmuMemory',
 'NativeAdrenoSmmuContext','NativeAdrenoGmuMemory','NativeAdrenoGmuContext',
 'NativeAdrenoGpuContext','NativeAdrenoA6xxPower','NativeAdrenoA6xxGmu',
 'NativeAdrenoA6xxHwInit','NativeAdrenoA6xxSmmu','NativeAdrenoA6xxZap',
 'NativeAdrenoService','NativeAdrenoSha256','NativeAdrenoGpuProfile','NabuGpuResources',
 'NativeAdrenoTimer','NativeAdrenoUserClient','NativeAdrenoSubmit','NativeAdrenoScheduler','NativeAdrenoPublish','NativeAdrenoAccelerator','NabuGpuDisplayBridge')

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
    extra=['-I',str(staged),'-mgeneral-regs-only','-DNABU_GPU_METAL=1','-DXNU_TARGET_OS_OSX=1']
    for name in CPP:
        obj=out/(name+'.o');compile_cpp(staged/(name+'.cpp'),obj,[*extra,'-std=gnu++17']);objects.append(obj)
    # Register classes after the retained parent kext has loaded, never during
    # native-kernel static initialization. Bind only the audited macOS base.
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'kits/XnuPrelink'))
    from native_prelink import macho
    spec=importlib.util.spec_from_file_location('nabu_input_bindings',Path(__file__).with_name('build-nabu-input27.py'))
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    for name in ('NativeAdrenoPublish','NativeAdrenoAccelerator'):
        obj=out/(name+'.o');late=out/(name+'.late.o')
        helper.late_object(obj,late,macho,'__GLOBAL__sub_I_'+name+'.cpp')
        objects[objects.index(obj)]=late
    abi=json.loads((staged/'ioaccelerator-base-abi.json').read_text())
    assert abi['base_class']=='IOAccelerator' and abi['instance_bytes']==136 and abi['virtual_entries']==272 and not abi['operational_overrides']
    slide=int(abi['collection_slide'],16)
    bindings={'__ZN13IOAcceleratorC2EPK11OSMetaClass':int(abi['protected_compatible_constructor_aliases'][0],16)+slide,
              '__ZN13IOAcceleratorD2Ev':int(abi['nondeleting_destructor'],16)+slide,
              '__ZN13IOAccelerator10gMetaClassE':int(abi['metaclass'],16)+slide}
    text='.text\n.p2align 2\n';addresses=''
    for name,value in bindings.items():
        if name.endswith('gMetaClassE'):addresses+=f'.globl {name}\n.set {name}, {value:#x}\n'
        else:
            addresses+=f'.globl {name}.original\n.set {name}.original, {value:#x}\n'
            text+=f'.globl {name}\n{name}:\n bti c\n b {name}.original\n'
    for name,body in [('gpu-native-bindings',text),('native-addresses',addresses)]:
        src=out/(name+'.S');src.write_text(body);obj=out/(name+'.o')
        run(['xcrun','clang','-target','arm64-apple-macos14','-c',src,'-o',obj]);objects.append(obj)
    c_names=sorted(p.name for p in (source/'upstream/ArmSmmu4K').glob('*.c'))+['AdrenoPM4.c','NabuGpuStart.c']
    for name in c_names:
        obj=out/(Path(name).stem+'.o');compile_c(staged/name,obj,extra=extra);objects.append(obj)
    report=dict(target='macos-27.3-26C5035r',architecture='arm64',source_identity=identity,
        sources=inventory,objects=[dict(path=p.name,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in objects],
        a640_chip='0x06040001',gmem_bytes=1048576,trigger='nabu-gpu=1',native_wire_abi=2,
        metal_runtime_wire_abi=2,metal_publication_enabled=True,original_base_bindings={n:hex(v) for n,v in bindings.items()},hardware_tested=False)
    (out/'build-report.json').write_text(json.dumps(report,indent=2)+'\n')
    return objects
