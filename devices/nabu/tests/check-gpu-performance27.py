#!/usr/bin/env python3
"""Check the stock nabu FDT and execute the real A640 Power producer under sanitizers."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

p=argparse.ArgumentParser()
p.add_argument('--vendor-dtb',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
d=Path(__file__).resolve().parents[1];g=d/'package/gpu/a640'
def fdt(path,key=None):
    args=['fdtget','-t','x',str(a.vendor_dtb),path,key] if key else ['fdtget','-l',str(a.vendor_dtb),path]
    value=subprocess.check_output(args,text=True).split()
    return [int(x,16) for x in value] if key else value

gpu='/soc/qcom,kgsl-3d0@2C00000'
assert fdt(gpu,'qcom,chipid')==[0x06040001]
bins=[]
for b in (0,1):
    node=gpu+f'/qcom,gpu-pwrlevel-bins/qcom,gpu-pwrlevels-{b}'
    assert fdt(node,'qcom,speed-bin')==[b]
    bins.append({fdt(node+'/'+n,'qcom,gpu-freq')[0]//1000:
                 dict(default=fdt(node+'/'+n,'qcom,bus-freq')[0],
                      minimum=fdt(node+'/'+n,'qcom,bus-min')[0],
                      maximum=fdt(node+'/'+n,'qcom,bus-max')[0]) for n in fdt(node)})
opp={}
for n in fdt('/soc/gpu_opp_table_v2'):
    node='/soc/gpu_opp_table_v2/'+n;freq=fdt(node,'opp-hz')
    opp[((freq[0]<<32)|freq[1])//1000]=fdt(node,'opp-microvolt')[0]-1
header=(g/'NabuGpuPerformance.h').read_text()
levels=[tuple(map(int,x)) for x in re.findall(r'\{(\d+),(\d+),(\d+)\}',header)]
assert {x[0] for x in levels}==set(bins[0])&set(bins[1])
for khz,arc,bw in levels:
    assert arc==(opp[khz] if khz else 0)
    for b in bins:assert b[khz]['minimum']<=bw<=b[khz]['maximum']
vectors=fdt(gpu,'qcom,msm-bus,vectors-KBps')
assert len(vectors)==12*4
assert all(vectors[i:i+3]==[26,512,0] for i in range(0,len(vectors),4))
ddr=vectors[3::4]
declared=list(map(int,re.search(r'NabuGpuDdrKbps\[\] = \{([^}]+)',header).group(1).replace('\n','').split(',')))
assert ddr==declared and ddr[-1]==8363000
assert fdt('/soc/qcom,gmu@0x2C6A000/qcom,gmu-pwrlevels/qcom,gmu-pwrlevel@1','qcom,gmu-freq')==[200000000]
service=(g/'NativeAdrenoService.cpp').read_text();gmu=(g/'NativeAdrenoA6xxGmu.cpp').read_text()
assert 'nabuGpuPerformanceVote(*config_.tables,votePayload)' in gmu
assert 'exchange(30, votePayload, 3)' in gmu
assert 'nabuGpuPerformanceTablesValid(*config.tables)' in gmu
assert 'pollDelayMs_=pollPolicy_.delayMs(current,submitted,completed)' in service
assert 'IOSleep(state->pollDelayMs())' in service
assert 'watchdog_.sample(current,submitted,completed,500000)' in service

with tempfile.TemporaryDirectory(prefix='nabu-performance-') as temp:
    root=Path(temp);kit=root/'IOKit';kit.mkdir()
    (kit/'IOTypes.h').write_text('''#pragma once
#include <stdint.h>
using IOReturn=int;
enum {kIOReturnSuccess=0,kIOReturnBadArgument=1,kIOReturnBadMedia=2,kIOReturnBusy=3,
 kIOReturnIOError=4,kIOReturnNotFound=5,kIOReturnNotReady=6,kIOReturnTimeout=7,kIOReturnUnsupported=8};
''')
    (kit/'IOLib.h').write_text('''#pragma once
#include <IOKit/IOTypes.h>
inline void IOLog(const char *,...){}
''')
    cmd=[shutil.which('clang++') or 'c++','-std=c++17','-O1','-g','-Wall','-Wextra','-Werror',
         '-fsanitize=address,undefined','-fno-sanitize-recover=all','-I',str(root),'-I',str(g),
         str(g/'NativeAdrenoA6xxPower.cpp'),str(d/'tests/gpu_performance27.cpp'),'-o',str(a.output.resolve()/'test')]
    (a.output/'command.json').write_text(json.dumps(cmd,indent=2)+'\n')
    subprocess.run(cmd,check=True)
    run=subprocess.run([str(a.output.resolve()/'test')],check=True,text=True,capture_output=True)
    (a.output/'run.log').write_text(run.stdout);print(run.stdout,end='')

report=dict(passed=True,actual_power_producer_executed=True,vendor_dtb_sha256=hashlib.sha256(a.vendor_dtb.read_bytes()).hexdigest(),
    speed_bins=bins,common_opps=levels,ddr_usecases_kbps=ddr,default_khz=585000,
    default_ddr_kbps=8363000,hfi_v2_counts=[6,2,12],arc_secondary_undervote_rejected=True,
    bcm_vcd_grouping_checked=True,stock_legacy_bcm_values_checked=True,active_poll_ms=1,idle_poll_ms=10,
    watchdog_us_unchanged=500000,sanitizers=['ASan','UBSan'],hardware_tested=False,
    source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
        [g/'NativeAdrenoA6xxPower.cpp',g/'NativeAdrenoA6xxPower.h',g/'NativeAdrenoA6xxGmu.cpp',
         g/'NabuGpuPerformance.h',g/'NabuGpuPollPolicy.h',g/'NativeAdrenoService.cpp']})
(a.output/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
print('PASS both stock speed bins, ARC OPP levels, all DDR usecases and unchanged watchdog budget')
