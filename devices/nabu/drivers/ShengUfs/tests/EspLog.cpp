// Production journal on sparse GPT/FAT media. No fake parser/journal logic.
#include <cassert>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <vector>
#define NABU_LOG_IMPLEMENTATION
#include "../NabuLogJournal.h"
static nb_log_volume volume;
static nb_log_journal journal;
static uint8_t ring_storage[NB_LOG_CAPACITY],snapshot_storage[NB_LOG_CAPACITY];
static FILE *media;
static unsigned writes,syncs,fail_op,operation;
static bool tear_header;
static int readMedia(void *,uint64_t lba,uint32_t blocks,uint8_t *bytes) {
  assert(!fseek(media,long(lba*4096),SEEK_SET));
  return fread(bytes,4096,blocks,media)==blocks;
}
static int writeMedia(void *,uint64_t lba,uint32_t blocks,const uint8_t *bytes) {
  assert(nb_volume_write_allowed(&volume,lba,blocks));
  ++writes;
  if(++operation==fail_op) {
    if(tear_header) {assert(!fseek(media,long(lba*4096),SEEK_SET));assert(fwrite(bytes,1,64,media)==64);}
    return 0;
  }
  assert(!fseek(media,long(lba*4096),SEEK_SET));
  return fwrite(bytes,4096,blocks,media)==blocks;
}
static int syncMedia(void *) {
  ++syncs;
  if(++operation==fail_op) return 0;
  return !fflush(media);
}
int main(int argc,char **argv) {
  assert(nabu_capture_init(ring_storage));journal.payload=snapshot_storage;
  assert(argc==3);media=fopen(argv[1],"r+b");assert(media);
  uint8_t token[16];for(unsigned i=0;i<16;++i)token[i]=uint8_t(i+1);
  int mode=argv[2][0]-'0';
  bool opened=nb_volume_open(&volume,readMedia,nullptr,130000,token);
  if(mode>=5){assert(!opened);assert(writes==0);fclose(media);return 0;}
  assert(opened);
  uint8_t boot[16]={1},build[32]={2};
  assert(nb_journal_open(&journal,&volume,writeMedia,syncMedia,nullptr,boot,build));
  assert(!nb_volume_write_allowed(&volume,volume.file_first,1));
  assert(!nb_volume_write_allowed(&volume,volume.file_first+NB_LOG_FILE_BYTES/4096,1));
  nabu_capture_append("EARLY\n",6);
  assert(nb_journal_commit(&journal,nullptr)==NB_COMMIT_OK);
  assert(journal.sequence==1 && journal.commits==1);
  assert(nb_journal_commit(&journal,nullptr)==NB_COMMIT_UNCHANGED);
  // Same lock used by worker, process-exit checkpoint and panic.
  journal.lock=1;
  assert(nb_journal_commit(&journal,nullptr)==NB_COMMIT_BUSY);journal.lock=0;
  nabu_capture_lock=1;nabu_capture_append("lost",4);nabu_capture_lock=0;
  assert(nabu_capture_lost==4);
  std::vector<uint8_t> input(NB_LOG_CAPACITY+12345);
  for(unsigned i=0;i<input.size();++i)input[i]=uint8_t(i*13+7);
  nabu_capture_append(reinterpret_cast<char *>(input.data()),unsigned(input.size()));
  unsigned before=writes;
  nabu_capture_lock=1;assert(nb_journal_commit(&journal,nullptr)==NB_COMMIT_BUSY);nabu_capture_lock=0;
  assert(writes==before);
  operation=0;
  // 64 payload chunks followed by sync/header/sync. Fail each publication
  // stage, and a middle payload write, without touching the active slot.
  fail_op=mode==1?2:mode==2?65:mode==3?66:mode==4?67:0;
  tear_header=mode==3;
  int result=nb_journal_commit(&journal,nullptr);
  assert(result==(mode?NB_COMMIT_FAILED:NB_COMMIT_OK));
  assert(journal.sequence==(mode?1:2));
  assert(journal.failures==unsigned(mode!=0));
  assert(!fflush(media));fclose(media);
  printf("PASS: production journal mode=%d writes=%u syncs=%u\n",mode,writes,syncs);
}
