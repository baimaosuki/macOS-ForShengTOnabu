/* SPDX-License-Identifier: MIT */
/* Execute production SMMUv2 invalidation against a bounded software peer. */
#include "ArmSmmu4KProgram.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
struct Peer {unsigned writes32,writes64,reads,barriers,fail_write,stall;uint64_t first,last;};
static int r32(void *c,size_t at,uint32_t *v){struct Peer *p=c;assert(at==0x87f4);++p->reads;*v=p->stall;return 0;}
static int r64(void *c,size_t at,uint64_t *v){(void)c;(void)at;*v=0;return 0;}
static int w32(void *c,size_t at,uint32_t v){struct Peer *p=c;assert(at==0x87f0 || at==0x8610);assert(v==UINT32_MAX || v==7);++p->writes32;return 0;}
static int w64(void *c,size_t at,uint64_t v){struct Peer *p=c;assert(at==0x8620);++p->writes64;if(p->writes64==1)p->first=v;p->last=v;return p->fail_write==p->writes64;}
static int barrier(void *c){++((struct Peer *)c)->barriers;return 0;}
int main(void){
 struct Peer p={0};ArmSmmu4KIo io={&p,0x10000,r32,r64,w32,w64,barrier};ArmSmmu4KProgramResult result;
 ArmSmmu4KRange range={0x1000000,0x80000000,4096,ArmSmmu4KRead|ArmSmmu4KWrite,0,2};
 assert(ArmSmmu4KSyncContextTlbPages(&io,0x8000,7,&range,1,4,&result)==ArmSmmu4KSuccess);
 assert(p.writes64==1&&p.writes32==1&&result.tlb_synchronized);
 assert(p.first==((UINT64_C(7)<<48)|0x1000));
 memset(&p,0,sizeof(p));range.bytes=512*4096;
 assert(ArmSmmu4KSyncContextTlbPages(&io,0x8000,7,&range,1,4,&result)==ArmSmmu4KSuccess);
 assert(p.writes64==512&&p.last==((UINT64_C(7)<<48)|0x11ff));
 memset(&p,0,sizeof(p));range.bytes=513*4096;
 assert(ArmSmmu4KSyncContextTlbPages(&io,0x8000,7,&range,1,4,&result)==ArmSmmu4KArgument);
 assert(!p.writes32&&!p.writes64);
 // The large-unmap policy uses the production ASID path, not TLBIALL.
 assert(ArmSmmu4KSyncContextTlb(&io,0x8000,7,0,4,&result)==ArmSmmu4KSuccess);
 assert(p.writes32==2&&!p.writes64&&result.tlb_synchronized);
 memset(&p,0,sizeof(p));range.bytes=8192;p.fail_write=2;
 assert(ArmSmmu4KSyncContextTlbPages(&io,0x8000,7,&range,1,4,&result)==ArmSmmu4KIoError);
 assert(!result.tlb_synchronized&&result.must_retain_tables&&!p.writes32);
 memset(&p,0,sizeof(p));p.stall=1;
 assert(ArmSmmu4KSyncContextTlbPages(&io,0x8000,7,&range,1,3,&result)==ArmSmmu4KTimeout);
 assert(p.reads==3&&!result.tlb_synchronized&&result.must_retain_tables);
 memset(&p,0,sizeof(p));range.iova++;
 assert(ArmSmmu4KSyncContextTlbPages(&io,0x8000,7,&range,1,4,&result)==ArmSmmu4KArgument);
 assert(!p.writes32&&!p.writes64);
 puts("PASS production SMMU: 1/512 page TLBIVAL, 513-page refusal, ASID path, write failure, bounded timeout, misalignment");
}
