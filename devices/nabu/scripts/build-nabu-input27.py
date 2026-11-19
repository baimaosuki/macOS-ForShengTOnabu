#!/usr/bin/env python3
"""Compile all three NABU inputs, qualify original class slots, bind native ABI.

The caller supplies the actual kernel compile recipe and Mach-O parser. No
firmware bytes or mocked kernel headers are used in this archive.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct

def load(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def symbols(raw,macho):
    image=macho(raw);st=next(p for c,p,_ in image['commands'] if c==2)
    off,count,strings,_=struct.unpack_from('<4I',raw,st+8)
    names=[]
    for i in range(count):
        ix,kind,_,_,va=struct.unpack_from('<IBBHQ',raw,off+i*16)
        names.append((raw[strings+ix:raw.index(b'\0',strings+ix)].decode(),va,kind))
    return image,names

def qualify(raw,macho,klass,family,abi,required):
    image,sy=symbols(raw,macho)
    vtname=f'__ZTV{len(klass)}{klass}'
    vt=next(v for n,v,k in sy if n==vtname);entries={}
    for seg in image['segments']:
        for sec in seg['sections']:
            if sec['va']<=vt<sec['va']+sec['size']:
                off,count=struct.unpack_from('<2I',raw,sec['command_offset']+56)
                for i in range(count):
                    delta,bits=struct.unpack_from('<2I',raw,off+8*i)
                    if bits>>27&1 and bits>>25&3==3 and bits>>28==0:
                        if struct.unpack_from('<Q',raw,sec['fileoff']+delta)[0]:
                            raise ValueError(f'{klass}: authenticated relocation in plain arm64 object')
                        entries[sec['va']+delta]=sy[bits&0xffffff][0]
    slots=[entries.get(vt+16+i*8) for i in range(len(abi['slots']))]
    if not all(slots) or vt+16+len(slots)*8 in entries:
        raise ValueError(f'{klass}: original {len(slots)}-entry vtable not reproduced')
    slide=int(abi['collection_slide'],16)
    bindings={n:int(v,16)+slide for n,v in abi['bindings'].items()}
    own=[]
    for i,n in enumerate(slots):
        if n.startswith((f'__ZN{len(klass)}{klass}',f'__ZNK{len(klass)}{klass}')):own.append(i)
        elif f'{len(family)}{family}' in n:
            target=int(abi['slots'][i]['target'],16)+slide
            if n in bindings and bindings[n]!=target:raise ValueError(f'{n}: conflicting native target')
            bindings[n]=target
        elif i>=abi['base_slots']:raise ValueError(f'{klass}: unexpected native slot {i}: {n}')
    if not set(required)<=set(own):raise ValueError(f'{klass}: missing overrides {set(required)-set(own)}')
    undefined={n for n,v,k in sy if k==1 and f'{len(family)}{family}' in n}
    if not undefined<=set(bindings):raise ValueError(f'{klass}: unbound native imports {undefined-set(bindings)}')
    return {n:bindings[n] for n in undefined},own

def late_object(source,destination,macho,initializer):
    # LLVM <=18 cannot globalize Mach-O symbols. Keep section extents and every
    # instruction intact; turn only the auto-init pointer section into regular
    # data and export the constructor explicitly called after family loading.
    raw=bytearray(source.read_bytes());image,sy=symbols(raw,macho)
    found=False
    for seg in image['segments']:
        for sec in seg['sections']:
            if sec['name']=='__mod_init_func':
                off=sec['command_offset'];raw[off:off+16]=b'__nabu_init_once'.ljust(16,b'\0')
                flags=struct.unpack_from('<I',raw,off+64)[0]
                struct.pack_into('<I',raw,off+64,flags&~0xff)
                found=True
    if not found:raise ValueError(f'{source}: missing expected class initializer section')
    st=next(p for c,p,_ in image['commands'] if c==2)
    symoff,count,stroff,_=struct.unpack_from('<4I',raw,st+8)
    exported=False
    for i in range(count):
        off=symoff+16*i;index=struct.unpack_from('<I',raw,off)[0]
        name=raw[stroff+index:raw.index(0,stroff+index)].decode()
        if name==initializer:
            if raw[off+4]&0x0e!=0x0e:raise ValueError('Initializer is not section-defined')
            raw[off+4]|=1;exported=True
    if not exported:raise ValueError(f'{source}: class initializer symbol missing')
    destination.write_bytes(raw)

def build(root,out,compile_cpp,run,llvm,triple,sdk,macho):
    out=out/'input';out.mkdir(parents=True,exist_ok=True)
    fixtures=Path(__file__).parent
    hid=json.loads((fixtures/'hid-device-26C5035r.json').read_text())
    usb=json.loads((fixtures/'usb-xhci-26C5035r.json').read_text())
    if (hid['build'],hid['base_slots'],len(hid['slots']),hid['instance_size'])!=('26C5035r',272,334,0xd0):raise ValueError('Unqualified HID ABI')
    if (usb['build'],usb['base_slots'],len(usb['slots']),usb['instance_size'])!=('26C5035r',272,427,0xf88):raise ValueError('Unqualified XHCI ABI')
    (out/'OriginalHidDeviceABI.h').write_text(load('build-nabu-touch27').header(hid))
    (out/'OriginalUsbXhciABI.h').write_text(load('build-nabu-usb27').header(usb))
    objects=[];bindings={};report={}
    configurations=[('NabuTouch','NabuTouchHid','IOHIDDevice',hid,[0,1,7,22,190,191,275,277,284]),
        ('NabuPowerKey','NabuPowerHid','IOHIDDevice',hid,[0,1,7,22,190,191,275,277,284]),
        ('NabuUsbHost','NabuUsbXhci','AppleUSBXHCI',usb,[0,1,7,292,419,421])]
    for source,klass,family,abi,required in configurations:
        path=root/'kits/nabu'/(source+'.cpp');obj=out/(source+'.o')
        compile_cpp(path,obj,extra=['-I',str(out),'-fno-c++-static-destructors'])
        imports,own=qualify(obj.read_bytes(),macho,klass,family,abi,required)
        for n,v in imports.items():
            if n in bindings and bindings[n]!=v:raise ValueError(f'Different ABI bindings for {n}')
            bindings[n]=v
        late=out/(source+'.late.o')
        late_object(obj,late,macho,f'__GLOBAL__sub_I_{source}.cpp')
        objects.append(late)
        report[source]=dict(slots=len(abi['slots']),owned_slots=own,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    for source in ['NabuInputHardware','NabuInputStart']:
        obj=out/(source+'.o');compile_cpp(root/'kits/nabu'/(source+'.cpp'),obj,extra=['-I',str(out),'-fno-c++-static-destructors']);objects.append(obj)
    text='.text\n.p2align 2\n';addresses=''
    for n,v in sorted(bindings.items()):
        if n.startswith('__ZTV') or n.endswith('10gMetaClassE'):
            addresses+=f'.globl {n}\n.set {n}, {v:#x}\n'
        else:
            addresses+=f'.globl {n}.original\n.set {n}.original, {v:#x}\n'
            text+=f'.globl {n}\n{n}:\n bti c\n b {n}.original\n'
    for name,value in [('native-bindings',text),('native-addresses',addresses)]:
        src=out/(name+'.S');src.write_text(value);obj=out/(name+'.o')
        run(['xcrun','clang','-target',triple,'-isysroot',sdk,'-c',src,'-o',obj]);objects.append(obj)
    report.update(bindings={n:hex(v) for n,v in bindings.items()},architecture='arm64',build='26C5035r',device_tested=False)
    (out/'build-report.json').write_text(json.dumps(report,indent=2)+'\n')
    return objects
