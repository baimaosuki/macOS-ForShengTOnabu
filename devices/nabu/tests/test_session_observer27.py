"""Execute the real raw-stack reader against failing/racing Mach boundaries."""
from pathlib import Path
import subprocess
import shutil

ROOT = Path(__file__).resolve().parents[3]


def test_real_raw_stack_header(tmp_path):
    harness = r'''
#define NABU_RAW_STACKS_HOST_TEST 1
#define NABU_RAW_STACKS_HOST_IO_TEST 1
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <fcntl.h>
#include <time.h>
#include <errno.h>
typedef unsigned task_t, kern_return_t, mach_msg_type_number_t;
typedef unsigned *thread_act_array_t;
typedef int *thread_info_t,*thread_state_t,*task_info_t;
typedef uint64_t mach_vm_size_t,mach_vm_address_t;
#define KERN_SUCCESS 0
#define MACH_PORT_NULL 0
#define THREAD_IDENTIFIER_INFO 4
#define THREAD_IDENTIFIER_INFO_COUNT 6
#define ARM_THREAD_STATE64 6
#define ARM_THREAD_STATE64_COUNT 68
#define TASK_DYLD_INFO 17
#define TASK_DYLD_INFO_COUNT 6
#define TASK_DYLD_ALL_IMAGE_INFO_64 2
#define MH_MAGIC_64 0xfeedfacf
#define CPU_TYPE_ARM64 0x100000c
#define LC_UUID 0x1b
#define LC_SEGMENT_64 0x19
typedef struct {uint64_t thread_id,handle,address;} thread_identifier_info_data_t;
typedef struct {uint64_t r[33];uint32_t flags[2];} arm_thread_state64_t;
#define arm_thread_state64_get_pc(s) ((s).r[32])
#define arm_thread_state64_get_lr(s) ((s).r[30])
#define arm_thread_state64_get_fp(s) ((s).r[29])
#define arm_thread_state64_get_sp(s) ((s).r[31])
struct task_dyld_info {uint64_t all_image_info_addr,all_image_info_size;int all_image_info_format;};
struct dyld_image_info {void *imageLoadAddress,*imageFilePath;uint64_t time;};
struct dyld_all_image_infos {uint32_t version,infoArrayCount;struct dyld_image_info *infoArray;void *dyldImageLoadAddress;uintptr_t sharedCacheSlide;unsigned char sharedCacheUUID[16];uintptr_t sharedCacheBaseAddress;};
struct load_command {uint32_t cmd,cmdsize;};
struct mach_header_64 {uint32_t magic,cputype,cpusubtype,filetype,ncmds,sizeofcmds,flags,reserved;};
struct uuid_command {uint32_t cmd,cmdsize;unsigned char uuid[16];};
struct segment_command_64 {uint32_t cmd,cmdsize;char segname[16];uint64_t vmaddr,vmsize,fileoff,filesize;uint32_t maxprot,initprot,nsects,flags;};
static char output[2000000];static size_t used;
static unsigned ports[140], mode, deallocated, vm_deallocated, reads, state_calls;
static unsigned frame_calls, array_count=3;static int dyld_mode;
static struct dyld_all_image_infos image_data;static struct dyld_image_info image_entry;static unsigned char image_commands[96];
static uint64_t state_fp=0x2000, state_sp=0x1f00;
static unsigned mach_task_self(void){return 7;}
static int task_read_for_pid(unsigned self,pid_t pid,unsigned *out){assert(self==7&&pid==321);*out=mode==1?0:42;return mode==1?5:0;}
static int task_threads(unsigned task,unsigned **out,unsigned *count){assert(task==42);if(mode==2)return 9;for(unsigned i=0;i<array_count;i++)ports[i]=100+i;*out=ports;*count=array_count;return 0;}
#define THREAD_BASIC_INFO 3
#define THREAD_BASIC_INFO_COUNT 10
typedef struct {struct {int seconds,microseconds;} user_time,system_time;int cpu_usage,policy,run_state,flags,suspend_count,sleep_time;} thread_basic_info_data_t;
static int thread_info(unsigned port,unsigned flavor,int *out,unsigned *count){(void)port;if(flavor==3){memset(out,0,sizeof(thread_basic_info_data_t));*count=10;return 0;}assert(flavor==4);((thread_identifier_info_data_t*)out)->thread_id=900+port;*count=6;return 0;}
static int thread_get_state(unsigned port,unsigned flavor,int *out,unsigned *count){assert(port>=100&&flavor==6);state_calls++;if(mode==3)return 4;arm_thread_state64_t *s=(void*)out;for(unsigned i=0;i<29;i++)s->r[i]=0xfedc000000001000+i;s->r[29]=state_fp;s->r[30]=0xabcd000012345678;s->r[31]=state_sp;s->r[32]=0x187123456;*count=mode==4?2:68;return 0;}
static int mach_port_deallocate(unsigned self,unsigned port){assert(self==7&&(port==42||port>=100));deallocated++;return 0;}
static int mach_vm_deallocate(unsigned self,uint64_t at,uint64_t length){assert(self==7&&at==(uintptr_t)ports&&length==array_count*sizeof(*ports));vm_deallocated++;return 0;}
static int task_info(unsigned task,unsigned flavor,int *out,unsigned *count){assert(task==42&&flavor==17);if(!dyld_mode)return 5;struct task_dyld_info *info=(void*)out;info->all_image_info_addr=0x5000;info->all_image_info_size=sizeof(image_data);info->all_image_info_format=2;*count=6;return 0;}
static int mach_vm_read_overwrite(unsigned task,uint64_t at,uint64_t length,uint64_t destination,uint64_t *actual){assert(task==42);reads++;if(dyld_mode&&at>=0x5000){const void *src=0;if(at==0x5000&&length==sizeof(image_data))src=&image_data;else if(at==0x6000&&length==sizeof(image_entry))src=&image_entry;else if(at>=0x7000&&at+length<=0x7000+sizeof(image_commands))src=image_commands+(at-0x7000);assert(src);memcpy((void*)(uintptr_t)destination,src,length);*actual=length;return 0;}assert(length==16);uint64_t *out=(void*)(uintptr_t)destination;*actual=length;if(mode==5){*actual=8;return 0;}if(mode==6)return 1;assert(at>=0x2000&&at<0x3000&&(at&15)==0);frame_calls++;out[0]=(mode==7)?at:mode==8?at+16:at==0x2000?0x2020:0;out[1]=0xfedc000018731234;return 0;}
static unsigned write_mode, write_calls, mirror_calls;
static ssize_t fixture_write(int fd,const void *data,size_t n){
 if(fd==9){mirror_calls++;if(write_mode==4)return (ssize_t)n;errno=EIO;return -1;}
 assert(fd==2&&n<=512);
 if(write_mode==3||write_mode==4){errno=ENOSPC;return -1;}
 if(write_mode==1){if(!write_calls++){errno=EINTR;return -1;}size_t part=n<7?n:7;memcpy(output+used,data,part);used+=part;output[used]=0;return part;}
 assert(n>14);assert(!memcmp(data,"NABU-RAWSTACK ",14));assert(((const char*)data)[n-1]=='\n');for(size_t i=0;i+1<n;i++)assert(((const char*)data)[i]!='\n');assert(used+n+1<sizeof(output));memcpy(output+used,data,n);used+=n;output[used]=0;return (ssize_t)n;}
#define write fixture_write
#include "nabu_raw_stacks.h"
#undef write
static void reset(unsigned next){used=reads=deallocated=vm_deallocated=state_calls=frame_calls=0;output[0]=0;mode=next;dyld_mode=0;array_count=3;state_fp=0x2000;state_sp=0x1f00;}
int main(void){

 reset(0);assert(!nabu_raw_target(321));assert(state_calls==3&&frame_calls==6&&deallocated==4&&vm_deallocated==1);assert(strstr(output,"pc=0x187123456")&&strstr(output,"return_raw=0xfedc000018731234"));assert(strstr(output,"reason=end"));assert(strstr(output,"gpr tid=1000 basex=16")&&strstr(output,"x19=0xfedc000000001013")&&strstr(output,"x22=0xfedc000000001016")&&strstr(output,"basex=28 x28=0xfedc00000000101c"));assert(strstr(output,"task_read_for_pid"));
 reset(1);assert(nabu_raw_target(321)==1&&!state_calls&&!deallocated&&!reads);assert(!strstr(output,"registers")&&!strstr(output," gpr "));
 reset(2);assert(nabu_raw_target(321)==1&&deallocated==1&&!reads);
 reset(3);assert(nabu_raw_target(321)==1&&deallocated==4&&!reads);assert(!strstr(output,"registers")&&!strstr(output," gpr "));
 reset(4);assert(nabu_raw_target(321)==1&&!reads&&!strstr(output,"registers")&&!strstr(output," gpr "));
 reset(5);assert(!nabu_raw_target(321));assert(!strstr(output," frame ")&&strstr(output,"actual=8")&&strstr(output,"truncated=1"));
 reset(6);assert(!nabu_raw_target(321));assert(!strstr(output," frame ")&&strstr(output,"kr=1"));
 reset(7);assert(!nabu_raw_target(321));assert(frame_calls==3&&strstr(output,"nonascending-fp"));
 reset(8);assert(!nabu_raw_target(321));assert(frame_calls==192&&strstr(output,"reason=frame-limit truncated=1"));
 reset(0);state_fp=0x2001;assert(!nabu_raw_target(321));assert(!reads&&strstr(output,"invalid-fp"));
 reset(0);state_fp=0x1000;assert(!nabu_raw_target(321));assert(!reads);
 reset(0);state_fp=0x1000000000000;assert(!nabu_raw_target(321));assert(!reads);
 reset(0);array_count=130;assert(!nabu_raw_target(321));assert(state_calls==130&&deallocated==131&&strstr(output,"index=129"));
 reset(0);assert(!nabu_raw_read(42,UINT64_MAX-1,output,16)&&!reads);
 reset(0);nabu_raw_pid=2147483647;nabu_raw_round=4294967295u;nabu_raw_timestamp_ns=UINT64_MAX;
 char hex[321];memset(hex,'f',320);hex[320]=0;
 nabu_raw_printf("NABU-RAWSTACK relay offset=4294967295 hex=%s\n",hex);
 assert(strstr(output,"offset=4294967295")&&!strstr(output,"line-too-long"));
 reset(0);dyld_mode=1;array_count=0;memset(&image_data,0,sizeof(image_data));image_data.version=15;image_data.infoArrayCount=1;image_data.infoArray=(void*)0x6000;image_data.sharedCacheSlide=0x123000;image_data.sharedCacheBaseAddress=0x180000000;image_data.sharedCacheUUID[0]=0xab;image_entry.imageLoadAddress=(void*)0x7000;
 struct mach_header_64 *mh=(void*)image_commands;*mh=(struct mach_header_64){.magic=MH_MAGIC_64,.cputype=CPU_TYPE_ARM64,.ncmds=1,.sizeofcmds=24};struct uuid_command *uc=(void*)(image_commands+sizeof(*mh));*uc=(struct uuid_command){.cmd=LC_UUID,.cmdsize=24};uc->uuid[0]=0xcd;
assert(!nabu_raw_target(321));assert(strstr(output,"cache slide=0x123000")&&strstr(output,"uuid=ab0000")&&strstr(output,"uuid=cd0000")&&strstr(output,"images-consistent=1"));
 reset(0);write_mode=1;write_calls=0;assert(nabu_raw_printf("short-write=1\n")>0);assert(strstr(output,"short-write=1\n")&&write_calls>1);
 reset(0);write_mode=2;nabu_raw_console_fd=9;nabu_raw_io_errors=0;
 assert(nabu_raw_printf("disk-before-mirror=1\n")>0);assert(strstr(output,"disk-before-mirror=1\n")&&mirror_calls==1&&nabu_raw_io_errors==1);
 reset(0);write_mode=3;mirror_calls=0;nabu_raw_io_errors=0;
 assert(nabu_raw_printf("disk-failure=1\n")==-1);assert(!used&&mirror_calls==1&&nabu_raw_io_errors==2);
 reset(0);write_mode=4;mirror_calls=0;nabu_raw_io_errors=0;
 assert(nabu_raw_printf("disk-failed-console-kept=1\n")>0);assert(!used&&mirror_calls==1&&nabu_raw_io_errors==1);
 reset(0);nabu_raw_output_fd=-1;mirror_calls=0;nabu_raw_io_errors=0;
 assert(nabu_raw_printf("console-only=1\n")>0);assert(!used&&mirror_calls==1&&!nabu_raw_io_errors);
 return 0;
}
'''
    source = tmp_path / 'raw.c'
    source.write_text(harness)
    binary = tmp_path / 'raw'
    subprocess.run([shutil.which('clang') or 'cc', '-std=c11', '-D_DEFAULT_SOURCE', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                    '-I', str(ROOT / 'scripts/macos-boot-shims'),
                    str(source), '-o', str(binary)], check=True)
    subprocess.run([binary], check=True)


