#!/usr/bin/env python3
"""Merge previously signed M2V artifacts into an assembled nabu ESP directory."""
import argparse,hashlib,json,struct,tomllib,uuid,zlib
from pathlib import Path

def decode(raw):
    magic,count,reserved,total=struct.unpack_from('<8sIIQ',raw)
    assert magic==b'NGPUADD2' and reserved==0 and total==len(raw)
    out={};at=24
    for _ in range(count):
        n,mode,size,packed=struct.unpack_from('<IIQQ',raw,at);at+=24
        name=raw[at:at+n];assert name.endswith(b'\0');name=name[:-1].decode();at+=(n+7)&~7
        data=zlib.decompress(raw[at:at+packed]);at+=(packed+7)&~7
        assert len(data)==size and name not in out and not name.startswith('/') and '..' not in Path(name).parts
        out[name]=(mode,data)
    assert at==len(raw);return out

def encode(entries):
    chunks=[]
    for name,(mode,data) in entries.items():
        name=name.encode()+b'\0';packed=zlib.compress(data,9)
        chunks.append(struct.pack('<IIQQ',len(name),mode,len(data),len(packed))+name+bytes(-len(name)%8)+packed+bytes(-len(packed)%8))
    body=b''.join(chunks);return struct.pack('<8sIIQ',b'NGPUADD2',len(chunks),0,24+len(body))+body

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--esp',type=Path,required=True)
    p.add_argument('--signed-userspace',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    report=json.loads((a.signed_userspace/'verification.json').read_text())
    gpu=(a.signed_userspace/'gpuaddon.bin').read_bytes();owner=(a.signed_userspace/'owner-gpu.trustcache').read_bytes()
    assert report['passed'] and hashlib.sha256(gpu).hexdigest()==report['addon_sha256']
    assert hashlib.sha256(owner).hexdigest()==report['owner_module_sha256']
    payload=a.esp/'EFI/UniXNU/Payloads';addon=payload/'kernel.macho.gpuaddon';trust=payload/'kernel.macho.trustcache'
    original=decode(addon.read_bytes());merged=dict(original)
    for name,value in decode(gpu).items():
        assert name not in merged or merged[name]==value,name
        merged[name]=value
    addon.write_bytes(encode(merged));readback=decode(addon.read_bytes());assert readback==merged
    assert all(readback[k]==v for k,v in original.items())
    # Preserve every existing Apple/owner trust module. Append the exact signed
    # userspace module; no new executable or entitlement is manufactured here.
    trust_bytes=trust.read_bytes();count=struct.unpack_from('<I',trust_bytes)[0]
    offsets=struct.unpack_from('<'+'I'*count,trust_bytes,4)
    modules=[trust_bytes[start:(offsets[i+1] if i+1<count else len(trust_bytes))] for i,start in enumerate(offsets)]
    if owner not in modules:modules.append(owner)
    start=4+4*len(modules);indices=[]
    for module in modules:indices.append(start);start+=len(module)
    trust.write_bytes(struct.pack('<I',len(modules))+struct.pack('<'+'I'*len(indices),*indices)+b''.join(modules))
    menu=a.esp/'EFI/UniXNU/unixnu.toml';text=menu.read_text();config=tomllib.loads(text)
    cpu=next(e for e in config['entry'] if e.get('id')=='xnu')
    assert 'nabu-gpu=1' not in cpu['args'].split() and 'data-reset=1' not in cpu['args'].split()
    entry=dict(cpu);entry['id']='a640-metal';entry['title']='macOS 27.3 - A640 Metal GPU'
    tokens=[t for t in cpu['args'].split() if not t.startswith(('gpu-addon=','nabu-gpu=','nabu-gpu-delay=','fb-gpu=','fb-bgra=','gpu-metal=','dpu-scanout=','gpu-power=','vc-text=','exec-log='))]
    tokens+=['nabu-gpu=1','nabu-gpu-delay=0','gpu-addon=1','fb-gpu=1','fb-bgra=1','dpu-scanout=2','gpu-power=9','gpu-metal=1','ca-triple-buffer=1','ca-free-wakeup=1','log-gpu=1']
    entry['args']=' '.join(tokens)
    assert len(entry['args'].encode())<1024 and 'data-reset=1' not in tokens
    assert not any(e.get('id')==entry['id'] for e in config['entry'])
    menu.write_text(text+'\n[[entry]]\n'+''.join(k+' = '+json.dumps(v)+'\n' for k,v in entry.items()))
    result=dict(passed=True,signed_userspace_commit=report['source_commit'],
        addon_sha256=hashlib.sha256(addon.read_bytes()).hexdigest(),trust_sha256=hashlib.sha256(trust.read_bytes()).hexdigest(),
        preserved_entries=len(original),gpu_entries=len(merged)-len(original),owner_module_preserved=True,
        cpu_entry_preserved=True,gpu_entry=entry,hardware_tested=False)
    a.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))

if __name__=='__main__':main()
