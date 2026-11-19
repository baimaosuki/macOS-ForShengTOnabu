#!/usr/bin/env python3
"""Build/ABI/link-check NABU inputs locally without CI, packaging or hardware IO.

Uses a real Apple SDK, the prepared XNU headers, the current native kernel's
actual symbol table, and the original 26C5035r kernelcache ABI. The link output
is a closure-check executable, not a bootable kernel or a delivery payload.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys

def macho(data):
    magic,cpu,_,_,count=struct.unpack_from('<5I',data)
    if(magic,cpu)!=(0xfeedfacf,0x100000c):raise ValueError('Expected arm64 Mach-O')
    commands=[];segments=[];offset=32
    for _ in range(count):
        command,size=struct.unpack_from('<2I',data,offset);commands.append((command,offset,size))
        if command==0x19:
            sections=[]
            for i in range(struct.unpack_from('<I',data,offset+64)[0]):
                at=offset+72+80*i;va,length,fileoff=struct.unpack_from('<QQI',data,at+32)
                sections.append(dict(name=data[at:at+16].rstrip(b'\0').decode(),va=va,size=length,fileoff=fileoff,command_offset=at))
            segments.append(dict(sections=sections))
        offset+=size
    return dict(commands=commands,segments=segments)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sdkroot',type=Path,required=True)
    p.add_argument('--tree',type=Path,required=True,help='Assembled build tree containing kits/nabu and .cache/xnu-work')
    p.add_argument('--native-kernel',type=Path,required=True)
    p.add_argument('--kernelcache',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--clang',default='clang-17');p.add_argument('--clangxx',default='clang++-17')
    p.add_argument('--ar',default='llvm-ar-17');p.add_argument('--ld',default='ld64.lld-17')
    a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
    device=Path(__file__).resolve().parents[1];root=device.parents[1];x=a.tree/'.cache/xnu-work'
    spec=importlib.util.spec_from_file_location('nabu_inputs',Path(__file__).with_name('build-nabu-input27.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    sys.path.insert(0,str(root/'kits/XnuPrelink'));from kc_image import Image
    kc=a.kernelcache.read_bytes();sha=hashlib.sha256(kc).hexdigest()
    fixtures=[json.loads((Path(__file__).parent/name).read_text()) for name in ['hid-device-26C5035r.json','usb-xhci-26C5035r.json']]
    if any(f['kernelcache_sha256']!=sha for f in fixtures):raise ValueError('Original kernelcache differs from reviewed ABI')
    original=Image(kc);fixups=original.fixups()
    for f in fixtures:
        for i,entry in enumerate(f['slots']):
            if fixups[int(f['vtable'],16)+16+8*i][0]!=int(entry['target'],16):
                raise ValueError(f'Original slot mismatch {i}')
    del kc,original,fixups
    headers=a.sdkroot/'System/Library/Frameworks/Kernel.framework/Headers'
    common=[a.clangxx,'-target','arm64-apple-macos14','-isysroot',str(a.sdkroot),'-std=gnu++17',
        '-fno-exceptions','-fno-rtti','-fno-common','-fno-c++-static-destructors','-fapple-kext','-ffreestanding','-mgeneral-regs-only',
        '-DKERNEL=1','-DKERNEL_PRIVATE=1','-DDRIVER_PRIVATE=1','-DXNU_KERNEL_PRIVATE=1','-DPRIVATE=1','-DMACH_KERNEL_PRIVATE=1',
        '-D__arm64__=1','-D__LP64__=1','-D__DARWIN_UNIX03=1','-nostdinc++','-Wall','-Wextra','-Werror',
        '-Wno-unused-parameter','-Wno-deprecated-declarations','-Wno-unknown-warning-option','-Wno-unused-private-field',
        '-I',str(device/'package'),'-I',str(headers)]
    for part in ['osfmk','bsd','libkern','iokit','pexpert','.']:common+=['-I',str(x/part)]
    commands=[]
    def run(command,**kw):
        command=[str(v) for v in command]
        if command[:2]==['xcrun','clang']:command=[a.clang,*command[2:]]
        commands.append(command);return subprocess.run(command,check=True,**kw)
    def compile_cpp(src,dst,extra=()):run([*common,*extra,'-c',src,'-o',dst])
    objects=module.build(a.tree,out,compile_cpp,run,Path('/usr/bin'),'arm64-apple-macos14',str(a.sdkroot),macho)
    archive=out/'libNabuInput.local.a';run([a.ar,'rcs',archive,*objects])
    native_raw=a.native_kernel.read_bytes()
    native={n:v for v,names in Image(native_raw).symbols().items() for n in names}
    undefined=set();defined=set();definitions={}
    for path in objects:
        raw=path.read_bytes();_,sy=module.symbols(raw,macho)
        for n,value,kind in sy:
            if kind==1:undefined.add(n)
            elif kind&0xe:defined.add(n);definitions[n]=value
    missing=sorted(undefined-defined-set(native))
    if missing:raise ValueError(f'Unresolved native symbols: {missing}')
    # LLD's Mach-O address options reject the kernel's unsigned high addresses.
    # A common rebase of ALL actual addresses preserves every relative branch
    # and page relocation; it does not invent implementations for any import.
    rebase=0xfffffff000000000;link_objects=[]
    for path in objects:
        if path.name!='native-addresses.o':link_objects.append(path);continue
        raw=bytearray(path.read_bytes());image,_=module.symbols(raw,macho)
        st=next(pos for cmd,pos,_ in image['commands'] if cmd==2);symoff,count,_,_=struct.unpack_from('<4I',raw,st+8)
        for i in range(count):
            at=symoff+16*i
            if raw[at+4]&0xe==2:
                value=struct.unpack_from('<Q',raw,at+8)[0]
                if value<rebase:raise ValueError('Unexpected low native address')
                struct.pack_into('<Q',raw,at+8,value-rebase)
        rebased=out/'native-addresses.rebased.o';rebased.write_bytes(raw);link_objects.append(rebased)
    imports=undefined-defined
    assembly=''.join(f'.globl {n}\n.set {n}, {native[n]-rebase:#x}\n' for n in sorted(imports))
    source=out/'kernel-exports.S';source.write_text(assembly);exports=out/'kernel-exports.o'
    run([a.clang,'-target','arm64-apple-macos14','-c',source,'-o',exports])
    run([a.ld,'-arch','arm64','-execute','-e','_nabu_input_start','-platform_version','macos','14.0','26.5',
         '-no_fixup_chains','-undefined','error','-pagezero_size','0xe000000',*link_objects,exports,'-o',out/'input-closure-check.macho'])
    report=dict(result='pass',archive=str(archive),object_count=len(objects),missing_symbols=missing,
        native_kernel=str(a.native_kernel),native_sha256=hashlib.sha256(native_raw).hexdigest(),original_sha256=sha,
        common_link_rebase=hex(rebase),sdk=str(a.sdkroot),kernel_header_source=str(x),device_tested=False,
        bootable_kernel_built=False,ci_triggered=False,packaged=False)
    (out/'local-check.json').write_text(json.dumps(report,indent=2)+'\n')
    (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
    print(json.dumps(report,indent=2))
if __name__=='__main__':main()
