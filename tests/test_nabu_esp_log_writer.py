"""Run the production NABULOG2 writer on sparse GPT/FAT media through mock SCSI."""
from pathlib import Path
import hashlib
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from nabu_log_volume import BLOCK,FILE_BYTES,make_config,decode_container
from nabu_storage_source import stage

TOKEN=bytes.fromhex('0123456789abcdef0123456789abcdef')


def disk(path, esp_mib):
    blocks=130000;first=32;esp_blocks=esp_mib*256;fat=128;reserved=32;data_first=reserved+2*fat
    clusters=esp_blocks-data_first
    assert (clusters+2)*4<=fat*BLOCK
    entries=bytearray(128*128)
    entries[:16]=bytes.fromhex('28732ac11ff8d211ba4b00a0c93ec93b')
    entries[16:32]=bytes(range(16));struct.pack_into('<QQ',entries,32,first,first+esp_blocks-1)
    entries[56:64]='esp\0'.encode('utf-16le')
    entries[128:144]=bytes([1])*16;struct.pack_into('<QQ',entries,160,110000,129990)
    header=bytearray(BLOCK);header[:8]=b'EFI PART';struct.pack_into('<II',header,8,0x10000,92)
    struct.pack_into('<QQQQ',header,24,1,blocks-1,6,blocks-2)
    struct.pack_into('<QIII',header,72,2,128,128,zlib.crc32(entries));struct.pack_into('<I',header,16,zlib.crc32(header[:92]))
    boot=bytearray(BLOCK);struct.pack_into('<H',boot,11,BLOCK);boot[13]=1;struct.pack_into('<H',boot,14,reserved);boot[16]=2
    struct.pack_into('<II',boot,32,esp_blocks,fat);struct.pack_into('<I',boot,44,2);boot[82:87]=b'FAT32';boot[510:512]=b'\x55\xaa'
    fats=bytearray(fat*BLOCK);struct.pack_into('<III',fats,0,0xffffff8,0xfffffff,0xfffffff)
    count=FILE_BYTES//BLOCK
    for i in range(count):struct.pack_into('<I',fats,(3+i)*4,3+i+1 if i+1<count else 0xfffffff)
    directory=bytearray(BLOCK);directory[:11]=b'XNU2LOG BIN';directory[11]=0x20
    struct.pack_into('<H',directory,26,3);struct.pack_into('<I',directory,28,FILE_BYTES)
    file_first=first+data_first+1
    with path.open('wb') as f:
        f.truncate(blocks*BLOCK)
        for lba,value in ((1,header),(2,entries),(first,boot),(first+reserved,fats),(first+reserved+fat,fats),(first+data_first,directory),(file_first,make_config(TOKEN))):
            f.seek(lba*BLOCK);f.write(value)
    return file_first,first,data_first


