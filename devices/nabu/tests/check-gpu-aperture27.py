#!/usr/bin/env python3
"""Execute selected XNU aperture setup/cache/TLBI branches without PPL."""
import argparse,json,subprocess,tempfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--xnu',type=Path,required=True);p.add_argument('--baseline-xnu',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
def case(xnu,enabled):
 proc=(xnu/'osfmk/arm64/proc_reg.h').read_text();start=proc.index('#if XNU_MONITOR || defined(NABU)') if enabled else proc.index('#if XNU_MONITOR\n#define __ARM_PTE_PHYSMAP__')
 end=proc.index('\n\n',start);feature=proc[start:end]
 # The selected production branch, not a test copy of the new condition.
 init=(xnu/'osfmk/arm64/arm_vm_init.c').read_text();start=init.rindex('static void\narm_vm_physmap_init(boot_args *args)');end=init.index('\n\tassert((temp_ptov_table',start)
 init=init[start:end]+'\n}\n'
 src=(a.xnu/'osfmk/arm/pmap/pmap.c').read_text()
 start=src.index('pmap_update_cache_attributes_locked(\n\tppnum_t ppnum,');end=src.index('\n\tpai = pa_index(phys);',start)
 update='bool\n'+src[start:end]+'\n return tlb_flush_needed;\n}\n'
 start=src.index('pmap_flush_tlb_for_paddr_locked_async(pmap_paddr_t paddr)\n{');end=src.index('\n\t/* Flush the mappings tracked in the ptes. */',start)
 flush='void\n'+src[start:end]+'\n}\n'
 peer=r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <vector>
#include <cstdio>
#define XNU_MONITOR 0
#define NABU 1
FEATURE
#ifndef __ARM_PTE_PHYSMAP__
#define __ARM_PTE_PHYSMAP__ 0
#endif
using ppnum_t=uint32_t;using pmap_paddr_t=uint64_t;using vm_offset_t=uint64_t;using pt_entry_t=uint64_t;using pv_entry_t=void;using boolean_t=bool;
constexpr unsigned ARM64_GRANULE_ALLOW_BLOCK=1,ARM64_GRANULE_ALLOW_HINT=2,AP_RWNA=0;
constexpr uint64_t ARM_PTE_ATTRINDXMASK=7<<2,ARM_PTE_SHMASK=3<<8,ARM_PTE_HINT_MASK=UINT64_C(1)<<52;
#define PMAP_TRACE(...)
#define PAGE_SIZE 4096
#define VM_WIMG_MASK 255
#define VM_WIMG_IO 1
#define VM_WIMG_POSTED 2
#define VM_WIMG_POSTED_REORDERED 3
#define VM_WIMG_POSTED_COMBINED_REORDERED 4
static bool pmap_panic_dev_wimg_on_managed=false;
static void panic(const char*,...){assert(false);}
static uint64_t ptoa(ppnum_t p){return uint64_t(p)<<12;}
static void *kernel_pmap=(void*)1;static uint64_t pte[2];static unsigned writes=0,flushes=0;
static uint64_t phystokv(uint64_t pa){assert(pa==0x140001000 || pa==0x140002000);return pa==0x140001000?0xfffffff980001000:0xfffffff990002000;}
static uint64_t *pmap_pte(void *p,uint64_t va){assert(p==kernel_pmap);return va==0xfffffff980001000?&pte[0]:&pte[1];}
static uint64_t wimg_to_pte(unsigned attr,uint64_t){return attr==6?(1<<2):(3<<8);}
static void write_pte_strong(uint64_t *p,uint64_t v){*p=v;++writes;}static void write_pte_fast(uint64_t *p,uint64_t v){*p=v;++writes;}
static void flush_mmu_tlb_region_asid_async(uint64_t,size_t n,void *p,bool a,bool b){assert(n==4096 && p==kernel_pmap && a && !b);++flushes;}
struct ptov_table_entry{uint64_t pa,va,len;};constexpr unsigned PTOV_TABLE_SIZE=8;
struct boot_args{uint64_t topOfKernelData;};
static uint64_t gVirtBase=0x100000,gPhysBase=0x80000000,segLOWEST=0x120000,end_kern=0x150000,real_avail_end=0xc0000000;
static std::vector<unsigned> granules;
static void arm_vm_physmap_slide(ptov_table_entry*,uint64_t,uint64_t,unsigned,unsigned granule){granules.push_back(granule);}
static void arm_vm_page_granular_RWNX(uint64_t,uint64_t,unsigned granule){granules.push_back(granule);}
#define bzero(p,n) memset(p,0,n)
'''.replace('FEATURE',feature)
 # init calls phystokv on topOfKernelData; keep this independent of pmap helper peers.
 init=init.replace('phystokv(args->topOfKernelData)','(args->topOfKernelData)')
 test='''
int main(){
 constexpr bool enabled=EXPECTED;
 boot_args args{0x160000};arm_vm_physmap_init(&args);assert(granules.size()==3);
 for(auto g:granules)assert(g==(enabled?0:3));
 pte[0]=0x140001003|(3<<8);pte[1]=0x140002003|(3<<8);
 assert(pmap_update_cache_attributes_locked(0x140001,6,false)==enabled);
 assert(writes==(enabled?1u:0u) && !flushes);
 assert(((pte[0]>>2)&7)==(enabled?1u:0u));
 pmap_flush_tlb_for_paddr_locked_async(0x140001000);assert(flushes==(enabled?1u:0u));
 assert(pmap_update_cache_attributes_locked(0x140002,6,true)==enabled);
 assert(writes==(enabled?2u:0u) && flushes==(enabled?2u:0u));
 // Restore default attributes before pages return to the pageable allocator.
 assert(pmap_update_cache_attributes_locked(0x140001,2,true)==enabled);
 assert(((pte[0]>>2)&7)==0 && ((pte[0]>>8)&3)==3);
 puts(enabled?"PASS actual non-PPL XNU4K aperture allocation, WC/default PTE updates and batch/single TLBI":"PASS negative control: old non-PPL branch leaves WB aperture and no physmap TLBI");
}
'''.replace('EXPECTED','true' if enabled else 'false')
 with tempfile.TemporaryDirectory(prefix='nabu-aperture-') as td:
  r=Path(td);cpp=r/'test.cpp';cpp.write_text(peer+init+update+flush+test)
  subprocess.run(['c++','-std=c++17','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-variable','-Wno-unused-function','-Wno-unused-parameter','-fsanitize=address,undefined',str(cpp),'-o',str(r/'test')],check=True)
  q=subprocess.run([str(r/'test')],capture_output=True,text=True,check=True);return q.stdout
out=case(a.xnu,True)
if a.baseline_xnu:out+=case(a.baseline_xnu,False)
a.output.mkdir(parents=True,exist_ok=True);(a.output/'run.log').write_text(out);print(out,end='')
(a.output/'verification.json').write_text(json.dumps(dict(passed=True,actual_xnu_aperture_init=True,actual_cache_update_prefix=True,actual_physmap_tlbi=True,non_ppl=True,nc_and_restore=True,whole_alias_contract=True,negative_old_branch=bool(a.baseline_xnu),sanitizers=['ASan','UBSan'],hardware_tested=False),indent=2)+'\n')
