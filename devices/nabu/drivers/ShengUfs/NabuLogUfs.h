/* Board-local ESP logger on the current UFS slot pool. No VFS dependency.
 * Console capture is RAM-only. A single journal lock serializes the worker,
 * critical-exit checkpoint and panic. No panic allocation or sleeping lock.
 */
#include "NabuLogJournal.h"
#include "NabuLogBuild.h"
#include <stdarg.h>
static struct nb_log_volume *nabu_esp_volume;
static struct nb_log_journal nabu_esp_journal;
static unsigned nabu_esp_started, nabu_esp_panic_seen;
static uint64_t nabu_esp_lun_blocks;
struct nb_esp_io_context { bool panic; uint64_t deadline; };
extern "C" {
volatile unsigned nabu_log_writer_state=0, nabu_log_writer_rounds=0, nabu_log_writer_failures=0;
}

// 0 means retry later/refused. Transport errors retain the driver's quarantine
// semantics. A diagnostic operation takes a free slot once, never queues.
static int nabuEspIo (nb_esp_io_context *ctx, unsigned op, uint64_t lba,
                       uint32_t blocks, uint8_t *bytes)
{
  if (!ctx || !gHost.ready || gHost.poisoned || mach_absolute_time () >= ctx->deadline ||
      (!ctx->panic && __atomic_load_n (&nabu_esp_panic_seen, __ATOMIC_ACQUIRE))) return 0;
  if (op != 2 && (!blocks || blocks > 16 || !bytes || lba >= nabu_esp_lun_blocks || blocks > nabu_esp_lun_blocks-lba)) return 0;
  if (op == 1 && (!nb_volume_write_allowed (nabu_esp_volume,lba,blocks) ||
      !gEspLogOpen || lba < gEspLogFirst || lba-gEspLogFirst >= gEspLogBlocks ||
      blocks > gEspLogBlocks-(lba-gEspLogFirst))) return 0;
  if (op == 2 && !gEspLogOpen) return 0;
  UfsSlot slot;
  if (!hostDiagnosticSlotTake (&slot,ctx->panic)) return 0;
  // A root timeout can quarantine the allocation just before slot acquisition.
  if (!gHost.ready || gHost.poisoned) {
    if (!ctx->panic) hostSlotGive (slot,true);
    return 0;
  }
  uint8_t cdb[10]={}; uint32_t scsi=0;
  cdb[0]=op == 2 ? 0x35 : op == 1 ? 0x2a : 0x28;
  if (op != 2) {
    cdb[1]=op == 1 ? 8 : 0; // FUA: write data before publishing its header
    cdb[2]=uint8_t(lba>>24);cdb[3]=uint8_t(lba>>16);cdb[4]=uint8_t(lba>>8);cdb[5]=uint8_t(lba);
    cdb[7]=uint8_t(blocks>>8);cdb[8]=uint8_t(blocks);
    if (op == 1) memcpy (slot.data,bytes,blocks*4096);
  }
  IOReturn status=hostScsiDir (slot,0,cdb,10,op == 2 ? 0 : op == 1 ? 1 : 2,
                              op == 2 ? 0 : blocks*4096,ctx->panic ? 250000 : 1000000,
                              &scsi,nullptr,0,nullptr,ctx->panic);
  if (status == kIOReturnSuccess && op == 0) memcpy (bytes,slot.data,blocks*4096);
  if (!ctx->panic) hostSlotGive (slot,true);
  return status == kIOReturnSuccess;
}
// The current source transport does not cache LUN capacity in UfsHost.
// Prove the 4K LUN geometry through a normal free slot before reading GPT.
static bool nabuEspCapacity (nb_esp_io_context *ctx) {
  if (!ctx || !gHost.ready || gHost.poisoned || mach_absolute_time () >= ctx->deadline) return false;
  UfsSlot slot;
  if (!hostDiagnosticSlotTake (&slot,false)) return false;
  uint8_t cdb[10]={0x25}; uint32_t scsi=0;
  IOReturn status=hostScsiDir (slot,0,cdb,10,2,8,1000000,&scsi);
  uint32_t last=0,size=0;
  if (status==kIOReturnSuccess) {
    for (unsigned i=0;i<4;++i) {last=(last<<8)|slot.data[i];size=(size<<8)|slot.data[i+4];}
  }
  hostSlotGive (slot,true);
  if (status!=kIOReturnSuccess || size!=4096 || last==0xffffffffU || last<7) return false;
  nabu_esp_lun_blocks=uint64_t(last)+1;
  return true;
}
static int nabuEspRead (void *ctx,uint64_t lba,uint32_t blocks,uint8_t *bytes) {
  return nabuEspIo (static_cast<nb_esp_io_context *>(ctx),0,lba,blocks,bytes);
}
static int nabuEspWrite (void *ctx,uint64_t lba,uint32_t blocks,const uint8_t *bytes) {
  return nabuEspIo (static_cast<nb_esp_io_context *>(ctx),1,lba,blocks,const_cast<uint8_t *>(bytes));
}
static int nabuEspSync (void *ctx) {
  return nabuEspIo (static_cast<nb_esp_io_context *>(ctx),2,0,0,nullptr);
}
static bool nabuEspToken (uint8_t token[16]) {
  unsigned enabled=0;
  if (!PE_parse_boot_argn ("nabu-log",&enabled,sizeof enabled) || enabled != 1) return false;
  // PE_parse_boot_argn treats ordinary values as integers, so read this
  // 32-hex identity from the raw boot string. Never print the token.
  const char *p=PE_boot_args (); unsigned found=0;
  while (p && *p) {
    while (*p==' ' || *p=='\t') ++p;
    const char *start=p;
    while (*p && *p!=' ' && *p!='\t') ++p;
    size_t n=static_cast<size_t> (p-start);
    if (n<15 || !nb_equal (start,"nabu-log-token=",15)) continue;
    if (++found != 1 || n != 47) return false;
    unsigned nonzero=0;
    for (unsigned i=0;i<16;++i) {
      unsigned pair=0;
      for (unsigned j=0;j<2;++j) {
        char c=start[15+2*i+j];
        int v=c>='0' && c<='9' ? c-'0' : c>='a' && c<='f' ? c-'a'+10 : c>='A' && c<='F' ? c-'A'+10 : -1;
        if (v<0) return false;
        pair=pair*16+unsigned(v);
      }
      token[i]=uint8_t(pair);nonzero|=pair;
    }
    if (!nonzero) return false;
  }
  return found == 1;
}
static int nabuEspCommit (bool panic) {
  nb_esp_io_context ctx={panic,0};
  clock_interval_to_deadline (panic ? 3000 : 5000,kMillisecondScale,&ctx.deadline);
  int result=nb_journal_commit (&nabu_esp_journal,&ctx);
  nabu_log_writer_rounds=nabu_esp_journal.commits;
  nabu_log_writer_failures=nabu_esp_journal.failures;
  return result;
}
extern "C" void nabu_log_checkpoint (void) {
  // Still ordinary faultable process-exit context, before sync/panic freezes
  // CPUs. A busy writer/slot leaves the previous committed journal intact.
  int result=nabuEspCommit (false);
  printf ("NABULOG2: critical-exit checkpoint result=%d commits=%u\n",result,nabu_log_writer_rounds);
}
extern "C" void nabu_log_panic_record (const char *format,va_list *args,unsigned long caller) {
  if (__atomic_exchange_n (&nabu_esp_panic_seen,1,__ATOMIC_ACQ_REL)) return;
  char message[2048]; va_list copy;
  va_copy (copy,*args);
  int length=vsnprintf (message,sizeof message,format,copy);
  va_end (copy);
  nabu_capture_append ("\nNABULOG2-PANIC: ",17);
  if (length>0) nabu_capture_append (message,unsigned(length)>=sizeof message ? sizeof message-1 : unsigned(length));
  length=snprintf (message,sizeof message," caller=0x%lx\n",caller);
  if (length>0) nabu_capture_append (message,unsigned(length));
  // This runs before the legacy storage-probe gate. The reserved slot polls
  // with a 250ms command timeout and a 3s whole-commit deadline. No slot steal.
  (void)nabuEspCommit (true);
}
// The freestanding panic screen replaces both panic entry points, so it
// calls this ABI adapter explicitly with Darwin's variadic stack slots.
#if defined(__APPLE__) && defined(__arm64__)
extern "C" void nabu_log_panic_stack (const char *format,const uint64_t *args,unsigned long caller) {
  va_list ap;
  static_assert (sizeof ap == sizeof args,"Darwin arm64 stack va_list required");
  memcpy (&ap,&args,sizeof ap);
  nabu_log_panic_record (format,&ap,caller);
}
#endif
extern "C" uint32_t nabu_log_copy_msgbuf (uint8_t *);
static unsigned nabu_esp_memory_state;
// Follow SHENG BootLog: allocate wired buffers from ordinary driver startup,
// not the fixed prelinked __DATA gap. Panic never allocates or frees memory.
static bool nabuLogMemoryInit (void) {
  unsigned expected=0;
  if (!__atomic_compare_exchange_n (&nabu_esp_memory_state,&expected,1,false,__ATOMIC_ACQ_REL,__ATOMIC_RELAXED))
    return __atomic_load_n (&nabu_esp_memory_state,__ATOMIC_ACQUIRE)==2;
  uint8_t token[16];
  if (!nabuEspToken (token)) {nabu_esp_memory_state=3;return false;}
  auto *volume=static_cast<nb_log_volume *>(IOMalloc (sizeof(nb_log_volume)));
  auto *ring=static_cast<uint8_t *>(IOMallocAligned (NB_LOG_CAPACITY,4096));
  auto *snapshot=static_cast<uint8_t *>(IOMallocAligned (NB_LOG_CAPACITY,4096));
  if (!volume || !ring || !snapshot) {
    if (volume) IOFree (volume,sizeof(nb_log_volume));
    if (ring) IOFreeAligned (ring,NB_LOG_CAPACITY);
    if (snapshot) IOFreeAligned (snapshot,NB_LOG_CAPACITY);
    nabu_esp_memory_state=3;
    IOLog ("NABULOG2: buffer allocation failed; ESP remains unchanged\n");
    return false;
  }
  bzero (volume,sizeof(*volume));
  uint32_t early=nabu_log_copy_msgbuf (snapshot); // existing SHENG bounded msgbuf copy
  nabu_esp_volume=volume;nabu_esp_journal.payload=snapshot;
  if (!nabu_capture_init (ring)) {nabu_esp_memory_state=3;return false;}
  nabu_capture_append (reinterpret_cast<const char *>(snapshot),early);
  __atomic_store_n (&nabu_esp_memory_state,2,__ATOMIC_RELEASE);
  IOLog ("NABULOG2: buffers ready; console capture starts before UFS bring-up\n");
  return true;
}
static void nabuEspThread (void *,wait_result_t) {
  uint8_t token[16],boot[16];
  if (!nabuEspToken (token)) {
    IOLog ("NABULOG2: disabled; require nabu-log=1 and matching allocation token\n");
    goto stopped;
  }
  if (__atomic_load_n (&nabu_esp_memory_state,__ATOMIC_ACQUIRE)!=2) goto stopped;
  nabu_log_writer_state=1;
  {
    nb_esp_io_context ctx={false,0}; bool opened=false;
    for (unsigned attempt=0;attempt<10 && !nabu_esp_panic_seen;++attempt) {
      clock_interval_to_deadline (5000,kMillisecondScale,&ctx.deadline);
      if (!nabu_esp_lun_blocks && !nabuEspCapacity (&ctx)) {
        IOLog ("NABULOG2: LUN capacity unavailable or not 4K; preserving ESP\n");
        if (!gHost.ready || gHost.poisoned) break;
        IOSleep (1000);continue;
      }
      if (nb_volume_open (nabu_esp_volume,nabuEspRead,&ctx,nabu_esp_lun_blocks,token)) {opened=true;break;}
      IOLog ("NABULOG2: volume refused reason=%s ready=%u poisoned=%u\n",
             nb_volume_failure_name(nabu_esp_volume->failure),gHost.ready?1:0,gHost.poisoned?1:0);
      if (!gHost.ready || gHost.poisoned) break;
      IOSleep (1000);
    }
    if (!opened) goto stopped;
    // The whole ESP must be disjoint from the already checked macosroot
    // partition, including its Data store and raw-log reserve.
    if (!gStoreGeometry.valid || (nabu_esp_volume->esp_first <= gStoreGeometry.last &&
        gStoreGeometry.first < nabu_esp_volume->esp_first+nabu_esp_volume->esp_blocks)) {
      IOLog ("NABULOG2: ESP/root overlap or missing root geometry\n");goto stopped;
    }
    if (!hostEspLogOpen (nabu_esp_volume->file_first+1,NB_LOG_FILE_BYTES/4096-1)) {
      IOLog ("NABULOG2: ESP write window refused\n");goto stopped;
    }
    nb_put64 (boot,mach_absolute_time ());nb_put64 (boot+8,mach_absolute_time ()^nb_le64(nabu_log_build_id));
    clock_interval_to_deadline (5000,kMillisecondScale,&ctx.deadline);
    if (!nb_journal_open (&nabu_esp_journal,nabu_esp_volume,nabuEspWrite,nabuEspSync,&ctx,boot,nabu_log_build_id)) {
      IOLog ("NABULOG2: slot verification refused; preserving existing file\n");goto stopped;
    }
    // Volume reads are startup-only. Do not retain a pointer to a stack context.
    nabu_esp_volume->context=nullptr;nabu_esp_journal.context=nullptr;
  }
  nabu_log_writer_state=2;
  IOLog ("NABULOG2: ready file=XNU2LOG.BIN capacity=4194304 period_ms=2000 panic=bounded\n");
  while (!__atomic_load_n (&nabu_esp_panic_seen,__ATOMIC_ACQUIRE)) {
    int result=nabuEspCommit (false);
    if (result==NB_COMMIT_OK && nabu_log_writer_rounds==1)
      IOLog ("NABULOG2: first commit complete seq=%llu bytes=%llu\n",
             nabu_esp_journal.sequence,nabu_esp_journal.committed_end);
    if (!gHost.ready || gHost.poisoned) break;
    IOSleep (2000);
  }
stopped:
  nabu_log_writer_state=3;
  thread_terminate (current_thread ());
}
static void nabuLogStart (void) {
  if (__atomic_exchange_n (&nabu_esp_started,1,__ATOMIC_ACQ_REL)) return;
  thread_t thread=nullptr;
  if (kernel_thread_start (nabuEspThread,nullptr,&thread)==KERN_SUCCESS) thread_deallocate (thread);
  else IOLog ("NABULOG2: writer thread creation failed\n");
}
