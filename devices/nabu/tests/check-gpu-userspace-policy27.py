#!/usr/bin/env python3
"""Check nabu's staged ABI policy and the real compiler's return-address code."""
import argparse,json,re,shutil,subprocess,tempfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True)
p.add_argument('--clang',default='clang');p.add_argument('--sdkroot',type=Path)
p.add_argument('--output',type=Path,required=True);a=p.parse_args()
record=json.loads((a.stage/'nabu-runtime-inputs.json').read_text())
assert record['abi']=='zeta-ordinary-v1' and record['clang_cpu']=='generic'
assert record['clang_arch']=='armv8.2-a+crypto' and not record['rust_pac_boundary']
assert record['rust_lto']=='thin' and record['rust_codegen_units']==8
assert '-Wl,-fixup_chains' in (a.stage/'scripts/build-turnip-darwin.py').read_text()
for name in ['scripts/build-turnip-darwin.py','scripts/build-adreno-metal-plugin.py',
             'drivers/AdrenoMetal/tools/build-shader-cache-app.py']:
    assert '-mcpu=generic' in (a.stage/name).read_text(),name
with tempfile.TemporaryDirectory(prefix='nabu-return-address-') as temp:
    source=Path(temp)/'caller.c';assembly=Path(temp)/'caller.s'
    source.write_text('void *caller(void){return __builtin_return_address(0);}\n')
    flags=['-target','arm64-apple-macos27.0','-mcpu=generic',
           '-march=armv8.2-a+crypto','-mbranch-protection=none','-O2','-S']
    if a.sdkroot:flags+=['-isysroot',str(a.sdkroot)]
    subprocess.run([a.clang,*flags,str(source),'-o',str(assembly)],check=True)
    text=assembly.read_text()
    assert not re.search(r'^\s*(?:xpaci|xpacd|pac[a-z]*|aut[a-z]*|retab|retaa|braa[a-z]*|blraa[a-z]*)\b',text,re.M),text
    # XPACLRI is encoded as HINT #7 and is a NOP without FEAT_PAuth.
    assert re.search(r'\b(?:ret|retab|retaa)\b',text)
a.output.parent.mkdir(parents=True,exist_ok=True)
a.output.write_text(json.dumps(dict(passed=True,real_compiler_return_address_probe=True,
    clang_cpu='generic',architecture='armv8.2-a+crypto',unsupported_pauth=False,
    compatible_hint7_allowed=True,hardware_tested=False),indent=2)+'\n')
print('PASS ordinary arm64 source policy and return-address compiler probe')
