#!/usr/bin/env python3
"""Build and sign the pinned arm64 macOS GPU userspace on an Intel Mac.

The output is an integration artifact. It is not automatically installed or
added to the ESP: the A640 kernel wire adapter and hardware proof remain gates.
"""
import argparse,hashlib,json,os,platform,shutil,struct,subprocess,sys,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(Path(__file__).resolve().parent))
import importlib.util

BRIDGE_ARCHIVE='libadreno_metal_bridge.a'

def bridge_cache_policy(tree,tools):
    """Bind the reusable archive to the actual code, ABI and compiler inputs.

    The upstream builder separately checks its complete identity and archive
    hash before copying NMB_PREBUILT_BRIDGE. This key also binds compiler flags
    and tool versions that its upstream identity does not yet include.
    """
    policy=json.loads((tree/'nabu-runtime-inputs.json').read_text())
    files=[tree/'scripts/build-adreno-metal-bridge.py',
           tree/'scripts/data/gpu-metal-vulkan-inputs.json']
    for directory in ('drivers/AdrenoMetal/bridge','drivers/AdrenoMetal/shaders'):
        files+=sorted(p for p in (tree/directory).rglob('*') if p.is_file())
    return dict(version=1,sdk='26.5',tools=tools,
        target={key:policy[key] for key in ('adrenometal_commit','abi','rust_target',
            'rust_target_cpu','rust_target_features','rust_pac_boundary','rust_lto','rust_codegen_units')},
        sources={str(p.relative_to(tree)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files})

def cached_bridge(cache,policy):
    try:
        if json.loads((cache/'policy.json').read_text())!=policy:return None
        record=cache/(BRIDGE_ARCHIVE+'.json')
        expected=json.loads(record.read_text())['sha256']
        with (cache/BRIDGE_ARCHIVE).open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=expected:return None
        return record
    except (OSError,ValueError,KeyError,TypeError):
        return None

def save_bridge(cache,policy,source):
    cache.mkdir(parents=True,exist_ok=True)
    # Publish policy last, so a partial cache cannot be considered complete.
    for name in (BRIDGE_ARCHIVE,BRIDGE_ARCHIVE+'.json'):
        with tempfile.NamedTemporaryFile(dir=cache,delete=False) as stream:
            temporary=Path(stream.name)
        try:
            shutil.copyfile(source/name,temporary);os.replace(temporary,cache/name)
        finally:temporary.unlink(missing_ok=True)
    temporary=cache/'policy.json.tmp'
    temporary.write_text(json.dumps(policy,sort_keys=True,indent=2)+'\n')
    os.replace(temporary,cache/'policy.json')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--phase',choices=['all','prepare','turnip','bridge','plugin','verify'],default='all')
    a=p.parse_args()
    if platform.system()!='Darwin':raise SystemExit('Use a real macOS SDK/signing host')
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
    offsets={}
    def forward_logs():
        candidates=list((out/'tree/.cache').glob('mesa-tu-macos27-*.log'))
        candidates+=list((out/'tree/build/adreno-metal-bridge').glob('*.log'))
        for path in sorted(candidates):
            offset=offsets.get(path,0)
            if path.stat().st_size<offset:offset=0
            with path.open('rb') as stream:
                stream.seek(offset);chunk=stream.read(1024*1024);offsets[path]=stream.tell()
            if chunk:
                print('GPU BUILD LOG:',path.name,flush=True)
                print(chunk.decode(errors='replace'),end='',flush=True)
    def run(*cmd,env=None):
        command=list(map(str,cmd));print('GPU BUILD STEP:',*command,flush=True)
        process=subprocess.Popen(command,env=env)
        while process.poll() is None:
            forward_logs()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:pass
        forward_logs()
        if process.returncode:raise subprocess.CalledProcessError(process.returncode,command)
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
    tree=out/'tree'
    translator=upstream/'metal2vulkan'
    if a.phase in ('all','prepare'):
        core=fetch('UniXNU','https://github.com/UniXNU/UniXNU.git',stage.CORE)
        frontend=fetch('AdrenoMetal','https://github.com/UniXNU/AdrenoMetal.git',stage.FRONTEND)
        run('git','-C',core,'submodule','update','--init','--depth=1','--','drivers/Adreno740','drivers/GopFramebuffer','kits/AdrenoPM4')
        pins=json.loads((core/'scripts/data/gpu-metal-vulkan-inputs.json').read_text())
        mesa=fetch('mesa',pins['mesa']['repository'],stage.MESA)
        translator=fetch('metal2vulkan',pins['translator']['repository'],pins['translator']['commit'])
        run(sys.executable,Path(__file__).with_name('prepare-nabu-m2v27.py'),'--core',core,'--frontend',frontend,'--output',tree)
        run(sys.executable,ROOT/'devices/nabu/tests/check-gpu-userspace-policy27.py',
            '--stage',tree,'--clang',read('xcrun','--find','clang'),
            '--output',out/'cpu-policy-verification.json')
        run(sys.executable,ROOT/'devices/nabu/tests/check-gpu-bridge-cache27.py',
            '--stage',tree,'--output',out/'bridge-cache-verification.json')
        patch=tree/'patches/nabu/mesa-c823-darwin.patch'
        run('git','-C',mesa,'apply','--check',patch);run('git','-C',mesa,'apply',patch)
        (tree/'.cache/mesa-work').symlink_to(mesa,target_is_directory=True)
        run(sys.executable,'-m','venv','--system-site-packages',tree/'.cache/mesa-venv')
        run(tree/'.cache/mesa-venv/bin/python','-m','pip','install','meson==1.9.1','mako','packaging','PyYAML')
        run('rustup','toolchain','install','1.98.0','--profile','minimal','--component','rust-src','--target','aarch64-apple-darwin')
        run('cargo','+1.98.0','--version')
        priority=out/'queue-priority-host'
        run('xcrun','clang++','-std=c++17','-Wall','-Wextra','-Werror',tree/'drivers/AdrenoMetal/bridge/queue_priority_host.cpp','-o',priority)
        run(priority)
        # Keep Cargo's incremental release objects across retries without creating
        # a partial staging tree before its exact-source preparation.
        cargo_target=ROOT/'.ci-cache/nabu27-m2v-target'
        cargo_target.mkdir(parents=True,exist_ok=True)
        (tree/'build').mkdir(exist_ok=True)
        (tree/'build/m2v-target').symlink_to(cargo_target,target_is_directory=True)
        (out/'prepared.json').write_text(json.dumps(dict(commit=read('git','-C',ROOT,'rev-parse','HEAD'),policy=json.loads((tree/'nabu-runtime-inputs.json').read_text())),indent=2)+'\n')
    else:
        prepared=json.loads((out/'prepared.json').read_text())
        if prepared['commit']!=read('git','-C',ROOT,'rev-parse','HEAD'):
            raise ValueError('Prepared runtime sources belong to another commit')
    if a.phase=='prepare':return
    if a.phase in ('all','turnip'):
        run(sys.executable,tree/'scripts/build-turnip-darwin.py','--target','macos27')
    if a.phase=='turnip':return
    if a.phase in ('all','bridge'):
        cache=ROOT/'.ci-cache/nabu27-m2v-prebuilt'
        policy=bridge_cache_policy(tree,dict(rustc=read('rustc','+1.98.0','--version','--verbose'),
            clang=read('clang','--version'),apple_clang=read('xcrun','clang','--version')))
        prebuilt=cached_bridge(cache,policy)
        env=dict(os.environ)
        env.pop('NMB_PREBUILT_BRIDGE',None)
        if prebuilt:
            print('GPU BUILD CACHE: reusing source/ABI/toolchain-matched complete Rust archive',flush=True)
            env['NMB_PREBUILT_BRIDGE']=str(prebuilt)
        run(sys.executable,tree/'scripts/build-adreno-metal-bridge.py','--target','macos27','--source',translator,env=env)
        save_bridge(cache,policy,tree/'build/adreno-metal-bridge/macos27')
    if a.phase=='bridge':return
    if a.phase in ('all','plugin'):
        run(sys.executable,tree/'scripts/build-adreno-metal-plugin.py','--target','macos27','--no-replay','--reuse-bridge','--translator-source',translator)
    if a.phase=='plugin':return
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
                            if mn.startswith(('pac','aut','braa','brab','blraa','blrab')) or mn in ('retaa','retab','xpaci','xpacd'):
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
