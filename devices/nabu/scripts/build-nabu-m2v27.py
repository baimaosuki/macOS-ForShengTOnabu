#!/usr/bin/env python3
"""Build and sign the pinned arm64 macOS GPU userspace on an Intel Mac.

The output is an integration artifact. It is not automatically installed or
added to the ESP: the A640 kernel wire adapter and hardware proof remain gates.
"""
import argparse,hashlib,json,os,platform,struct,subprocess,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(Path(__file__).resolve().parent))
import importlib.util

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if platform.system()!='Darwin':raise SystemExit('Use a real macOS SDK/signing host')
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
    def run(*cmd,env=None):subprocess.run(list(map(str,cmd)),check=True,env=env)
    def read(*cmd):return subprocess.check_output(list(map(str,cmd)),text=True).strip()
    if read('xcrun','--sdk','macosx','--show-sdk-version')!='26.5':raise ValueError('Select SDK 26.5; OS target stays macOS 27')
    spec=importlib.util.spec_from_file_location('nabu_stage',Path(__file__).with_name('prepare-nabu-m2v27.py'));stage=importlib.util.module_from_spec(spec);spec.loader.exec_module(stage)
    upstream=out/'upstream';upstream.mkdir(exist_ok=True)
    def fetch(name,url,commit):
        dest=upstream/name
        if not dest.exists():run('git','init','-q',dest)
        present=subprocess.run(['git','-C',str(dest),'cat-file','-e',commit],capture_output=True).returncode==0
        if not present:run('git','-C',dest,'fetch','--depth=1',url,commit)
        if read('git','-C',dest,'status','--porcelain'):raise ValueError(f'Preserve modified scratch input: {dest}')
        run('git','-C',dest,'switch','--detach',commit)
        return dest
    core=fetch('UniXNU','https://github.com/UniXNU/UniXNU.git',stage.CORE)
    frontend=fetch('AdrenoMetal','https://github.com/UniXNU/AdrenoMetal.git',stage.FRONTEND)
    run('git','-C',core,'submodule','update','--init','--depth=1','--','drivers/Adreno740','drivers/GopFramebuffer','kits/AdrenoPM4')
    pins=json.loads((core/'scripts/data/gpu-metal-vulkan-inputs.json').read_text())
    mesa=fetch('mesa',pins['mesa']['repository'],stage.MESA)
    translator=fetch('metal2vulkan',pins['translator']['repository'],pins['translator']['commit'])
    tree=out/'tree'
    run(sys.executable,Path(__file__).with_name('prepare-nabu-m2v27.py'),'--core',core,'--frontend',frontend,'--output',tree)
    patch=tree/'patches/nabu/mesa-c823-darwin.patch'
    run('git','-C',mesa,'apply','--check',patch);run('git','-C',mesa,'apply',patch)
    (tree/'.cache/mesa-work').symlink_to(mesa,target_is_directory=True)
    run(sys.executable,'-m','venv','--system-site-packages',tree/'.cache/mesa-venv')
    run(tree/'.cache/mesa-venv/bin/python','-m','pip','install','meson==1.9.1','mako','packaging','PyYAML')
    run('rustup','toolchain','install','1.98.0','--profile','minimal','--component','rust-src','--target','aarch64-apple-darwin')
    priority=out/'queue-priority-host'
    run('xcrun','clang++','-std=c++17','-Wall','-Wextra','-Werror',tree/'drivers/AdrenoMetal/bridge/queue_priority_host.cpp','-o',priority)
    run(priority)
    run(sys.executable,tree/'scripts/build-turnip-darwin.py','--target','macos27')
    run(sys.executable,tree/'scripts/build-adreno-metal-plugin.py','--target','macos27','--no-replay','--translator-source',translator)
    artifacts=tree/'build/adreno-metal-plugin-macos27'
    code=artifacts/'NativeAdrenoMetal.bundle/Contents/MacOS'
    paths=[code/'NativeAdrenoMetal',code/'libvulkan_freedreno.dylib',artifacts/'adrenoprobed']
    paths+=list((artifacts/'Shader Cache.app/Contents/MacOS').iterdir())
    from capstone import Cs,CS_ARCH_ARM64,CS_MODE_ARM
    sys.path.insert(0,str(ROOT/'kits/XnuPrelink'))
    # The shared prelink parser is optional for this job; read text sections
    # directly so this stage has no dependency on an unrelated kernel checkout.
    cs=Cs(CS_ARCH_ARM64,CS_MODE_ARM);cs.skipdata=True;records=[]
    for path in paths:
        raw=path.read_bytes();magic,cpu,subtype,kind,count=struct.unpack_from('<5I',raw)
        if (magic,cpu,subtype)!=(0xfeedfacf,0x100000c,0):raise ValueError(f'Non-arm64 artifact: {path}')
        at=32;instructions=0
        for _ in range(count):
            cmd,size=struct.unpack_from('<II',raw,at)
            if cmd==0x19:
                for n in range(struct.unpack_from('<I',raw,at+64)[0]):
                    sect=at+72+80*n
                    if raw[sect:sect+16].rstrip(b'\0')==b'__text':
                        va,length,off=struct.unpack_from('<QQI',raw,sect+32)
                        for addr,_,mn,_ in cs.disasm_lite(raw[off:off+length],va):
                            instructions+=1
                            if mn.startswith(('pac','aut','braa','brab','blraa','blrab')) or mn in ('retaa','retab'):
                                raise ValueError(f'Authenticated instruction in {path}: {addr:#x} {mn}')
            at+=size
        run('codesign','--verify','--strict',path)
        records.append(dict(path=str(path.relative_to(artifacts)),sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw),text_instructions=instructions,pac_instructions=0))
    result=dict(passed=True,commit=read('git','-C',ROOT,'rev-parse','HEAD'),
        target='macos-27.3-26C5035r',sdk='26.5',abi='zeta-ordinary-v1',
        queue_priority_host_test=True,frontend_vulkan_runtime_tests=False,
        compile_link_passed=True,codesign_strict=True,files=records,
        kernel_wire_compatible=False,installed=False,hardware_tested=False)
    (out/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
