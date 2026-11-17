#!/usr/bin/env python3
"""Compare direct saved-state PAC dispatch with the production no-PAC model."""
import argparse,json,subprocess,shutil
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--xnu',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
src=a.output/'test.c';src.write_text(r'''
#include <stdint.h>
#include <stdbool.h>
#include <assert.h>
#include <string.h>
#include <stdio.h>
#include "nabu_nopac.h"
typedef struct {uint64_t x[29],fp,lr,sp,pc;uint32_t cpsr,reserved;uint64_t far,esr;} arm_saved_state64_t;
#include "nabu_gpr_fast.h"
static uint64_t random64(void){static uint64_t x=0xfefc23d45; x^=x<<13;x^=x>>7;x^=x<<17;return x;}
static unsigned cases;
static void check(uint32_t op,bool user) {
 arm_saved_state64_t s={0},before;struct nabu_nopac_frame f;
 for(unsigned i=0;i<31;i++){f.x[i]=random64();}
 f.sp=random64();f.pc=random64();
 memcpy(s.x,f.x,29*8);s.fp=f.x[29];s.lr=f.x[30];s.sp=f.sp;s.pc=f.pc;s.cpsr=(uint32_t)random64();s.far=random64();s.esr=random64();before=s;
 bool expected=nabu_nopac_step(&f,op,user,0,0);bool result=nabu_gpr_fast_step(&s,op,user);
 assert(expected==result);
 if(result){assert(!memcmp(s.x,f.x,29*8)&&s.fp==f.x[29]&&s.lr==f.x[30]&&s.sp==f.sp&&s.pc==f.pc);assert(s.cpsr==(before.cpsr&~(1U<<21)));assert(s.far==before.far&&s.esr==before.esr);}
 else assert(!memcmp(&s,&before,sizeof s));
 ++cases;
}
int main(void) {
 const uint32_t bases[]={0xd71f0800,0xd71f0c00,0xd73f0800,0xd73f0c00,0xd61f081f,0xd61f0c1f,0xd63f081f,0xd63f0c1f};
 for(unsigned user=0;user<2;user++)for(unsigned rep=0;rep<8;rep++) {
  for(unsigned opc=0;opc<32;opc++)for(unsigned rn=0;rn<32;rn++)for(unsigned rd=0;rd<32;rd++)check(0xdac10000U|(opc<<10)|(rn<<5)|rd,user);
  for(unsigned b=0;b<8;b++)for(unsigned rn=0;rn<32;rn++)for(unsigned rm=0;rm<32;rm++)check(bases[b]|(rn<<5)|(b<4?rm:0),user);
  check(0xd65f0bff,user);check(0xd65f0fff,user);check(0xd503201f,user);check(0xdeadbeef,user);
 }
 printf("%u direct-state PAC equivalence cases passed\n",cases);
}
''')
cc=shutil.which('clang') or 'cc';binary=a.output/'test';subprocess.run([cc,'-O2','-Wall','-Wextra','-Werror','-Wno-unused-function','-fsanitize=address,undefined','-I',str(a.xnu/'osfmk/arm64'),str(src),'-o',str(binary)],check=True);r=subprocess.check_output([binary],text=True);print(r);(a.output/'verification.json').write_text(json.dumps(dict(passed=True,result=r.strip(),assembly_vector_spill_optimized=False,device_speed_measured=False),indent=2)+'\n')
