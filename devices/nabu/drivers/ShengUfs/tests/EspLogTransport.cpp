// Compile the production adapter; mock only host commands and scheduling.
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cstdarg>
#include <string>
#define NABU_LOG_IMPLEMENTATION
#include "../NabuLogCore.h"
static uint8_t nabu_log_build_id[32]={1};
using IOReturn=unsigned;using wait_result_t=int;using thread_t=void *;
constexpr unsigned kIOReturnSuccess=0,KERN_SUCCESS=0,kMillisecondScale=1000;
static struct {bool ready=true,poisoned=false;uint64_t blocks=130000;}gHost;
static struct {bool valid=true;uint64_t first=110000,last=129990;}gStoreGeometry;
static bool gEspLogOpen=true;static uint64_t gEspLogFirst=501,gEspLogBlocks=2050;
struct UfsSlot {unsigned index;uint8_t *data;};
static uint8_t dma[65536];static unsigned scsi_calls,takes,gives;static bool slot_available=true;
static uint64_t ticks;
static uint64_t mach_absolute_time(){return ++ticks;}
static void clock_interval_to_deadline(unsigned n,unsigned scale,uint64_t *d){*d=ticks+uint64_t(n)*scale;}
static bool hostDiagnosticSlotTake(UfsSlot *slot,bool panic){takes++;*slot={panic?15U:0U,dma};return slot_available;}
static void hostSlotGive(const UfsSlot &,bool pooled){assert(pooled);gives++;}
static unsigned hostScsiDir(const UfsSlot &slot,unsigned lun,const uint8_t *cdb,unsigned len,unsigned dir,unsigned bytes,unsigned timeout,uint32_t *scsi,void *,unsigned,void *,bool panic){
  scsi_calls++;assert(lun==0 && len==10 && timeout==(panic?250000U:1000000U));
  assert(slot.index==(panic?15U:0U));*scsi=0;
  if(cdb[0]==0x35)assert(!bytes && !dir);
  else if(cdb[0]==0x2a)assert(cdb[1]==8 && dir==1 && bytes);
  else {assert(cdb[0]==0x28 && dir==2 && bytes);memset(dma,0x5a,bytes);}
  return 0;
}
static bool hostEspLogOpen(uint64_t first,uint64_t blocks){gEspLogFirst=first;gEspLogBlocks=blocks;return true;}
static std::string bootargs;
static unsigned enable=1;
static int PE_parse_boot_argn(const char *,unsigned *v,unsigned){*v=enable;return 1;}
static const char *PE_boot_args(){return bootargs.c_str();}
static void IOLog(const char *,...){}
static void IOSleep(unsigned){}
static void thread_terminate(thread_t){}
static thread_t current_thread(){return nullptr;}
static void thread_deallocate(thread_t){}
static int kernel_thread_start(void (*)(void *,int),void *,thread_t *t){*t=nullptr;return 0;}
#include "../NabuLogUfs.h"
int main(){
 uint8_t token[16],data[4096]={};
 bootargs="nabu-log=1 nabu-log-token=0102030405060708090a0b0c0d0e0f10";
 assert(nabuEspToken(token) && token[0]==1 && token[15]==16);
 bootargs+=" nabu-log-token=0102030405060708090a0b0c0d0e0f10";assert(!nabuEspToken(token));
 bootargs="nabu-log-token=0";assert(!nabuEspToken(token));
 bootargs="nabu-log-token=00000000000000000000000000000000";assert(!nabuEspToken(token));
 enable=0;assert(!nabuEspToken(token));enable=1;
 nabu_esp_volume.validated=1;nabu_esp_volume.file_first=500;
 nb_esp_io_context ctx={false,1000000};
 assert(!nabuEspWrite(&ctx,500,1,data) && scsi_calls==0);
 assert(nabuEspWrite(&ctx,501,1,data) && takes==1 && gives==1);
 assert(nabuEspRead(&ctx,1,1,data) && data[0]==0x5a && data[4095]==0x5a);
 assert(nabuEspSync(&ctx));
 unsigned before=scsi_calls;
 gHost.poisoned=true;assert(!nabuEspWrite(&ctx,501,1,data) && scsi_calls==before);gHost.poisoned=false;
 slot_available=false;assert(!nabuEspWrite(&ctx,501,1,data) && scsi_calls==before);slot_available=true;
 ctx.deadline=0;assert(!nabuEspWrite(&ctx,501,1,data) && scsi_calls==before);
 ctx={true,1000000};unsigned give=gives;
 assert(nabuEspWrite(&ctx,501,1,data) && gives==give); // reserved slot: no pool unlock
 nabu_esp_panic_seen=1;ctx.panic=false;assert(!nabuEspSync(&ctx));ctx.panic=true;assert(nabuEspSync(&ctx));
 nabuLogStart();assert(nabu_esp_started==1);nabuLogStart();
 puts("PASS: production ESP adapter gates, token parsing, bounded panic polling and try-slot behavior");
}
