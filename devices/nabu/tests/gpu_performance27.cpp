/* SPDX-License-Identifier: MIT */
#include "NativeAdrenoA6xxPower.h"
#include "NabuGpuPerformance.h"
#include "NabuGpuPollPolicy.h"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <vector>

struct CommandDb {
    std::vector<uint8_t> data=std::vector<uint8_t>(0x20000);
    unsigned reads=0,writes=0;
    void word(unsigned at,uint32_t v) {assert(at+4<=data.size());memcpy(data.data()+at,&v,4);}
    void record(unsigned at,const char *name,uint32_t address,unsigned bytes,unsigned aux) {
        memcpy(data.data()+at,name,strlen(name));word(at+16,address);word(at+20,bytes|(aux<<16));
    }
    void levels(unsigned at,std::initializer_list<uint16_t> values) {
        for(auto v:values) {memcpy(data.data()+at,&v,2);at+=2;}
    }
    CommandDb() {
        word(0,1);word(4,0x0c0330db);
        word(8,3);word(12,(4<<16)|(0x1000-144));
        word(24,5|((240-144)<<16));word(28,(6<<16)|(0x2000-144));
        const char *arc[]={"gfx.lvl","mx.lvl","cx.lvl","xo.lvl"};
        const uint32_t addr[]={0x30050,0x30010,0x30000,0x30090};
        for(unsigned i=0;i<4;++i)record(144+24*i,arc[i],addr[i],32,32*i);
        levels(0x1000,{0,16,48,64,128,192,224,256,320});
        levels(0x1020,{0,16,64,128,192,224,256,320});
        levels(0x1040,{0,16,64,128,192,256,320});
        levels(0x1060,{0,32,64});
        const char *bcm[]={"SH0","MC0","ACV","SH2","SN15","SN3"};
        const uint32_t addresses[]={0x5003c,0x50000,0x5000c,0x50034,0x5007c,0x5004c};
        const uint32_t units[]={3910000,2050000,1,3910000,530000,530000};
        const uint16_t widths[]={64,16,0,8,4,4};
        const uint8_t vcd[]={1,0,11,1,3,3};
        for(unsigned i=0;i<6;++i) {
            record(240+24*i,bcm[i],addresses[i],8,8*i);
            word(0x2000+8*i,units[i]);word(0x2004+8*i,widths[i]|(uint32_t(vcd[i])<<16));
        }
    }
    static int read(void *cookie,uint32_t domain,uint32_t at,uint32_t *v) {
        auto &p=*static_cast<CommandDb *>(cookie);++p.reads;
        // Stop after table construction, BEFORE QMP/power hardware.
        if(domain!=NativeAdrenoA6xxCommandDb || at+4>p.data.size() || (at&3))return 1;
        memcpy(v,p.data.data()+at,4);return 0;
    }
    static int write(void *cookie,uint32_t,uint32_t,uint32_t) {++static_cast<CommandDb *>(cookie)->writes;return 1;}
    static void delay(void *,uint32_t) {assert(false);}
    NativeAdrenoA6xxIo io() {return {this,read,write,delay};}
};

static NativeAdrenoA6xxTables build(CommandDb &db,uint32_t khz=585000) {
    NativeAdrenoA6xxPower power;
    assert(power.enableCx(db.io(),12,1,khz)==kIOReturnIOError);
    assert(power.report().phase==NativeAdrenoA6xxPower::AopAcd && !db.writes);
    assert(nabuGpuPerformanceTablesValid(power.tables()));
    return power.tables();
}