def test_observer_schedule_and_gate(tmp_path):
    source = tmp_path / 'schedule.c'
    source.write_text(r'''
#define NABU_SESSION_HOST_TEST 1
#include <assert.h>
#include "nabu_session_observer.h"
int main(void) {
    struct nabu_session_target target={"WindowServer",0,0};
    const uint64_t second=1000000000ULL, start=154*second;
    assert(!nabu_session_due(&target,0));
    assert(nabu_session_due(&target,start));
    target.discovered=start;target.round=1;
    assert(!nabu_session_due(&target,start-1));
    assert(!nabu_session_due(&target,start+120*second-1));
    assert(nabu_session_due(&target,start+120*second));
    target.round=2;
    assert(!nabu_session_due(&target,start+240*second-1));
    assert(nabu_session_due(&target,start+240*second));
    target.round=3;
    assert(!nabu_session_due(&target,424*second));
    assert(!nabu_session_due(&target,474*second-1));
    assert(nabu_session_due(&target,474*second));
    target.round=4;assert(!nabu_session_due(&target,1000*second));
    assert(nabu_session_enabled("a=1 nabu-session-observer=1 z=3"));
    assert(nabu_session_enabled("\tnabu-session-observer=1\t"));
    const char *bad[]={""," ","nabu-session-observer=0","nabu-session-observer=10",
        "xxnabu-session-observer=1","nabu-session-observer=1x"};
    for(unsigned i=0;i<sizeof(bad)/sizeof(*bad);++i) assert(!nabu_session_enabled(bad[i]));
    return 0;
}
''')
    binary = tmp_path / 'schedule'
    subprocess.run([shutil.which('clang') or 'cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                    '-I', str(ROOT / 'scripts/macos-boot-shims'), str(source), '-o', str(binary)], check=True)
    subprocess.run([binary], check=True)


def test_repeated_boot_output_files(tmp_path):
    source = tmp_path / 'output.c'
    source.write_text(r'''
#include <assert.h>
#include <sys/stat.h>
#include <unistd.h>
#include <string.h>
#include "nabu_observer_output.h"
int main(int argc,char **argv) {
    assert(argc==2);
    int dir=open(argv[1],O_RDONLY|O_DIRECTORY);assert(dir>=0);
    const unsigned long long time=1794824455ULL;const pid_t pid=8;
    const char *legacy="session-1794824455-8.log";
    int old=openat(dir,legacy,O_CREAT|O_WRONLY|O_EXCL,0600);assert(old>=0);
    assert(write(old,"previous-boot",13)==13);close(old);
    /* Reproduce the previous production failure with the frozen clock/PID. */
    errno=0;old=openat(dir,legacy,O_CREAT|O_WRONLY|O_EXCL,0600);assert(old<0&&errno==EEXIST);
    char names[3][96];
    for(unsigned i=0;i<3;i++) {
        int fd=nabu_observer_output(dir,time,pid,names[i],sizeof(names[i]));assert(fd>=0);
        assert(write(fd,"current-boot",12)==12);
        struct stat st;assert(!fstat(fd,&st)&&S_ISREG(st.st_mode)&&(st.st_mode&0777)==0600);
        close(fd);for(unsigned j=0;j<i;j++)assert(strcmp(names[i],names[j]));
    }
    old=openat(dir,legacy,O_RDONLY);char bytes[32]={0};assert(read(old,bytes,32)==13);
    assert(!memcmp(bytes,"previous-boot",13));close(old);
    assert(!symlinkat(legacy,dir,"session-1794824455-8-3.log"));
    char name[96];int fd=nabu_observer_output(dir,time,pid,name,sizeof(name));assert(fd>=0);
    assert(!strcmp(name,"session-1794824455-8-4.log"));close(fd);
    assert(nabu_observer_output(dir,time,pid,name,4)<0&&errno==ENAMETOOLONG);
    assert(nabu_observer_output(-1,time,pid,name,sizeof(name))<0&&errno==EBADF);
    close(dir);return 0;
}
''')
    binary = tmp_path / 'output'
    subprocess.run([shutil.which('clang') or 'cc', '-std=c11', '-D_DEFAULT_SOURCE',
                    '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                    '-fno-sanitize-recover=all', '-I', str(ROOT / 'scripts/macos-boot-shims'),
                    str(source), '-o', str(binary)], check=True)
    directory = tmp_path / 'repeated-boots'
    directory.mkdir()
    subprocess.run([binary, str(directory)], check=True)


if __name__ == '__main__':
    import tempfile
    with tempfile.TemporaryDirectory(prefix='nabu-observer-test-') as directory:
        work = Path(directory)
        test_real_raw_stack_header(work)
        test_observer_schedule_and_gate(work)
        test_repeated_boot_output_files(work)
    print('Raw reader, independent sinks, repeated clock/PID log creation and late schedule passed (ASan/UBSan).')
