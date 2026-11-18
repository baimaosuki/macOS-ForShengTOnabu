// Execute the production startup worker; only transport, time and scheduling are mocked.
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdarg>
#include <string>
#define NABU_LOG_IMPLEMENTATION
#include "../NabuLogCore.h"
static uint8_t nabu_log_build_id[32]={1};
using IOReturn=unsigned;using wait_result_t=int;using thread_t=void *;
constexpr unsigned kIOReturnSuccess=0,KERN_SUCCESS=0,kMillisecondScale=1000;
static struct {bool ready=true,poisoned=false;}gHost;
static struct {bool valid=true;uint64_t first=110000,last=129990;}gStoreGeometry;
static bool gEspLogOpen;static uint64_t gEspLogFirst,gEspLogBlocks;
struct UfsSlot {unsigned index;uint8_t *data;};
static uint8_t dma[65536];static FILE *media;static unsigned mode,refusals,sleeps,writes,syncs,window_opens;
static uint64_t ticks;static std::string messages;
static uint64_t mach_absolute_time(){return ++ticks;}
static void clock_interval_to_deadline(unsigned n,unsigned scale,uint64_t *d){*d=ticks+uint64_t(n)*scale;}
static bool hostDiagnosticSlotTake(UfsSlot *slot,bool panic){
    *slot={panic?15U:0U,dma};
    if(mode==0 && refusals<20){++refusals;return false;}
    return true;
}
static void hostSlotGive(const UfsSlot &,bool pooled){assert(pooled);}
static unsigned hostScsiDir(const UfsSlot &,unsigned lun,const uint8_t *cdb,unsigned len,unsigned dir,unsigned bytes,unsigned,uint32_t *scsi,void * = nullptr,unsigned = 0,void * = nullptr,bool = false){
    assert(lun==0 && len==10);*scsi=0;
    if(cdb[0]==0x35){assert(!bytes && !dir);++syncs;return fflush(media)?1:0;}
    uint64_t lba=(uint64_t(cdb[2])<<24)|(uint64_t(cdb[3])<<16)|(uint64_t(cdb[4])<<8)|cdb[5];
    unsigned blocks=(unsigned(cdb[7])<<8)|cdb[8];assert(bytes==blocks*4096 && bytes<=sizeof dma);
    if(mode==1 && cdb[0]==0x28 && lba==322 && refusals<12){++refusals;return 1;}
    assert(!fseek(media,long(lba*4096),SEEK_SET));
    if(cdb[0]==0x28){assert(dir==2);return fread(dma,1,bytes,media)==bytes?0:1;}
    assert(cdb[0]==0x2a && cdb[1]==8 && dir==1 && lba>=322 && lba+blocks<=2372);
    ++writes;return fwrite(dma,1,bytes,media)==bytes?0:1;
}
static bool hostEspLogOpen(uint64_t first,uint64_t blocks){
    ++window_opens;if(gEspLogOpen)return false;
    assert(first==322 && blocks==2050);gEspLogFirst=first;gEspLogBlocks=blocks;gEspLogOpen=true;return true;
}
static int PE_parse_boot_argn(const char *,unsigned *v,unsigned){*v=1;return 1;}
static const char *PE_boot_args(){return "nabu-log=1 nabu-log-token=0102030405060708090a0b0c0d0e0f10";}
static void IOLog(const char *fmt,...){char b[512];va_list a;va_start(a,fmt);vsnprintf(b,sizeof b,fmt,a);va_end(a);messages+=b;}
static void IOSleep(unsigned);
static void thread_terminate(thread_t){}
static thread_t current_thread(){return nullptr;}
static void thread_deallocate(thread_t){}
static int kernel_thread_start(void (*)(void *,int),void *,thread_t *t){*t=nullptr;return 0;}
static void *IOMalloc(size_t n){return malloc(n);}
static void *IOMallocAligned(size_t n,size_t){return malloc(n);}
static void IOFree(void *p,size_t){free(p);}
static void IOFreeAligned(void *p,size_t){free(p);}
extern "C" uint32_t nabu_log_copy_msgbuf(uint8_t *p){memcpy(p,"EARLY\n",6);return 6;}
#include "../NabuLogUfs.h"
static void IOSleep(unsigned ms){
    assert(ms==2000);ticks+=uint64_t(ms)*1000;++sleeps;
    if(nabu_esp_journal.commits || sleeps>=24) nabu_esp_panic_seen=1;
}
int main(int argc,char **argv){
    assert(argc==3);mode=unsigned(strtoul(argv[2],nullptr,10));media=fopen(argv[1],"r+b");assert(media);
    assert(nabuLogMemoryInit());nabu_esp_lun_blocks=130000;
    if(mode==5){gEspLogOpen=true;gEspLogFirst=323;gEspLogBlocks=2050;}
    if(mode==6)gHost.poisoned=true;
    nabuLogStart();assert(nabu_esp_started==1);
    nabuEspThread(nullptr,0);
    if(mode<=1){
        assert(nabu_esp_journal.commits==1 && writes && syncs==2 && window_opens==1);
        assert(messages.find("startup verified attempts=")!=std::string::npos);
        assert(messages.find("retry_limit=none")!=std::string::npos);
        if(mode==0)assert(refusals==20 && sleeps==21);
        if(mode==1)assert(refusals==12 && sleeps==13);
    }else{
        assert(!nabu_esp_journal.ready && !writes && !syncs);
        if(mode<=4)assert(sleeps==24 && messages.find("startup retry reason=")!=std::string::npos);
        if(mode==5)assert(sleeps==0 && messages.find("ESP write window refused")!=std::string::npos);
        if(mode==6)assert(sleeps==0 && messages.find("transport-quarantined")!=std::string::npos);
    }
    assert(nabu_log_writer_state==3);fclose(media);
    printf("PASS: production startup mode=%u refusals=%u sleeps=%u writes=%u windows=%u\n",mode,refusals,sleeps,writes,window_opens);
}
