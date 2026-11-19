#!/usr/bin/env python3
"""Compare input resources to the owner's original nabu DTB and generate ADT."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tomllib

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--vendor-dtb',type=Path,required=True)
    p.add_argument('--firmware-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    device=Path(__file__).resolve().parents[1];root=device.parents[1];sys.path.insert(0,str(root/'scripts'))
    from unixnu.devicetree import read_fdt,generate
    vendor=read_fdt(a.vendor_dtb.read_bytes());entries=list(vendor.walk())
    def get(name):return next(n for n in entries if n.name==name)
    checks={
      'ssusb@a600000':{'reg':[0xa600000,0x100000],'iommus':[0x26,0x140,0]},
      'dwc3@a600000':{'reg':[0xa600000,0xcd00],'interrupts':[0,133,0]},
      'hsphy@88e2000':{'reg':[0x88e2000,0x110,0x7801f8,4],'qcom,param-override-seq':[4,0x70,0x2c,0x74]},
      'spi@c84000':{'reg':[0xc84000,0x4000],'interrupts':[0,583,0],'clocks':[0x1b,0x6f,0x1b,0x7d,0x1b,0x7e]},
      'novatek@0':{'novatek,irq-gpio':[0x38,39,0x2001],'spi-max-frequency':[9600000],
                    'novatek,swrst-n8-addr':[0x3f0fe],'novatek,spi-rd-fast-addr':[0x3f310]},
      'qcom,power-on@800':{'reg':[0x800,0x100]},
      'qcom,spmi@c440000':{'reg':[0xc440000,0x1100,0xc600000,0x2000000,0xe600000,0x100000,0xe700000,0xa0000,0xc40a000,0x26000]},
      'mailbox@18220000':{'reg':[0x18220000,0x100,0x18220d00,0x3000],'qcom,drv-id':[2],
                            'qcom,tcs-config':[2,2,0,3,1,3,3,1]}}
    for name,properties in checks.items():
        for key,expected in properties.items():
            actual=get(name).cells(key)
            if actual!=expected:raise ValueError(f'{name}:{key}: {actual} != {expected}')
    dtb=a.output/'nabu.dtb'
    subprocess.run(['dtc','-I','dts','-O','dtb','-i',str(root/'soc/sm8150/dts'),'-o',str(dtb),str(device/'dts/nabu.dts')],check=True)
    config=tomllib.loads((device/'board.toml').read_text())
    os.environ['NABU_TOUCH_FIRMWARE_DIR']=str(a.firmware_dir.resolve())
    adt=generate(dtb,config,unique_chip_id='12345678');(a.output/'nabu-input.adt').write_bytes(adt)
    # Walk the emitted binary ADT, not textual searches through configuration.
    def parse(at=0):
        props,count=struct.unpack_from('<II',adt,at);at+=8;data={};children=[]
        for _ in range(props):
            name,size=struct.unpack_from('<32sI',adt,at);at+=36
            data[name.rstrip(b'\0').decode()]=adt[at:at+size];at+=(size+3)&~3
        for _ in range(count):
            child,at=parse(at);children.append(child)
        return (data,children),at
    parsed,end=parse();assert end==len(adt)
    nodes={}
    def walk(item,path=''):
        props,children=item;path+='/'+props['name'].rstrip(b'\0').decode();nodes[path]=props
        for child in children:walk(child,path)
    walk(parsed)
    chosen=nodes['/device-tree/chosen'];assert 'ephemeral-storage' not in chosen
    board=nodes['/device-tree/nabu-input'];assert struct.unpack('<4I',board['nabu,spi-pins'])==(23,24,25,26)
    arm=nodes['/device-tree/arm-io'];_,base,_=struct.unpack('<3Q',arm['ranges'])
    usb=nodes['/device-tree/arm-io/usb-host0'];offset,size=struct.unpack_from('<QQ',usb['reg'])
    assert base+offset==0xa600000 and size==0xcd00
    assert struct.unpack('<3I',usb['interrupts'])==(0,133,4)
    assert struct.unpack('<3I',usb['iommus'])[1]==0x140
    touch=nodes['/device-tree/nabu-touch'];assert len(touch['firmware'])==196608
    result=dict(result='pass',vendor_sha256=hashlib.sha256(a.vendor_dtb.read_bytes()).hexdigest(),
        checks=checks,adt_bytes=len(adt),adt_sha256=hashlib.sha256(adt).hexdigest(),
        firmware_sha256=hashlib.sha256(touch['firmware']).hexdigest(),ephemeral_storage=False,device_tested=False)
    (a.output/'adt-check.json').write_text(json.dumps(result,indent=2)+'\n');print('Vendor DTB and emitted production ADT: PASS')
if __name__=='__main__':main()
