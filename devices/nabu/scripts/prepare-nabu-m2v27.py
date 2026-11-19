#!/usr/bin/env python3
"""Stage the pinned SHENG userspace for nabu's ordinary-pointer macOS 27 ABI.

This prepares build sources only. It does not publish a GPU service, deploy an
addon, or change the boot configuration. The A640 wire adapter is a separate gate.
"""
import argparse,hashlib,json,shutil,subprocess
from pathlib import Path

CORE='8542980d02b4d2cc5c32ee3542de1760b2562aac'
FRONTEND='cc0a4e8e57d20f4a4698753c2530875f2b10c0ca'
MESA='c823a83e8f373a5c36c2b9ed1d7dc98a4f3360ee'

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--core',type=Path,required=True)
    p.add_argument('--frontend',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.core=a.core.resolve();a.frontend=a.frontend.resolve();out=a.output.resolve()
    for source,expected in [(a.core,CORE),(a.frontend,FRONTEND)]:
        actual=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
        if actual!=expected:raise ValueError(f'Unexpected source revision: {source}: {actual}')
    out.mkdir(parents=True,exist_ok=False)
    shutil.copytree(a.core/'scripts',out/'scripts',ignore=shutil.ignore_patterns('__pycache__'))
    (out/'drivers').mkdir();(out/'kits').mkdir();(out/'.cache').mkdir()
    shutil.copytree(a.frontend,out/'drivers/AdrenoMetal',ignore=shutil.ignore_patterns('.git','__pycache__'))
    for rel in ['drivers/Adreno740','drivers/GopFramebuffer','kits/AdrenoPM4']:
        source=a.core/rel
        if not any(source.iterdir()):raise ValueError(f'Initialize pinned ABI/helper submodule: {rel}')
        entry=subprocess.check_output(['git','-C',str(a.core),'ls-tree',CORE,rel],text=True).split()
        revision=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
        if len(entry)<3 or entry[2]!=revision:raise ValueError(f'Submodule revision differs: {rel}')
        (out/rel).symlink_to(source,target_is_directory=True)
    patch=Path(__file__).resolve().parents[1]/'package/gpu/patches/mesa-c823-darwin.patch'
    staged_patch=out/'patches/nabu/mesa-c823-darwin.patch'
    staged_patch.parent.mkdir(parents=True);shutil.copyfile(patch,staged_patch)
    pins_path=out/'scripts/data/adreno-compiler-inputs.json'
    pins=json.loads(pins_path.read_text());pins['mesa']['commit']=MESA
    pins['mesa'].pop('tree',None);pins['patches']=['patches/nabu/mesa-c823-darwin.patch']
    pins_path.write_text(json.dumps(pins,indent=2)+'\n')
    def change(rel,old,new):
        path=out/rel;text=path.read_text()
        if text.count(old)!=1:raise ValueError(f'Upstream adaptation anchor changed: {rel}: {old}')
        path.write_text(text.replace(old,new))
    bridge='scripts/build-adreno-metal-bridge.py'
    change(bridge,"'macos27': 'arm64e-apple-darwin'","'macos27': 'aarch64-apple-darwin'")
    change(bridge,"ARM64E = args.target in ('ios27', 'macos27')","ARM64E = args.target == 'ios27'")
    change(bridge,"'lto = \"fat\"', 'codegen-units = 1'", "'lto = \"thin\"', 'codegen-units = 8'")
    change(bridge,"if ARM64E:\n    env['RUSTC_BOOTSTRAP'] = '1'", "if ARM64E:\n    env['RUSTC_BOOTSTRAP'] = '1'\nelif args.target == 'macos27':\n    env['RUSTFLAGS'] = '-C target-cpu=generic -C target-feature=-paca,-pacg,-bti'")
    change(bridge,"identity = dict(target=args.target,", "identity = dict(architecture='arm64' if args.target == 'macos27' else triple, abi='zeta-ordinary-v1' if args.target == 'macos27' else 'upstream', target=args.target,")
    revision="json.loads((ROOT / 'nabu-runtime-inputs.json').read_text())['adrenometal_commit']"
    change(bridge,"subprocess.check_output(['git', '-C', str(ROOT / 'drivers/AdrenoMetal'), 'rev-parse', 'HEAD'], text=True).strip()",revision)
    plugin='scripts/build-adreno-metal-plugin.py'
    change(plugin,"IOS27 = args.target in ('ios27', 'macos27')","IOS27 = args.target == 'ios27'")
    change(plugin,"metal_build_id = source_hash.hexdigest()", "metal_build_id = source_hash.hexdigest()\nif MACOS:\n    base += ['-mcpu=generic', '-march=armv8.2-a+crypto', '-mbranch-protection=none']")
    for script in [plugin,'scripts/build-turnip-darwin.py']:
        change(script,"'macos27': 'arm64e-apple-macos27.0'","'macos27': 'arm64-apple-macos27.0'")
    change('scripts/build-turnip-darwin.py',"    cross = ROOT / ('build/mesa-tu-%s.cross' % args.target)", "    if args.target == 'macos27':\n        flags += ['-mcpu=generic', '-march=armv8.2-a+crypto', '-mbranch-protection=none']\n    cross = ROOT / ('build/mesa-tu-%s.cross' % args.target)")
    change('scripts/build-turnip-darwin.py',"(k, flags) for k in ('c_args', 'cpp_args', 'objc_args', 'c_link_args', 'cpp_link_args',", "(k, flags + (['-Wl,-fixup_chains'] if args.target == 'macos27' and k.endswith('_link_args') else [])) for k in ('c_args', 'cpp_args', 'objc_args', 'c_link_args', 'cpp_link_args',")
    change(plugin,"subprocess.check_output(['git', '-C', str(ROOT / 'drivers/AdrenoMetal'), 'rev-parse', 'HEAD'], text=True).strip()",revision)
    change(plugin,"ROOT / 'drivers/AdrenoMetal/tools/build-shader-cache-app.py', OUT]", "ROOT / 'drivers/AdrenoMetal/tools/build-shader-cache-app.py', OUT, '--arch', 'arm64']")
    cache='drivers/AdrenoMetal/tools/build-shader-cache-app.py'
    change(cache,"sdk = subprocess.check_output(", "nabu_flags = ['-mcpu=generic', '-march=armv8.2-a+crypto', '-mbranch-protection=none'] if args.arch == 'arm64' else []\nsdk = subprocess.check_output(")
    change(cache,"subprocess.run([*base, SOURCE / 'ShaderCache' / source,", "subprocess.run([*base, *nabu_flags, SOURCE / 'ShaderCache' / source,")
    change(cache,"if name == 'ShaderCacheWorker' and args.arch == 'arm64e' else {}", "if name == 'ShaderCacheWorker' else {}")
    frontend='drivers/AdrenoMetal/NativeAdrenoMetalPlugin.mm'
    change(frontend,'        exceptiondiag::start();','        setenv("TU_DEBUG", "gmem,nocb", 0);\n        setenv("NATIVE_METAL_SINGLE_SAMPLE", "1", 0);\n        exceptiondiag::start();')
    change(frontend,'setenv("tu_allow_concurrent_binning", bootArgSet("tu-cb=0") ? "false" : "true", 1);','setenv("tu_allow_concurrent_binning", "false", 1);')
    change(frontend,'setenv("tu_enable_fast_border_color_for_undefined_formats", bootArgSet("tu-fbc=0") ? "false" : "true", 1);','setenv("tu_enable_fast_border_color_for_undefined_formats", "false", 1);')
    change(frontend,'else unsetenv("NATIVE_METAL_FLOW");','else setenv("NATIVE_METAL_FLOW", "3", 1);')
    record=dict(core_commit=CORE,adrenometal_commit=FRONTEND,mesa_commit=MESA,
        mesa_patch_sha256=hashlib.sha256(patch.read_bytes()).hexdigest(),target='macos27',
        clang_target='arm64-apple-macos27.0',rust_target='aarch64-apple-darwin',
        clang_cpu='generic',clang_arch='armv8.2-a+crypto',rust_target_cpu='generic',rust_target_features='-paca,-pacg,-bti',
        abi='zeta-ordinary-v1',panic='unwind',rust_pac_boundary=False,
        rust_lto='thin',rust_codegen_units=8,
        kernel_wire_compatible=False,deployment_enabled=False,hardware_tested=False)
    record['adapted_files']={rel:hashlib.sha256((out/rel).read_bytes()).hexdigest()
        for rel in [bridge,plugin,'scripts/build-turnip-darwin.py',frontend,cache]}
    (out/'nabu-runtime-inputs.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record,indent=2))

if __name__=='__main__':main()