class EspLogWriter(unittest.TestCase):
    def test_real_writer_full_ring_dual_slot_and_fail_closed(self):
        cc=next((shutil.which(n) for n in ('clang++','clang++-19','g++') if shutil.which(n)),None)
        if not cc:self.skipTest('requires C++ compiler')
        with tempfile.TemporaryDirectory() as tmp:
            work=Path(tmp);source,record=stage(ROOT,work/'source',board='nabu')
            staged=source.read_text()
            self.assertIn('#include "NabuLogUfs.h"',staged)
            self.assertIn('nabuLogStart();',staged)
            self.assertLess(staged.index('ufsPrepareRootWindows (operations, layout->windows)'),staged.index('nabuLogStart();'))
            harness=r'''
#include <cassert>
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <string>
#define NABU_LOG_IMPLEMENTATION
#include "NabuLogCore.h"
typedef int IOReturn;typedef int wait_result_t;typedef void* thread_t;
#define kIOReturnSuccess 0
#define KERN_SUCCESS 0
#define kMillisecondScale 1
static const unsigned kDataOffset=0,kDataMax=65536;
static unsigned char dma[kDataMax];
static struct {bool ready=true,poisoned=false;void*lock=(void*)1;uint64_t blocks=130000;unsigned char*virt=dma;}gHost;
static uint64_t nabu_log_root_first=110000,nabu_log_root_blocks=19991;
static FILE*disk_file;static int fail_header,write_calls,sync_calls,scsi_calls,unlock_calls;
static uint64_t ticks;
static bool held,quarantine_on_lock;
static bool IOLockTryLock(void*){if(held)return false;held=true;if(quarantine_on_lock){gHost.poisoned=true;gHost.ready=false;}return true;}
static void IOLockUnlock(void*){assert(held);held=false;unlock_calls++;}
#define IOLog(...) std::printf(__VA_ARGS__)
static unsigned boot_enable=1;
static int PE_parse_boot_argn(const char*n,unsigned*v,unsigned){if(!strcmp(n,"nabu-log")){*v=boot_enable;return 1;}return 0;}
static std::string bootargs="nabu-log=1 nabu-log-token=0123456789abcdef0123456789abcdef";
static const char*PE_boot_args(){return bootargs.c_str();}
static uint64_t mach_absolute_time(){return ++ticks;}
static void clock_interval_to_deadline(unsigned n,int,uint64_t*d){*d=ticks+n;}
static void thread_yield_internal(int);
static void thread_terminate(thread_t){throw 1;}
static thread_t current_thread(){return nullptr;}
static void thread_deallocate(thread_t){}
static int kernel_thread_start(void(*f)(void*,int),void*p,thread_t*t){*t=nullptr;f(p,0);return 0;}
static IOReturn hostScsiDir(unsigned lun,const uint8_t*cdb,unsigned len,unsigned direction,unsigned bytes,unsigned,uint32_t*scsi){
 scsi_calls++;
 assert(lun==0&&len==10&&held);*scsi=0;
 if(cdb[0]==0x35){assert(!direction&&!bytes);sync_calls++;return 0;}
 uint64_t lba=((uint64_t)cdb[2]<<24)|((uint64_t)cdb[3]<<16)|(cdb[4]<<8)|cdb[5];
 unsigned blocks=(cdb[7]<<8)|cdb[8];assert(bytes==blocks*4096&&blocks<=16);
 assert(!fseek(disk_file,lba*4096,SEEK_SET));
 if(cdb[0]==0x2a){assert(direction==1&&cdb[1]==8);write_calls++;
  if(fail_header&&nabu_log_writer_rounds==1&&blocks==1)return -1;
  assert(fwrite(dma,1,bytes,disk_file)==bytes);
 }else{assert(cdb[0]==0x28&&direction==2);assert(fread(dma,1,bytes,disk_file)==bytes);}
 return 0;
}
#include "NabuLogUfs.h"
static void thread_yield_internal(int){
 ticks+=1;
 if(nabu_log_writer_rounds==1){static bool appended;if(!appended){nabu_capture_append("LATE-BOOT\n",10);appended=true;}}
 if(nabu_log_writer_rounds>=2)throw 2;
}
int main(int argc,char**argv){
 assert(argc==3);disk_file=fopen(argv[1],"r+b");assert(disk_file);fail_header=atoi(argv[2]);
 uint8_t token[16]={0};
 assert(!nb_volume_write_allowed(&nabu_log_volume,1,1));
 // A competing root request times out after the pre-lock check. The old DMA
 // allocation is quarantined while TryLock succeeds: no byte may be touched.
 memset(dma,0xa5,sizeof(dma));uint8_t payload[4096];memset(payload,0x5a,sizeof(payload));
 nabu_log_volume.validated=1;nabu_log_volume.file_first=100;quarantine_on_lock=true;
 assert(nabuLogIo(true,101,1,payload)==-1);
 assert(!held&&unlock_calls==1&&!scsi_calls&&!write_calls&&!sync_calls);
 for(unsigned i=0;i<sizeof(dma);i++)assert(dma[i]==0xa5);
 assert(!strcmp(nabu_log_io_reason,"transport-quarantined"));
 quarantine_on_lock=false;gHost.ready=true;gHost.poisoned=false;nabu_log_volume.validated=0;
 boot_enable=0;nabuLogStart();assert(!write_calls&&!nabu_log_writer_state);boot_enable=1;
 bootargs="nabu-log=1 nabu-log-token=0";assert(nabuLogTokenParse(token)==NB_TOKEN_BAD_HEX);
 bootargs="nabu-log=1 nabu-log-token=00000000000000000000000000000000";assert(nabuLogTokenParse(token)==NB_TOKEN_BAD_HEX);
 bootargs="nabu-log=1 nabu-log-token=0123456789abcdef0123456789abcdef";
 std::vector<char> text(NB_LOG_CAPACITY+77);for(unsigned i=0;i<text.size();i++)text[i]='a'+i%26;
 nabu_capture_append(text.data(),text.size());
 try{nabuLogStart();}catch(int){}
 if(fail_header==2){assert(!write_calls&&!sync_calls&&nabu_log_writer_failures==1);fclose(disk_file);return 0;}
 assert(write_calls>0&&sync_calls>=2&&!held);
 assert(nabu_log_writer_rounds==(fail_header?1u:2u));
 assert(!nb_volume_write_allowed(&nabu_log_volume,nabu_log_volume.file_first,1));
 assert(!nb_volume_write_allowed(&nabu_log_volume,1,1));
 assert(!nb_volume_write_allowed(&nabu_log_volume,nabu_log_root_first,1));
 assert(!nb_volume_write_allowed(&nabu_log_volume,nabu_log_volume.file_first+NB_LOG_FILE_BYTES/4096-1,2));
 nabu_log_root_first=nabu_log_volume.esp_first;assert(!nabuLogWindowsClear());
 fclose(disk_file);return 0;
}
'''
            # The transport stub references the public counter before the header definition.
            harness=harness.replace('static IOReturn hostScsiDir','extern "C" volatile unsigned nabu_log_writer_rounds;\nstatic IOReturn hostScsiDir')
            cpp=work/'writer.cpp';cpp.write_text(harness)
            binary=work/'writer'
            build=subprocess.run([cc,'-std=c++17','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-format','-I'+str(source.parent),str(cpp),'-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(build.returncode,0,build.stderr)
            for mib,fail in ((300,0),(384,1),(300,2)):
                path=work/f'disk-{mib}.img';file_first,esp,data_first=disk(path,mib)
                if fail==2:
                    with path.open('r+b')as f:
                        f.seek((esp+32+128)*BLOCK+12);f.write(b'\0\0\0\0')
                with path.open('rb')as f:metadata=f.read((esp+data_first+1)*BLOCK)
                run=subprocess.run([str(binary),str(path),str(fail)],capture_output=True,text=True,timeout=30)
                self.assertEqual(run.returncode,0,run.stdout+run.stderr)
                with path.open('rb')as f:
                    self.assertEqual(f.read(len(metadata)),metadata)
                    f.seek(file_first*BLOCK);container=f.read(FILE_BYTES)
                if fail==2:
                    self.assertEqual(container,make_config(TOKEN)+bytes(FILE_BYTES-BLOCK))
                    self.assertIn('reason=fat-mirror',run.stdout)
                    continue
                payload,info=decode_container(container)
                self.assertEqual(info['sequence'],1 if fail else 2)
                self.assertEqual(len(payload),4<<20)
                self.assertTrue(info['stored_checksum_valid'])
                if not fail:self.assertTrue(payload.endswith(b'LATE-BOOT\n'))
                self.assertIn('first snapshot committed',run.stdout)
                if fail:self.assertIn('old slot preserved',run.stdout)
                self.assertNotIn(TOKEN.hex(),run.stdout)
