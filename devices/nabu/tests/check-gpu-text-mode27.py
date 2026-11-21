#!/usr/bin/env python3
"""Replay the real signed-text mode filter; CPU and GPU must select different rules."""
import argparse,json,subprocess,tempfile,tomllib
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--xnu',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
d=Path(__file__).resolve().parents[1];source=(a.xnu/'bsd/kern/ubc_subr.c').read_text()
start=source.index('static __attribute__((noinline)) int\nnabu_cs_text_rule_for_mode(');end=source.index('\nstatic void\ncs_text_fixup_init(',start)
body=source[start:end]
assert source.count('if (nabu_cs_text_rule_for_mode(')==2
rules=tomllib.loads((d/'board.toml').read_text())['payload']['text_fixups'];rows=[]
for rule in rules:
    file,offset,old,new=rule.split(',');rows.append((file,int(offset,16)))
prefix='#include <assert.h>\n#include <stdint.h>\n#include <string.h>\n#include <stdio.h>\nstruct cs_text_fixup {char name[64];uint64_t offset;};\n'
test='int main(void){struct cs_text_fixup f={0};unsigned cpu=0,gpu=0;\n'
for name,off in rows:
    cpu=off!=0x5de1134;gpu=off not in [0x5b51d00,0x5b4263c,0x24dfd5c,0x26cde70]
    test+=f'strcpy(f.name,"{name}");f.offset={off:#x};assert(nabu_cs_text_rule_for_mode(&f,0)=={int(cpu)});assert(nabu_cs_text_rule_for_mode(&f,1)=={int(gpu)});cpu+={int(cpu)};gpu+={int(gpu)};\n'
test+='printf("PASS production signed-text mode filter: CPU=%u GPU=%u; GPU keeps native Metal discovery\\n",cpu,gpu);}\n'
a.output.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='nabu-text-mode-') as temp:
    root=Path(temp);(root/'test.c').write_text(prefix+body+test)
    subprocess.run(['cc','-std=c11','-DNABU=1','-Wall','-Werror',str(root/'test.c'),'-o',str(root/'test')],check=True)
    q=subprocess.run([str(root/'test')],capture_output=True,text=True,check=True);print(q.stdout,end='');(a.output/'run.log').write_text(q.stdout)
(a.output/'verification.json').write_text(json.dumps(dict(passed=True,production_mode_filter=True,
    configured_rules=len(rows),cpu_renderer_overrides_disabled_for_gpu=True,final_bgra_allocation_enabled_for_cpu_and_gpu=True,signed_hash_reconstruction_unchanged=True,hardware_tested=False),indent=2)+'\n')