int main() {
    CommandDb db;auto t=build(db);
    assert(t.performance[0]==6 && t.performance[1]==2);
    const uint32_t expected[]={0,257000,345000,427000,499200,585000};
    for(unsigned i=0;i<6;++i)assert(t.performance[4+3*i]==expected[i]);
    assert(t.performance[52]==0x00400202 && t.performance[53]==200000);
    assert(t.performance[17]==0x01000607); // 585 MHz: gfx index7, MX index6, level256.
    assert(t.gfxIndex==7 && t.mxIndex==6 && t.cxIndex==2);
    uint32_t vote[3]={};assert(nabuGpuPerformanceVote(t,vote));
    assert(vote[0]==1 && vote[1]==5 && vote[2]==11);
    assert(t.bandwidth[0]==12 && t.bandwidth[23]==0x50000 && t.bandwidth[24]==0x5003c && t.bandwidth[25]==0x5000c);
    assert(t.bandwidth[4]==7 && t.bandwidth[3]==5); // VCD commit waits, including shared SN15/SN3 VCD.
    assert(t.bandwidth[31+3*8]==0x60000db6 && t.bandwidth[32+3*8]==0x60000730);
    assert(t.bandwidth[31+11*8]==0x60003fbe && t.bandwidth[32+11*8]==0x6000216b);
    for(unsigned i=0;i<12;++i) {
        assert(t.bandwidth[33+i*8]==(i?0x60000008u:0x40000000u));
        for(unsigned j=3;j<8;++j)assert(!t.bandwidth[31+i*8+j]);
        if(i>1)for(unsigned j=0;j<2;++j)assert((t.bandwidth[31+i*8+j]&0x3fff)>=(t.bandwidth[23+i*8+j]&0x3fff));
    }
    for(unsigned i=12*8+31;i<159;++i)assert(!t.bandwidth[i]);
    for(unsigned i=1;i<6;++i) {
        CommandDb peer;auto q=build(peer,expected[i]);assert(nabuGpuPerformanceVote(q,vote));
        assert(vote[1]==i && vote[2]==NabuGpuOpps[i].bandwidth);
        assert(q.gfxIndex==(q.performance[2+3*i]&255));
    }
    for(uint32_t bad:{0u,675000u,586000u,0xffffffffu}) {
        CommandDb peer;NativeAdrenoA6xxPower power;
        assert(power.enableCx(peer.io(),12,1,bad)==kIOReturnBadArgument && !peer.reads && !peer.writes);
    }
    // Missing/underrated dependent rail, internal zero, descending levels,
    // missing high GX OPP, duplicate resource and malformed BCM: reject before power writes.
    for(unsigned failure=0;failure<6;++failure) {
        CommandDb peer;
        if(failure==0)memset(peer.data.data()+0x1020+8,0,24); // MX max128 cannot cover high GX.
        if(failure==1)peer.word(0x1028,0x00e00000); // zero inside ARC levels, then nonzero.
        if(failure==2)peer.word(0x1028,0x00c000e0); // descending224,192.
        if(failure==3)memset(peer.data.data()+0x100c,0,20); // GX ends at192.
        if(failure==4)memcpy(peer.data.data()+240+24,"SH0",3);
        if(failure==5)peer.word(0x2000,0); // BCM unit zero.
        NativeAdrenoA6xxPower power;assert(power.enableCx(peer.io(),12,1)!=0);
        assert(power.report().phase==NativeAdrenoA6xxPower::CommandDatabase && !peer.writes);
    }
    for(unsigned bad=0;bad<11;++bad) {
        auto q=t;
        if(bad==0)q.performance[0]=17;
        if(bad==1)q.gpuLevel=0;
        if(bad==2)q.bandwidthLevel=12;
        if(bad==3)q.performance[19]=675000;
        if(bad==4)q.performance[17]=0;
        if(bad==5)q.performance[53]=400000;
        if(bad==6)q.bandwidth[31]|=1u<<29;
        if(bad==7)q.performance[40]=1;
        if(bad==8)q.performance[17]=0x01400607; // level320 exceeds common OPP limit256.
        if(bad==9)q.performance[52]=0x00800404; // excessive GMU voltage.
        if(bad==10)q.performance[17]=0x01001007; // secondary index16 is outside HFI ARC bounds.
        assert(!nabuGpuPerformanceVote(q,vote));
    }
    NabuGpuPollPolicy poll;
    assert(poll.delayMs(0,0,0)==10);
    assert(poll.delayMs(100,10,9)==1);
    assert(poll.delayMs(300000,10,9)==1); // long in-flight job still serviced.
    assert(poll.delayMs(319999,10,10)==1 && poll.delayMs(320000,10,10)==10);
    assert(poll.delayMs(400000,11,10)==1);
    assert(poll.delayMs(400001,11,10,false)==10);
    assert(poll.delayMs(1,0,0)==10); // regressed clock does not extend grace.
    assert(poll.delayMs(500000,0,0xffffffff)==1); // fence wrap.
    puts("PASS actual Power table construction, stock HFI vote, ARC dependencies, BCM VCDs and adaptive polling");
}
