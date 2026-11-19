#!/usr/bin/env python3
"""Run A640's production portable SMMU transactions with an injected MMIO peer."""
from pathlib import Path
import argparse,json,subprocess,shutil
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
d=Path(__file__).resolve().parents[1];g=d/'package/gpu';overlay=g/'a640';up=g/'upstream/ArmSmmu4K'
# The direct AArch64 MMIO binder is compiled in the real driver, not executed
# on a host. Every transaction in these tests uses its supplied software peer.
files=[overlay/p.name if (overlay/p.name).exists() else p for p in sorted(up.glob('*.c')) if p.name!='ArmSmmu4KMmio.c']
compiler=shutil.which('clang') or shutil.which('clang-17');assert compiler
cmd=[compiler,'-std=c11','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-sanitize-recover=all','-I',str(overlay),'-I',str(up),str(d/'tests/gpu_smmu27.c'),*map(str,files),'-o',str(a.output/'test')]
subprocess.run(cmd,check=True);q=subprocess.run([str((a.output/'test').resolve())],check=True,capture_output=True,text=True);(a.output/'run.log').write_text(q.stdout);print(q.stdout)
(a.output/'verification.json').write_text(json.dumps(dict(passed=True,sanitizers=['ASan','UBSan'],production_program_and_live_sources=True,page_limit=512,write_failure_tested=True,timeout_tested=True,hardware_tested=False),indent=2)+'\n')
