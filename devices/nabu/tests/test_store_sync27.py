"""Production store detection: regular files, threshold, no-follow and flushes."""
from pathlib import Path
import subprocess,shutil
ROOT=Path(__file__).resolve().parents[3]
def test_real_store_detection(tmp_path):
 s=tmp_path/'test.c';s.write_text(r'''
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <string.h>
static int flushes,pauses,files,metadata;
static char record[2048];
static void fake_sync(void){flushes++;}
static unsigned fake_sleep(unsigned n){assert(n==2);pauses++;return 0;}
static int fake_fsync(int fd){assert(fd>=0);files++;return 0;}
static void nabu_raw_printf(const char *fmt,...){
 if(strstr(fmt,"store-file ")) { va_list ap;va_start(ap,fmt);vsnprintf(record,sizeof record,fmt,ap);va_end(ap);metadata++; }
}
#define sync fake_sync
#define sleep fake_sleep
#define fsync fake_fsync
#include "nabu_store_sync.h"
#undef sync
#undef sleep
#undef fsync
int main(int argc,char **argv){
 assert(argc==2);char path[4096];int fd;
 assert(!nabu_store_sync("/nabu/nonexistent"));
 assert(!nabu_store_sync(argv[1]));
 snprintf(path,sizeof path,"%s/store.csstore",argv[1]);fd=open(path,O_CREAT|O_WRONLY,0600);assert(fd>=0);assert(!ftruncate(fd,4*1024*1024-1));close(fd);
 assert(!nabu_store_sync(argv[1])&&!flushes&&metadata==1);
 assert(strstr(record,"bytes=4194303 ")&&strstr(record,"created_sec=")&&strstr(record,"modified_sec=")&&strstr(record,"observed_sec="));
 assert(!nabu_store_sync(argv[1])&&metadata==1);
 fd=open(path,O_WRONLY);assert(fd>=0&&!ftruncate(fd,4*1024*1024));close(fd);
 assert(nabu_store_sync(argv[1])&&flushes==2&&pauses==1&&files==1&&metadata==2);
 unlink(path);snprintf(path,sizeof path,"%s/wrong.ext",argv[1]);fd=open(path,O_CREAT|O_WRONLY,0600);assert(fd>=0&&!ftruncate(fd,8*1024*1024));close(fd);
 char link[4096];snprintf(link,sizeof link,"%s/link.csstore",argv[1]);assert(!symlink(path,link));
 assert(!nabu_store_sync(argv[1])&&flushes==2);
 unlink(link);unlink(path);snprintf(path,sizeof path,"%s/directory.csstore",argv[1]);assert(!mkdir(path,0700));
 assert(!nabu_store_sync(argv[1])&&flushes==2);rmdir(path);
 assert(metadata==2);
 puts("store change metadata, threshold, no-follow, directory and flush-order cases passed");
}
''');binary=tmp_path/'test';data=tmp_path/'store';data.mkdir();subprocess.run([shutil.which('clang') or 'cc','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',str(ROOT/'scripts/macos-boot-shims'),str(s),'-o',str(binary)],check=True);subprocess.run([str(binary),str(data)],check=True)

if __name__ == "__main__":
 import tempfile
 with tempfile.TemporaryDirectory() as work:test_real_store_detection(Path(work))
