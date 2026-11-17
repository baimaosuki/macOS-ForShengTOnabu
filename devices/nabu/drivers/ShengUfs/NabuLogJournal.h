/* NABULOG2 A/B journal. Platform callbacks own deadlines and transport slots.
 * All storage is supplied before startup; commit never allocates or waits on
 * a lock. Payload -> sync -> header -> sync preserves the previous slot.
 */
#ifndef NABU_LOG_JOURNAL_H
#define NABU_LOG_JOURNAL_H
#include "NabuLogCore.h"
#include "NabuLogVolume.h"
typedef int (*nb_log_write_fn)(void *,uint64_t,uint32_t,const uint8_t *);
typedef int (*nb_log_sync_fn)(void *);
struct nb_log_journal {
  struct nb_log_volume *volume;
  nb_log_write_fn write;
  nb_log_sync_fn sync;
  void *context;
  uint8_t *payload; // allocated once by the board, before the writer starts
  uint8_t headers[2][4096], header[4096], boot[16], build[32];
  uint64_t sequence, committed_end, committed_lost;
  unsigned newest, ready, lock, commits, failures;
};
enum nb_commit_result { NB_COMMIT_FAILED=-1, NB_COMMIT_BUSY=0, NB_COMMIT_OK=1, NB_COMMIT_UNCHANGED=2 };
static inline int nb_journal_slot(struct nb_log_journal *j,unsigned slot) {
  struct nb_log_volume *v=j->volume;
  uint64_t base=v->file_first+1+(uint64_t)slot*(NB_LOG_SLOT_BYTES/4096);
  uint8_t *h=j->headers[slot];
  if(!v->read(v->context,base,1,h)) return -1;
  if(!nb_slot_valid(h,v->token)) return 0;
  unsigned length=nb_le32(h+72),blocks=(length+4095)/4096;
  for(unsigned at=0;at<blocks;) {
    unsigned n=blocks-at>16?16:blocks-at;
    if(!v->read(v->context,base+1+at,n,j->payload+at*4096)) return -1;
    at+=n;
  }
  return nb_crc(j->payload,length)==nb_le32(h+76);
}
static inline int nb_journal_open(struct nb_log_journal *j,struct nb_log_volume *v,
                                  nb_log_write_fn write,nb_log_sync_fn sync,void *ctx,
                                  const uint8_t boot[16],const uint8_t build[32]) {
  j->ready=0;
  if(!v->validated || !j->payload) return 0;
  j->volume=v;j->write=write;j->sync=sync;j->context=ctx;
  j->sequence=0;j->newest=1;j->commits=0;j->failures=0;j->lock=0;
  j->committed_end=j->committed_lost=~(uint64_t)0;
  int valid[2];
  for(unsigned i=0;i<2;i++) {
    valid[i]=nb_journal_slot(j,i);
    if(valid[i]<0) return 0;
    if(valid[i] && nb_le64(j->headers[i]+24)>j->sequence) {
      j->sequence=nb_le64(j->headers[i]+24);j->newest=i;
    }
  }
  if(!valid[0] && !valid[1]) {
    for(unsigned i=0;i<sizeof(j->headers);i++)
      if(((uint8_t *)j->headers)[i]) return 0;
  }
  nb_copy(j->boot,boot,16);nb_copy(j->build,build,32);
  __atomic_store_n(&j->ready,1,__ATOMIC_RELEASE);
  return 1;
}
static inline int nb_journal_commit(struct nb_log_journal *j,void *context) {
  unsigned expected=0;
  if(!__atomic_load_n(&j->ready,__ATOMIC_ACQUIRE) ||
     !__atomic_compare_exchange_n(&j->lock,&expected,1,0,__ATOMIC_ACQUIRE,__ATOMIC_RELAXED))
    return NB_COMMIT_BUSY;
  struct nb_log_snapshot s;
  int result=NB_COMMIT_BUSY;
  if(!nabu_capture_snapshot(&s)) goto out;
  if(s.end==j->committed_end && s.lost==j->committed_lost) {result=NB_COMMIT_UNCHANGED;goto out;}
  if(j->sequence==~(uint64_t)0) {result=NB_COMMIT_FAILED;goto out;}
  for(unsigned at=0;at<s.length;at+=1024) {
    unsigned n=s.length-at>1024?1024:s.length-at;
    if(!nabu_capture_copy(s.first+at,n,j->payload+at)) goto out;
  }
  {
    unsigned padded=(s.length+4095)&~4095u;
    nb_zero(j->payload+s.length,padded-s.length);
    uint8_t *h=j->header;
    nb_zero(h,4096);nb_copy(h,"NABULOG2",8);
    nb_put32(h+8,2);nb_put32(h+12,4096);nb_put32(h+16,1);nb_put32(h+20,NB_LOG_CAPACITY);
    nb_put64(h+24,j->sequence+1);nb_copy(h+32,j->boot,16);
    nb_put64(h+48,s.end);nb_put64(h+56,s.lost);nb_put64(h+64,s.first);
    nb_put32(h+72,s.length);nb_put32(h+76,nb_crc(j->payload,s.length));
    nb_put64(h+88,s.first);nb_copy(h+96,j->volume->token,16);nb_copy(h+112,j->build,32);
    nb_put32(h+80,nb_header_crc(h,80));
    unsigned slot=j->newest^1u;
    uint64_t base=j->volume->file_first+1+(uint64_t)slot*(NB_LOG_SLOT_BYTES/4096);
    result=NB_COMMIT_FAILED;
    for(unsigned at=0;at<padded/4096;) {
      unsigned n=padded/4096-at>16?16:padded/4096-at;
      if(!j->write(context,base+1+at,n,j->payload+at*4096)) goto out;
      at+=n;
    }
    if(!j->sync(context) || !j->write(context,base,1,h) || !j->sync(context)) goto out;
    j->sequence++;j->newest=slot;j->committed_end=s.end;j->committed_lost=s.lost;j->commits++;
    result=NB_COMMIT_OK;
  }
out:
  if(result==NB_COMMIT_FAILED) j->failures++;
  __atomic_store_n(&j->lock,0,__ATOMIC_RELEASE);
  return result;
}
#endif
