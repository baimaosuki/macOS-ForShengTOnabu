#!/usr/bin/env python3
"""Bind the A640 ADT to the owner's original nabu DTB and private firmware."""
import argparse,importlib.util,json,os,struct,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--vendor-dtb',type=Path,required=True)
p.add_argument('--touch-firmware-dir',type=Path,required=True)
p.add_argument('--gpu-firmware-dir',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args();d=Path(__file__).resolve().parents[1];s=d.parents[1]
os.environ['NABU_GPU_FIRMWARE_DIR']=str(a.gpu_firmware_dir.resolve())
subprocess.run([sys.executable,str(d/'scripts/check-input-dtb27.py'),'--vendor-dtb',str(a.vendor_dtb),'--firmware-dir',str(a.touch_firmware_dir),'--output',str(a.output)],check=True)
sys.path.insert(0,str(s/'scripts'));from unixnu.devicetree import read_fdt
vendor=read_fdt(a.vendor_dtb.read_bytes());nodes={n.name:n for n in vendor.walk()}
checks={'qcom,kgsl-3d0@2C00000':{'reg':[0x2c00000,0x40000,0x2c61000,0x800,0x6900000,0x44000,0x780000,0x6fff],'qcom,chipid':[0x6040001],'interrupts':[0,300,0],'qcom,highest-bank-bit':[15],'qcom,min-access-length':[32],'qcom,ubwc-mode':[3]},'qcom,gmu@0x2C6A000':{'reg':[0x2c6a000,0x30000,0xb290000,0x10000,0xb490000,0x10000],'interrupts':[0,304,0,0,305,0]},'qcom,kgsl-iommu@0x02CA0000':{'reg':[0x2ca0000,0x10000]},'gfx3d_user':{'iommus':[0x249,0,0x401]},'gmu_user':{'iommus':[0x249,4,0x400]},'gmu_kernel':{'iommus':[0x249,5,0x400]}}
for name,props in checks.items():
 if name not in nodes:raise ValueError('Missing stock node '+name)
 for key,want in props.items():
  got=nodes[name].cells(key)
  if got!=want:raise ValueError((name,key,got,want))
raw=(a.output/'nabu-input.adt').read_bytes()
def parse(at=0):
 n,c=struct.unpack_from('<II',raw,at);at+=8;props={};children=[]
 for _ in range(n):
  key,length=struct.unpack_from('<32sI',raw,at);at+=36
  props[key.rstrip(b'\0').decode()]=raw[at:at+length];at+=(length+3)&~3
 for _ in range(c):
  child,at=parse(at);children.append(child)
 return (props,children),at
root,end=parse();assert end==len(raw)
gpu=next(props for props,children in root[1] if props['name'].rstrip(b'\0')==b'nabu-adreno')
regs=dict(zip(gpu['reg-names'].rstrip(b'\0').decode().split('\0'),struct.iter_unpack('<QQ',gpu['reg'])))
assert regs['gpu']==(0x2c00000,0x40000) and regs['gmu']==(0x2c6a000,0x30000)
assert regs['smmu']==(0x2ca0000,0x10000) and regs['zap']==(0x98f15000,0x2000)
assert regs['smmu'][0]!=0x15000000 # USB/UFS use a different SMMU instance
for key,value in [('chip-id',0x6040001),('gmem-bytes',0x100000),('frequency-hz',257000000),('gpu-sid-mask',0x401),('gmu-sid',5),('gmu-sid-mask',0x400)]:assert struct.unpack('<I',gpu['nabu,'+key])[0]==value
assert struct.unpack('<10I',gpu['nabu,interrupts'])==(332,706,*range(713,721))
for name,length in [('gmu-firmware',37680),('sqe-firmware',32136),('zap-metadata',6860),('zap-shader',1968)]:assert len(gpu[name])==length
result=dict(passed=True,target='26C5035r',vendor_checks=checks,production_regs=regs,gpu_smmu_separate_from_ufs_usb=True,firmware_validated=True,hardware_tested=False)
(a.output/'gpu-adt-check.json').write_text(json.dumps(result,indent=2)+'\n');print('A640 stock DT and production ADT: PASS')
