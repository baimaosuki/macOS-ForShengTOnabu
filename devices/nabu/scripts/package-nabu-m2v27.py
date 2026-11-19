#!/usr/bin/env python3
"""Package CI-signed GPU userspace for later integration, without installation.

The existing boot addon and Apple trust modules are not inputs or outputs.
This emits a separate addon and owner module; the kernel wire/hardware gate
must be completed before integrating them into a device ESP.
"""
import argparse,hashlib,json,os,struct,subprocess,sys,uuid,zlib
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--artifacts',type=Path,required=True)
    p.add_argument('--tools-tree',type=Path,required=True)
    p.add_argument('--expected-commit',required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    artifacts=a.artifacts.resolve();report=json.loads((artifacts/'verification.json').read_text())
    assert report['passed'] and report['codesign_strict'] and report['compile_link_passed']
    assert report['commit']==a.expected_commit and report['abi']=='zeta-ordinary-v1'
    assert not report['kernel_wire_compatible'] and not report['installed'] and not report['hardware_tested']
    plugin=artifacts/'tree/build/adreno-metal-plugin-macos27'
    for row in report['files']:
        path=plugin/row['path'];raw=path.read_bytes()
        assert hashlib.sha256(raw).hexdigest()==row['sha256']
        assert struct.unpack_from('<3I',raw)==(0xfeedfacf,0x100000c,0)
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    addon=out/'gpuaddon.bin'
    env=dict(os.environ,ADRENO_PLUGIN_DIR=str(plugin))
    subprocess.run([sys.executable,str(a.tools_tree.resolve()/'scripts/make-gpu-addon.py'),
                    '--no-daemon','--prefix','GPUBundles',str(addon)],env=env,check=True)
    # Check the actual compressed output and every installed byte/mode.
    raw=addon.read_bytes();magic,count,reserved,total=struct.unpack_from('<8sIIQ',raw)
    assert magic==b'NGPUADD2' and not reserved and total==len(raw)
    at=24;entries={}
    for _ in range(count):
        n,mode,size,packed=struct.unpack_from('<IIQQ',raw,at);at+=24
        name=raw[at:at+n];assert name.endswith(b'\0');name=name[:-1].decode();at+=(n+7)&~7
        body=zlib.decompress(raw[at:at+packed]);at+=(packed+7)&~7
        assert len(body)==size and name not in entries and '..' not in Path(name).parts
        assert not name.startswith('/') and not name.endswith('.publish')
        expected_mode=0o755 if Path(name).parent.name=='MacOS' or Path(name).name in ('NativeAdrenoMetal','libvulkan_freedreno.dylib') else 0o644
        assert mode==expected_mode,(name,mode)
        entries[name]=dict(mode=mode,sha256=hashlib.sha256(body).hexdigest(),bytes=size)
    assert at==len(raw)
    for name in entries:
        if name.startswith('GPUBundles/NativeAdrenoMetal.bundle/'):
            source=plugin/'NativeAdrenoMetal.bundle'/name.split('GPUBundles/NativeAdrenoMetal.bundle/',1)[1]
        elif name.startswith('Applications/Shader Cache.app/'):
            source=plugin/'Shader Cache.app'/name.split('Applications/Shader Cache.app/',1)[1]
        else:
            assert name=='Library/LaunchAgents/org.unixnu.ShaderCache.register.plist'
            continue
        assert hashlib.sha256(source.read_bytes()).hexdigest()==entries[name]['sha256']
    trust=json.loads((plugin/'trust.json').read_text())
    cache=json.loads((plugin/'shader-cache-trust.json').read_text())
    hashes=sorted({bytes.fromhex(trust['cdhash']),bytes.fromhex(trust['turnip_cdhash']),
                   *[bytes.fromhex(cache[name]) for name in ['ShaderCache','ShaderCacheWorker','ShaderCacheRegister']]})
    assert len(hashes)==5 and all(len(h)==20 for h in hashes)
    ident=uuid.uuid5(uuid.NAMESPACE_URL,'org.unixnu.nabu.m2v27:'+''.join(h.hex() for h in hashes))
    module=struct.pack('<I16sI',2,ident.bytes,len(hashes))+b''.join(h+bytes([2,0,0,0]) for h in hashes)
    (out/'owner-gpu.trustcache').write_bytes(module)
    result=dict(passed=True,source_commit=a.expected_commit,abi=report['abi'],
        addon_sha256=hashlib.sha256(raw).hexdigest(),owner_module_sha256=hashlib.sha256(module).hexdigest(),
        owner_cdhashes=[h.hex() for h in hashes],entries=entries,
        publish_marker=False,installed=False,esp_modified=False,hardware_tested=False,
        kernel_wire_compatible=False)
    (out/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='entries'},indent=2))

if __name__=='__main__':main()
