#!/usr/bin/env python3
"""Run production snapshot on non-linear physmap; audit native ABI and symbols."""
import argparse,hashlib,json,re,struct,subprocess,tempfile,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--xnu',required=True,type=Path);p.add_argument('--native-kernel',type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
s=Path(__file__).resolve().parents[3];g=s/'devices/nabu/package/gpu/a640'
pmap=a.xnu/'osfmk/arm/pmap/pmap.h';armvm=a.xnu/'osfmk/arm64/arm_vm_init.c';pub=a.xnu/'osfmk/arm/pmap_public.h'
header=(g/'NabuGpuPhysmap.h').read_text();decls=[]
for name,args in [('phystokv','pmap_paddr_t pa'),('phystokv_range','pmap_paddr_t pa, vm_size_t *max_len')]:
 match=re.search(r'extern vm_map_address_t '+name+r'\(([^;]+)\);',pmap.read_text());assert match and match[1]==args
 assert re.search(r'vm_map_address_t\s+'+name+r'\(\s*'+re.escape(args)+r'\s*\)',armvm.read_text())
 decls.append(match[0])
assert 'typedef uint64_t pmap_paddr_t' in pub.read_text()
assert 'extern "C" vm_map_address_t phystokv(uint64_t pa);' in header
assert 'extern "C" vm_map_address_t phystokv_range(uint64_t pa,vm_size_t *max_len);' in header
assert all('gVirtBase' not in f.read_text() for f in g.glob('*.cpp'))
# Distinguish global defined LC_SYMTAB entries from debug/local name matches.
symbols={}
if a.native_kernel:
 sys.path.insert(0,str(s/'kits/XnuPrelink'));from kc_image import Image
 raw=a.native_kernel.read_bytes();im=Image(raw);off,_=im.command(2);symoff,n,strs,_=struct.unpack_from('<4I',raw,off+8)
 for i in range(n):
  st,ty,sec,de,va=struct.unpack_from('<IBBHQ',raw,symoff+i*16)
  if ty&0xe0 or not(ty&1) or ty&0xe!=0xe:continue
  name=raw[strs+st:raw.index(0,strs+st)].decode()
  if name in ['_phystokv','_phystokv_range','_flush_dcache','_clean_dcache','_pmap_sync_page_attributes_phys']:
   symbols[name]=dict(address=hex(va),n_type=hex(ty),section=sec,external_defined=True)
 assert all(n in symbols for n in ['_phystokv','_phystokv_range','_pmap_sync_page_attributes_phys'])
t=(g/'NativeAdrenoCpu.cpp').read_text();start=t.index('void NativeAdrenoMemory::cacheSnapshot(');end=t.index('{',start)+1;depth=1
while depth:depth+=(t[end]=='{')-(t[end]=='}');end+=1
method=t[start:end]
for reg,var in [('mair_el1','mair'),('mpidr_el1','mpidr'),('ttbr1_el1','ttbr'),('tcr_el1','tcr')]:
 method=method.replace('__asm__ volatile("mrs %0, '+reg+'" : "=r"('+var+'));',var+'=host_'+var+';')
