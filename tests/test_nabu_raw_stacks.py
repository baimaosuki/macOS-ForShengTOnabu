"""Execute the real raw-stack reader against failing/racing Mach boundaries."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


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
static int thread_info(unsigned port,unsigned flavor,int *out,unsigned *count){(void)port;assert(flavor==4);((thread_identifier_info_data_t*)out)->thread_id=900+port;*count=6;return 0;}
static int thread_get_state(unsigned port,unsigned flavor,int *out,unsigned *count){assert(port>=100&&flavor==6);state_calls++;if(mode==3)return 4;arm_thread_state64_t *s=(void*)out;for(unsigned i=0;i<29;i++)s->r[i]=0xfedc000000001000+i;s->r[29]=state_fp;s->r[30]=0xabcd000012345678;s->r[31]=state_sp;s->r[32]=0x187123456;*count=mode==4?2:68;return 0;}
static int mach_port_deallocate(unsigned self,unsigned port){assert(self==7&&(port==42||port>=100));deallocated++;return 0;}
static int mach_vm_deallocate(unsigned self,uint64_t at,uint64_t length){assert(self==7&&at==(uintptr_t)ports&&length==array_count*sizeof(*ports));vm_deallocated++;return 0;}
static int task_info(unsigned task,unsigned flavor,int *out,unsigned *count){assert(task==42&&flavor==17);if(!dyld_mode)return 5;struct task_dyld_info *info=(void*)out;info->all_image_info_addr=0x5000;info->all_image_info_size=sizeof(image_data);info->all_image_info_format=2;*count=6;return 0;}
static int mach_vm_read_overwrite(unsigned task,uint64_t at,uint64_t length,uint64_t destination,uint64_t *actual){assert(task==42);reads++;if(dyld_mode&&at>=0x5000){const void *src=0;if(at==0x5000&&length==sizeof(image_data))src=&image_data;else if(at==0x6000&&length==sizeof(image_entry))src=&image_entry;else if(at>=0x7000&&at+length<=0x7000+sizeof(image_commands))src=image_commands+at-0x7000;assert(src);memcpy((void*)(uintptr_t)destination,src,length);*actual=length;return 0;}assert(length==16);uint64_t *out=(void*)(uintptr_t)destination;*actual=length;if(mode==5){*actual=8;return 0;}if(mode==6)return 1;assert(at>=0x2000&&at<0x3000&&(at&15)==0);frame_calls++;out[0]=(mode==7)?at:mode==8?at+16:at==0x2000?0x2020:0;out[1]=0xfedc000018731234;return 0;}
static ssize_t fixture_write(int fd,const void *data,size_t n){assert(fd==2&&n<=512&&n>14);assert(!memcmp(data,"NABU-RAWSTACK ",14));assert(((const char*)data)[n-1]=='\n');for(size_t i=0;i+1<n;i++)assert(((const char*)data)[i]!='\n');assert(used+n+1<sizeof(output));memcpy(output+used,data,n);used+=n;output[used]=0;return (ssize_t)n;}
#define CLOCK_UPTIME_RAW 8
#define CTL_KERN 1
#define KERN_PROC 14
#define KERN_PROC_ALL 0
struct kinfo_proc {struct {pid_t p_pid;char p_comm[17];} kp_proc;};
static uint64_t clock_seconds=1;static unsigned forks,waits,watch_count=3;
static int fixture_clock(int clock,struct timespec *now){assert(clock==CLOCK_UPTIME_RAW);now->tv_sec=(time_t)clock_seconds;now->tv_nsec=0;return 0;}
static int fixture_usleep(unsigned micros){assert(micros==500000);clock_seconds+=75;return 0;}
static pid_t fixture_fork(void){forks++;return 777;}
static pid_t fixture_wait(pid_t pid,int *status,int flags){assert(pid==777&&!flags);*status=0;waits++;return pid;}
static int sysctl(int *mib,unsigned n,void *out,size_t *bytes,void *in,size_t inbytes){assert(mib[0]==1&&n==4&&!in&&!inbytes);if(!out){*bytes=watch_count*sizeof(struct kinfo_proc);return 0;}assert(*bytes>=watch_count*sizeof(struct kinfo_proc));struct kinfo_proc *p=out;const char *names[]={"opendirectoryd","loginwindow","WindowServer","lsd","coreservicesd","launchservicesd","powerd"};for(unsigned i=0;i<watch_count;i++){p[i].kp_proc.p_pid=(clock_seconds>=151?400:300)+(int)i;strcpy(p[i].kp_proc.p_comm,names[i]);}*bytes=watch_count*sizeof(*p);return 0;}
#define clock_gettime fixture_clock
#define usleep fixture_usleep
#define fork fixture_fork
#define waitpid fixture_wait
#define write fixture_write
#include "raw_stacks.h"
#undef write
static void reset(unsigned next){used=reads=deallocated=vm_deallocated=state_calls=frame_calls=0;output[0]=0;mode=next;dyld_mode=0;array_count=3;state_fp=0x2000;state_sp=0x1f00;}
int main(void){
 char *args[]={"--nabu-raw-stacks","321",0};
 reset(0);assert(!nabu_raw_stacks_command(2,args));assert(state_calls==3&&frame_calls==6&&deallocated==4&&vm_deallocated==1);assert(strstr(output,"pc=0x187123456")&&strstr(output,"return_raw=0xfedc000018731234"));assert(strstr(output,"reason=end"));assert(strstr(output,"gpr tid=1000 basex=16")&&strstr(output,"x19=0xfedc000000001013")&&strstr(output,"x22=0xfedc000000001016")&&strstr(output,"basex=28 x28=0xfedc00000000101c"));assert(strstr(output,"task_read_for_pid"));
 reset(1);assert(nabu_raw_stacks_command(2,args)==1&&!state_calls&&!deallocated&&!reads);assert(!strstr(output,"registers")&&!strstr(output," gpr "));
 reset(2);assert(nabu_raw_stacks_command(2,args)==1&&deallocated==1&&!reads);
 reset(3);assert(nabu_raw_stacks_command(2,args)==1&&deallocated==4&&!reads);assert(!strstr(output,"registers")&&!strstr(output," gpr "));
 reset(4);assert(nabu_raw_stacks_command(2,args)==1&&!reads&&!strstr(output,"registers")&&!strstr(output," gpr "));
 reset(5);assert(!nabu_raw_stacks_command(2,args));assert(!strstr(output," frame ")&&strstr(output,"actual=8")&&strstr(output,"truncated=1"));
 reset(6);assert(!nabu_raw_stacks_command(2,args));assert(!strstr(output," frame ")&&strstr(output,"kr=1"));
 reset(7);assert(!nabu_raw_stacks_command(2,args));assert(frame_calls==3&&strstr(output,"nonascending-fp"));
 reset(8);assert(!nabu_raw_stacks_command(2,args));assert(frame_calls==96&&strstr(output,"reason=frame-limit truncated=1"));
 reset(0);state_fp=0x2001;assert(!nabu_raw_stacks_command(2,args));assert(!reads&&strstr(output,"invalid-fp"));
 reset(0);state_fp=0x1000;assert(!nabu_raw_stacks_command(2,args));assert(!reads);
 reset(0);state_fp=0x1000000000000;assert(!nabu_raw_stacks_command(2,args));assert(!reads);
 reset(0);array_count=130;assert(!nabu_raw_stacks_command(2,args));assert(state_calls==130&&deallocated==131&&strstr(output,"index=129"));
 reset(0);assert(!nabu_raw_read(42,UINT64_MAX-1,output,16)&&!reads);
 reset(0);nabu_raw_pid=2147483647;nabu_raw_round=4294967295u;nabu_raw_timestamp_ns=UINT64_MAX;
 char hex[321];memset(hex,'f',320);hex[320]=0;
 nabu_raw_printf("NABU-RAWSTACK relay offset=4294967295 hex=%s\n",hex);
 assert(strstr(output,"offset=4294967295")&&!strstr(output,"line-too-long"));
 reset(0);dyld_mode=1;array_count=0;memset(&image_data,0,sizeof(image_data));image_data.version=15;image_data.infoArrayCount=1;image_data.infoArray=(void*)0x6000;image_data.sharedCacheSlide=0x123000;image_data.sharedCacheBaseAddress=0x180000000;image_data.sharedCacheUUID[0]=0xab;image_entry.imageLoadAddress=(void*)0x7000;
 struct mach_header_64 *mh=(void*)image_commands;*mh=(struct mach_header_64){.magic=MH_MAGIC_64,.cputype=CPU_TYPE_ARM64,.ncmds=1,.sizeofcmds=24};struct uuid_command *uc=(void*)(image_commands+sizeof(*mh));*uc=(struct uuid_command){.cmd=LC_UUID,.cmdsize=24};uc->uuid[0]=0xcd;
 args[1]="321";assert(!nabu_raw_stacks_command(2,args));assert(strstr(output,"cache slide=0x123000")&&strstr(output,"uuid=ab0000")&&strstr(output,"uuid=cd0000")&&strstr(output,"images-consistent=1"));
 assert(nabu_probe_relay("../escape")==2);
 reset(0);assert(!nabu_raw_watch());assert(forks==9&&waits==9);assert(strstr(output,"target_pid=300 round=1")&&strstr(output,"target_pid=400 round=2")&&strstr(output,"target_pid=400 round=3")&&strstr(output,"clock=mach-uptime")&&strstr(output,"completed_mask=7")&&strstr(output,"optional-not-observed target=lsd")&&strstr(output,"optional-not-observed target=coreservicesd")&&strstr(output,"optional-not-observed target=launchservicesd"));assert(clock_seconds==301);assert(strstr(output,"optional-not-observed target=powerd"));
 reset(0);watch_count=7;clock_seconds=1;forks=waits=0;assert(!nabu_raw_watch());assert(forks==21&&waits==21);assert(strstr(output,"target_pid=303 round=1")&&strstr(output,"target_pid=405 round=3")&&strstr(output,"completed_mask=127")&&!strstr(output,"optional-not-observed"));assert(strstr(output,"target_pid=306 round=2")&&strstr(output,"target_pid=406 round=3"));
 const char *invalid[]={"0","1","-3","321x","2147483648","999999999999999999999",""};
 for(unsigned i=0;i<sizeof(invalid)/sizeof(*invalid);i++){args[1]=(char*)invalid[i];assert(nabu_raw_stacks_command(2,args)==2);}
 return 0;
}
'''
    source = tmp_path / 'raw.c'
    source.write_text(harness)
    binary = tmp_path / 'raw'
    subprocess.run(['cc', '-std=c11', '-D_DEFAULT_SOURCE', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=undefined', '-fno-sanitize-recover=all',
                    '-I', str(ROOT / 'platform/nabu/macos26/bootshim'),
                    str(source), '-o', str(binary)], check=True, capture_output=True)
    subprocess.run([binary], check=True, capture_output=True)
