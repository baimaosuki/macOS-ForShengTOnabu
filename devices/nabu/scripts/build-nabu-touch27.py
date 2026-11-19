#!/usr/bin/env python3
"""Compile NABU's SPI pointer using the audited 26C5035r IOHIDDevice ABI.

Imported by the late display archive builder. No proprietary firmware is placed
in the source archive: the release assembler supplies ADT /nabu-touch locally.
"""
import hashlib
import json
from pathlib import Path
import struct


def header(abi):
    text='''// Generated from hid-device-26C5035r.json; no private field accesses.
#pragma once
#include <IOKit/IOService.h>
#include <IOKit/IOMemoryDescriptor.h>
enum IOHIDReportType { kIOHIDReportTypeInput=0 };
class IOHIDDevice : public IOService {
 OSDeclareAbstractStructors(IOHIDDevice)
 unsigned char originalPrivate[0xd0-sizeof(IOService)];
public:
 using IOService::setProperty;
 IOReturn Dispatch(IORPC) override;
 void free() override;
 bool init(OSDictionary *properties=nullptr) override;
 bool setProperty(const OSSymbol *,OSObject *) override;
 bool willTerminate(IOService *,IOOptionBits) override;
 bool didTerminate(IOService *,IOOptionBits,bool *) override;
 IOReturn configureReport(IOReportChannelList *,IOReportConfigureAction,void *,void *) override;
 IOReturn updateReport(IOReportChannelList *,IOReportUpdateAction,void *,void *) override;
 bool start(IOService *) override;
 void stop(IOService *) override;
 bool handleOpen(IOService *,IOOptionBits,void *) override;
 void handleClose(IOService *,IOOptionBits) override;
 bool handleIsOpen(const IOService *) const override;
 bool matchPropertyTable(OSDictionary *,SInt32 *) override;
 IOReturn message(UInt32,IOService *,void *) override;
 IOReturn newUserClient(task_t,void *,UInt32,OSDictionary *,IOUserClient **) override;
'''
    prototypes={275:'OSString *newTransportString() const',277:'OSString *newProductString() const',
                284:'IOReturn newReportDescriptor(IOMemoryDescriptor **) const=0',
                285:'IOReturn handleReport(IOMemoryDescriptor *,IOHIDReportType,IOOptionBits)'}
    for slot in range(abi['base_slots'],len(abi['slots'])):
        proto=prototypes.get(slot, f'__attribute__((deprecated("Unqualified ABI: never call"))) void opaque{slot}()')
        text+=f' virtual {proto};\n'
    return text+'};\nstatic_assert(sizeof(IOHIDDevice)==0xd0,"26C5035r device storage");\n'


def build(root, out, compile_cpp, run, llvm, triple, sdk, macho):
    abi_path=Path(__file__).with_name('hid-device-26C5035r.json')
    abi=json.loads(abi_path.read_text())
    if (abi['build'],abi['base_slots'],len(abi['slots']),abi['instance_size']) != ('26C5035r',272,334,0xd0):
        raise ValueError('Unqualified IOHIDDevice layout')
    out=out/'touch';out.mkdir(parents=True,exist_ok=True)
    (out/'OriginalHidDeviceABI.h').write_text(header(abi))
    source=root/'kits/nabu/NabuTouch.cpp';obj=out/'NabuTouch.o'
    compile_cpp(source,obj,extra=['-I',str(out)])
    raw=obj.read_bytes();image=macho(raw)
    st=next(p for c,p,_ in image['commands'] if c==2)
    symoff,count,stroff,_=struct.unpack_from('<4I',raw,st+8)
    sy=[]
    for i in range(count):
        ix,kind,_,_,va=struct.unpack_from('<IBBHQ',raw,symoff+i*16)
        sy.append((raw[stroff+ix:raw.index(b'\0',stroff+ix)].decode(),va,kind))
    vt=next(va for n,va,_ in sy if n=='__ZTV12NabuTouchHid')
    entries={}
    for seg in image['segments']:
        for sec in seg['sections']:
            if sec['va']<=vt<sec['va']+sec['size']:
                off,n=struct.unpack_from('<2I',raw,sec['command_offset']+56)
                for i in range(n):
                    delta,bits=struct.unpack_from('<2I',raw,off+8*i)
                    if bits>>27&1 and bits>>25&3==3 and bits>>28==0:
                        if struct.unpack_from('<Q',raw,sec['fileoff']+delta)[0]:
                            raise ValueError('PAC ABI present in plain arm64 touch object')
                        entries[sec['va']+delta]=sy[bits&0xffffff][0]
    slots=[entries.get(vt+16+i*8) for i in range(len(abi['slots']))]
    if not all(slots) or vt+16+len(slots)*8 in entries:
        raise ValueError('Compiled IOHIDDevice vtable length differs from the original')
    slide=int(abi['collection_slide'],16)
    bindings={n:int(v,16)+slide for n,v in abi['bindings'].items()}
    own=[]
    for i,n in enumerate(slots):
        if n.startswith(('__ZN12NabuTouchHid','__ZNK12NabuTouchHid')):
            own.append(i)
        elif '11IOHIDDevice' in n:
            if i==284:raise ValueError('Missing owned report descriptor')
            bindings[n]=int(abi['slots'][i]['target'],16)+slide
        elif i>=abi['base_slots']:
            raise ValueError(f'Unexpected HID vtable symbol {i}: {n}')
    if not {0,1,7,22,190,191,275,277,284} <= set(own):
        raise ValueError(f'Missing expected IOHIDDevice overrides: {own}')
    undefined={n for n,_,k in sy if k==1 and '11IOHIDDevice' in n}
    if not undefined<=set(bindings):raise ValueError(f'Unbound HID imports: {undefined-set(bindings)}')
    bindings={n:v for n,v in bindings.items() if n in undefined}
    asm='.text\n.p2align 2\n';addresses=''
    for n,va in sorted(bindings.items()):
        if n in {'__ZN11IOHIDDevice10gMetaClassE','__ZTV11IOHIDDevice'}:
            addresses+=f'.globl {n}\n.set {n}, {va:#x}\n'
        else:
            asm+=f'.globl {n}\n{n}:\n bti c\n b {n}.original\n'
            addresses+=f'.globl {n}.original\n.set {n}.original, {va:#x}\n'
    objs=[]
    for name,text in [('hid-bindings',asm),('hid-addresses',addresses)]:
        path=out/(name+'.S');path.write_text(text);dst=out/(name+'.o')
        run(['xcrun','clang','-target',triple,'-isysroot',sdk,'-c',path,'-o',dst]);objs.append(dst)
    late=out/'NabuTouch.late.o'
    run([llvm/'llvm-objcopy','--remove-section=__DATA,__mod_init_func','--globalize-symbol=__GLOBAL__sub_I_NabuTouch.cpp',obj,late])
    report=dict(build=abi['build'],kernelcache_sha256=abi['kernelcache_sha256'],vtable_entries=len(slots),owned_slots=own,
                bindings={n:hex(v) for n,v in bindings.items()},source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                firmware='private ADT /nabu-touch',input_mode='single-primary absolute mouse',device_tested=False)
    (out/'build-report.json').write_text(json.dumps(report,indent=2)+'\n')
    return [late,*objs]