abi='''#include <cstdint>\nusing vm_map_address_t=uint64_t;using vm_size_t=uintptr_t;using pmap_paddr_t=uint64_t;\nextern "C" {\n'''+ '\n'.join(decls)+'''\n}\n'''
peer=r'''
#include <cassert>
#include <array>
#include <map>
#include <memory>
#include <cstdio>
#include <cstdarg>
#include <string>
#include "NabuGpuPteSnapshot.h"
static uint64_t gPhysBase=0xc3804000,gPhysSize=0x100000000;
static uint64_t host_mair=0x0c0804ff00bb44ff,host_mpidr=0x100,host_ttbr=0xce55d000,host_tcr=(UINT64_C(2)<<30)|(UINT64_C(25)<<16);
static uint64_t boPA=0xdaddc000,directVA=0xfffffff980009000,kva=0xfffffff96c640000,nextTable=0xcf002000;
static std::map<uint64_t,std::unique_ptr<std::array<uint64_t,512>>> tables;
static std::vector<uint64_t> reads;
extern "C" vm_map_address_t phystokv(uint64_t pa){
 reads.push_back(pa);if((pa&~UINT64_C(4095))==boPA)return directVA+(pa&4095);
 if((pa&~UINT64_C(4095))==boPA+4096)return directVA+0xa000+(pa&4095);
 auto i=tables.find(pa&~UINT64_C(4095));assert(i!=tables.end());
 return reinterpret_cast<uintptr_t>(i->second->data())+(pa&4095);
}
extern "C" vm_map_address_t phystokv_range(uint64_t pa,vm_size_t *n){if(*n>4096-(pa&4095))*n=4096-(pa&4095);return phystokv(pa);}
static void _disable_preemption(){}static void _enable_preemption(){}
using ppnum_t=uint32_t;static unsigned pmap_cache_attributes(ppnum_t pn){assert((uint64_t(pn)<<12)==boPA || (uint64_t(pn)<<12)==boPA+4096);return 6;}
static std::string logText;static void IOLog(const char *f,...){char b[2048];va_list args;va_start(args,f);vsnprintf(b,sizeof b,f,args);va_end(args);logText+=b;}
struct Span{uint64_t offset,physical,bytes;};
struct NativeAdrenoMemory{
 struct{bool prepared=true;size_t bytes=8192;void *mapping=reinterpret_cast<void*>(kva);Span spans[2]={{0,boPA,4096},{4096,boPA+4096,4096}};size_t span_count=2;}storage_;
 void cacheSnapshot(const char *,size_t,uint64_t)const;
};
static void addPage(uint64_t va,uint64_t pa=boPA){
 uint64_t table=host_ttbr;
 for(unsigned level=1;level<=3;++level){
  auto &ptr=tables[table];if(!ptr)ptr=std::make_unique<std::array<uint64_t,512>>();
  auto &pte=(*ptr)[(va>>(39-9*level))&511];
  if(level==3)pte=pa|3|(1<<2)|(3<<8);
  else{if(!pte){pte=nextTable|3;nextTable+=0x7000;}table=pte&~UINT64_C(4095);}
 }
}
'''
test=r'''
int main(){
 addPage(kva);addPage(directVA);addPage(kva+4096,boPA+4096);addPage(directVA+0xa000,boPA+4096);
 NativeAdrenoMemory m;m.cacheSnapshot("p031-nonlinear",0,0);
 assert(logText.find("valid=1 level=3 attr=1 sh=3")!=std::string::npos);
 assert(logText.find("[bo dpte] phase=p031-nonlinear va=fffffff980009000 pte=00000000daddc307 valid=1 level=3 attr=1 sh=3")!=std::string::npos);
 assert(logText.find("wc=44")!=std::string::npos);
 size_t begin=0;while(begin<logText.size()){size_t end=logText.find('\n',begin);assert(end!=std::string::npos && end-begin<256);begin=end+1;}
 assert(!reads.empty() && reads.front()==boPA);
 bool rootOffset=false;for(auto pa:reads)if((pa&~UINT64_C(4095))==host_ttbr){rootOffset=rootOffset || (pa&4095)==0xf28;}
 assert(rootOffset);logText.clear();reads.clear();
 m.cacheSnapshot("second-page-nonlinear",4096,0);
 assert(reads.front()==boPA+4096 && logText.find("[bo dpte] phase=second-page-nonlinear")!=std::string::npos);
 logText.clear();reads.clear();
 // Missing kernel leaf is logged and prevents a second direct-PTE walk.
 auto root=tables[host_ttbr].get();(*root)[(kva>>30)&511]=0;
 m.cacheSnapshot("missing-kernel-leaf",0,0);assert(logText.find("valid=0")!=std::string::npos);
 assert(logText.find("[bo dpte] phase=missing-kernel-leaf")!=std::string::npos);
 puts("PASS actual cacheSnapshot on split physmap, P031 TTBR PA, MAIR/PTE output and invalid kernel mapping");
}
'''
a.output.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='nabu-physmap-') as td:
 r=Path(td);cpp=r/'snapshot.cpp';cpp.write_text('#include <vector>\n'+abi+peer+method+test)
 subprocess.run(['c++','-std=c++17','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',str(g),str(cpp),'-o',str(r/'snapshot')],check=True)
 q=subprocess.run([str(r/'snapshot')],capture_output=True,text=True);(a.output/'run.log').write_text(q.stdout+q.stderr);assert not q.returncode,q.stdout+q.stderr;print(q.stdout,end='')
report=dict(passed=True,production_snapshot_executed=True,nonlinear_physmap=True,invalid_kernel_pte_stops_direct_walk=True,native_signatures_verified=True,selected_sources={str(f.relative_to(a.xnu)):hashlib.sha256(f.read_bytes()).hexdigest() for f in [pmap,armvm,pub]},native_kernel=str(a.native_kernel) if a.native_kernel else None,native_symbols=symbols,sanitizers=['ASan','UBSan'],hardware_tested=False)
(a.output/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
