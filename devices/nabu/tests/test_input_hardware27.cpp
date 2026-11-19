#include "NabuSpmiV5.h"
#include "NabuUsb2Core.h"
#include "NabuUsbRole.h"
#include "NabuRpmh.h"
#include "NabuUsbDma.h"
#include "NabuPowerReport.h"
#include <cassert>
#include <map>
#include <vector>
#include <tuple>
#include <cstdio>
#include <string>
struct Spmi {
    unsigned owner=0;bool stuck=false,error=false;uint8_t byte=0x55;std::map<unsigned,unsigned> ch[2];
    uint32_t coreRead(unsigned o){if(!o)return 0x50000000;if(o==4)return 2;return o==0x904?0x20800:0;}
    uint32_t ownerRead(unsigned o){assert(o==0x704);return owner;}
    uint32_t readChannel(bool w,unsigned o){if(o%0x80==8)return stuck?0:error?3:1;if(!w && o==0x98)return byte;return ch[w][o];}
    void writeChannel(bool w,unsigned o,unsigned v){ch[w][o]=v;if(w&&o==0x10010)byte=v;}
    void delayUs(unsigned){}
};
struct Usb {
    std::map<std::pair<nabu::UsbBlock,unsigned>,unsigned> regs;
    std::vector<std::tuple<nabu::UsbBlock,unsigned,unsigned>> writes;
    std::vector<std::string> phases;
    bool gdscStuck=false,pllStuck=false,clockStuck=false,resetStuck=false,branchStuck=false;
    unsigned cap=0x40,elapsedUs=0;
    Usb(){regs[{nabu::UsbBlock::Gcc,0xf004}]=0x80000007; // stale PWR_ON, collapsed HW ownership
        regs[{nabu::UsbBlock::Gcc,0}]=1u<<20; // GPLL0 firmware FSM mode, no APPS vote
        regs[{nabu::UsbBlock::Core,0xc120}]=0x55333330;
        regs[{nabu::UsbBlock::Core,0}]=cap;regs[{nabu::UsbBlock::Core,cap+4}]=1;}
    unsigned read(nabu::UsbBlock b,unsigned o){
        if(b==nabu::UsbBlock::Gcc){
            if(o==0xf008)return !gdscStuck && !(regs[{b,0xf004}]&7)?1u<<16:0;
            if(o==0 && !pllStuck && (regs[{b,0x52000}]&1))regs[{b,0}]|=1u<<30;
            if(o==0xf078||o==0xf010||o==0xf07c||o==0xf014||o==0xf018){
                // NoC cannot acknowledge until ALL dependent branches receive
                // their enable vote, regardless of which branch is polled first.
                bool ready=!branchStuck && !(regs[{b,0xf000}]&1);
                for(unsigned r:{0xf010u,0xf078u,0xf07cu,0xf018u,0xf014u})ready&=bool(regs[{b,r}]&1);
                return (regs[{b,o}]&~0x80000000u)|(ready?0:0x80000000u);
            }
        }
        return regs[{b,o}];
    }
    void write(nabu::UsbBlock b,unsigned o,unsigned v){writes.emplace_back(b,o,v);regs[{b,o}]=v;
        if(b==nabu::UsbBlock::Gcc && (o==0xf01c||o==0xf034) && !clockStuck)regs[{b,o}]=v&~1;
        if(b==nabu::UsbBlock::Core && o==cap && (v&2) && !resetStuck)regs[{b,o}]=v&~2;
    }
    void delayUs(unsigned us){elapsedUs+=us;assert(elapsedUs<3000000);}
    void checkpoint(const char *p){phases.emplace_back(p);}
};
struct Pmic {uint8_t cc=0,cmd=0,cfg=2;unsigned writes=0;
    nabu::InputError read(uint8_t sid,uint16_t reg,uint8_t &v){assert(sid==2);v=reg==0x150b?cc:reg==0x1140?cmd:0;return nabu::InputError::Ok;}
    nabu::InputError update(uint8_t sid,uint16_t reg,uint8_t mask,uint8_t v){assert(sid==2);++writes;if(reg==0x1544){}else if(reg==0x1140)cmd=(cmd&~mask)|v;else if(reg==0x1153)cfg=(cfg&~mask)|v;else assert(false);return nabu::InputError::Ok;}
};
struct Tcs {
    std::map<unsigned,unsigned> regs;bool complete=true;
    unsigned read(unsigned o){return regs[o];}
    void write(unsigned o,unsigned v){regs[o]=v;
        if(o==0xd08)regs[0xd04]&=~v;
        if(o==0xd14 && (v&(1u<<24)) && complete){regs[0xd04]|=1;regs[0xd3c]=1u<<16;}
    }
    void delayUs(unsigned){}
};
int main(){
    assert(!nabu::clockBranchReady(0x80000002));
    assert(nabu::clockBranchReady(0x80000002,true)); // captured wrap2 slave AHB
    assert(nabu::clockBranchReady(0xa0000001)); // NoC FSM_ON despite CLK_OFF
    assert(!nabu::clockBranchReady(0x90000001));

    uint8_t report[2];nabuPowerReport(true,report);assert(report[0]==2&&report[1]==1);
    nabuPowerReport(false,report);assert(report[0]==2&&report[1]==0);
    assert(kNabuPowerDescriptor[1]==0x0c&&kNabuPowerDescriptor[9]==0x30);
    Spmi s;nabu::SpmiV5<Spmi> arb(s);uint8_t v=0;
    assert(arb.read(2,0x0810,v)==nabu::InputError::Ok&&v==0x55);
    assert(arb.update(2,0x0810,3,2)==nabu::InputError::Ok&&s.byte==0x56);
    s.owner=1;assert(arb.write(2,0x0810,1)==nabu::InputError::Permission);
    s.owner=0;s.stuck=true;assert(arb.read(2,0x0810,v)==nabu::InputError::Timeout);
    s.stuck=false;s.error=true;assert(arb.read(2,0x0810,v)==nabu::InputError::Transaction);
    assert(arb.read(0,0x0810,v)==nabu::InputError::NoMapping);
    nabu::PowerKeyState key;assert(!key.sample(false,0));assert(!key.sample(true,1));assert(!key.sample(false,10));
    assert(!key.sample(true,20));assert(!key.sample(true,49));assert(key.sample(true,50)==1);
    assert(!key.sample(false,60));assert(key.sample(false,90)==6);assert(!key.sample(false,100));
    assert(!key.sample(true,110));assert(key.sample(true,140)==1);assert(key.sample(true,2140)==8);
    assert(!key.sample(true,4000));assert(!key.sample(false,4001));assert(key.sample(false,4031)==2);
    Usb u;
    for(unsigned off:{0xf078u,0xf010u,0xf07cu,0xf014u,0xf018u})u.regs[{nabu::UsbBlock::Gcc,off}]=0x80000000;
    nabu::Usb2Core<Usb> core(u);assert(core.clocksAndReset());assert(core.femtoPhy());assert(core.selectUtmiHost());assert(core.resetXhci());
    assert((u.read(nabu::UsbBlock::Scratch,8)&0x109)==9);assert((u.read(nabu::UsbBlock::Core,0xc110)&0x3000)==0x1000);
    assert((u.read(nabu::UsbBlock::Gcc,0xf004)&0x00fff007)==0x00282000);
    assert(u.read(nabu::UsbBlock::Gcc,0x52000)&1);
    // PWR_ON is not the SM8150 completion predicate: stale bit31 must fail,
    // and a real CFG_GDSCR completion must work even with bit31 clear.
    Usb gdsc;gdsc.gdscStuck=true;nabu::Usb2Core<Usb> cg(gdsc);assert(!cg.clocksAndReset());
    assert(std::string(cg.lastPhase())=="GDSC-power-up"&&cg.failedOffset()==0xf008);
    assert(cg.failedMask()==0x10000&&gdsc.elapsedUs<=100000);
    Usb cfgOnly;cfgOnly.regs[{nabu::UsbBlock::Gcc,0xf004}]=7;
    nabu::Usb2Core<Usb> ccfg(cfgOnly);assert(ccfg.clocksAndReset());
    Usb pll;pll.pllStuck=true;nabu::Usb2Core<Usb> cp(pll);assert(!cp.clocksAndReset());
    assert(std::string(cp.lastPhase())=="GPLL0-vote"&&cp.failedOffset()==0&&pll.elapsedUs<=20200);
    // Do not alter a manual PLL that firmware and UFS already use.
    Usb manual;manual.regs[{nabu::UsbBlock::Gcc,0}]=0x80000001;
    nabu::Usb2Core<Usb> cm(manual);assert(cm.clocksAndReset());
    for(auto w:manual.writes)assert(!(std::get<0>(w)==nabu::UsbBlock::Gcc&&std::get<1>(w)==0));
    Usb stuck;stuck.clockStuck=true;nabu::Usb2Core<Usb> c2(stuck);assert(!c2.clocksAndReset());
    assert(std::string(c2.lastPhase())=="RCG-master"&&c2.failedOffset()==0xf01c);
    Usb gated;gated.branchStuck=true;nabu::Usb2Core<Usb> blocked(gated);assert(!blocked.clocksAndReset());
    assert(std::string(blocked.lastPhase())=="branch-ready"&&blocked.failedOffset()==0xf010);
    Usb reset;reset.resetStuck=true;nabu::Usb2Core<Usb> c3(reset);assert(!c3.resetXhci());
    assert(c3.failedOffset()==reset.cap&&reset.elapsedUs<=1000000);
    // Every GCC write is domain-local, except the additive shared PLL vote.
    // In particular UFS 0x77000..0x77fff, GPLL0 MODE and global resets survive.
    for(auto w:u.writes)if(std::get<0>(w)==nabu::UsbBlock::Gcc){
        unsigned o=std::get<1>(w);assert((o>=0xf000&&o<0xf080)||o==0x12000||o==0x52000);
    }
    // SM8150's DWC_usb31 family must reach real Host mode/HCRST, not be
    // rejected by the legacy DWC_usb3 GSNPSID check.
    Usb usb31;usb31.regs[{nabu::UsbBlock::Core,0xc120}]=0x33310000;
    usb31.regs[{nabu::UsbBlock::Core,0xc1a0}]=0x3137302a;
    nabu::Usb2Core<Usb> c31(usb31);assert(c31.selectUtmiHost()&&c31.resetXhci());
    assert(c31.coreId()==0x33310000&&c31.coreRevision()==0x3137302a);
    assert((usb31.read(nabu::UsbBlock::Core,0xc110)&0x3000)==0x1000);
    assert((usb31.read(nabu::UsbBlock::Core,0xc200)&((1u<<31)|(1u<<8)|(1u<<6)|(1u<<4)|(1u<<3)))==0);
    bool roleResetSeen=false;
    for(auto w:usb31.writes)if(std::get<0>(w)==nabu::UsbBlock::Core&&std::get<1>(w)==0xc110)
        roleResetSeen|=bool(std::get<2>(w)&(1u<<11));
    assert(roleResetSeen);
    // usb31 1.90a+ uses host HCRST rather than the older GCTL role reset.
    Usb newer;newer.regs[{nabu::UsbBlock::Core,0xc120}]=0x33310000;
    newer.regs[{nabu::UsbBlock::Core,0xc1a0}]=0x3139302a;
    nabu::Usb2Core<Usb> cnew(newer);assert(cnew.selectUtmiHost()&&cnew.resetXhci());
    for(auto w:newer.writes)if(std::get<0>(w)==nabu::UsbBlock::Core&&std::get<1>(w)==0xc110)
        assert(!(std::get<2>(w)&(1u<<11)));
    for(unsigned id:{0u,0xffffffffu,0x33320000u,0x12340000u}){
        Usb unknown;unknown.regs[{nabu::UsbBlock::Core,0xc120}]=id;
        nabu::Usb2Core<Usb> cx(unknown);assert(!cx.selectUtmiHost());
        assert(std::string(cx.lastPhase())=="controller-id"&&cx.failedOffset()==0xc120);
        for(auto w:unknown.writes)assert(std::get<0>(w)!=nabu::UsbBlock::Core);
    }
    for(unsigned rev:{0u,0xffffffffu}){
        Usb invalid;invalid.regs[{nabu::UsbBlock::Core,0xc120}]=0x33310000;
        invalid.regs[{nabu::UsbBlock::Core,0xc1a0}]=rev;
        nabu::Usb2Core<Usb> ci(invalid);assert(!ci.selectUtmiHost()&&ci.failedOffset()==0xc1a0);
    }
    Usb notReady;notReady.regs[{nabu::UsbBlock::Core,notReady.cap+4}]=1|(1u<<11);
    nabu::Usb2Core<Usb> cnr(notReady);assert(!cnr.resetXhci());
    assert(std::string(cnr.lastPhase())=="XHCI-ready"&&cnr.failedOffset()==notReady.cap+4);
    assert(notReady.elapsedUs<=1000000);
    Pmic p;nabu::UsbVbus<Pmic> vbus(p);assert(vbus.initializeRole()==nabu::InputError::Ok);p.writes=0;assert(vbus.enable()==nabu::InputError::Busy&&p.writes==0);
    p.cc=1;assert(vbus.enable()==nabu::InputError::Busy&&p.writes==0); // attached sink -> no backfeed
    assert(!nabu::UsbVbus<Pmic>::wantSource(0x41,0x10,false));
    assert(nabu::UsbVbus<Pmic>::wantSource(0x41,0x10,true)); // own 5V sense must not cause VBUS oscillation
    assert(!nabu::UsbVbus<Pmic>::wantSource(1,0,true));
    p.cc=0x41;assert(vbus.enable()==nabu::InputError::Ok&&p.cmd==1&&p.cfg==0);
    assert(vbus.disable()==nabu::InputError::Ok&&p.cmd==0);
    uint8_t db[192]={};db[4]=0xdb;db[5]=0x30;db[6]=3;db[7]=0xc;db[14]=1;
    const char id[]="ldoa5";for(unsigned i=0;i<5;i++)db[144+i]=id[i];db[160]=0x34;db[161]=0x12;
    unsigned addr=0;assert(nabu::cmdDbAddress(db,sizeof(db),id,addr)&&addr==0x1234);
    assert(!nabu::cmdDbAddress(db,155,id,addr));assert(!nabu::cmdDbAddress(db,sizeof(db),"missing",addr));
    Tcs tcs;nabu::RpmhTcs<Tcs> request(tcs);assert(request.send(0,0x1234,880));
    assert(tcs.regs[0xd34]==0x1234&&tcs.regs[0xd38]==880&&tcs.regs[0xd1c]==0);
    Tcs busy;busy.regs[0xd1c]=1;nabu::RpmhTcs<Tcs> busyRequest(busy);assert(!busyRequest.send(0,1,1));
    Tcs timeout;timeout.complete=false;nabu::RpmhTcs<Tcs> timed(timeout);assert(!timed.send(0,1,1));
    assert(timeout.regs[0xd1c]==1); // don't reuse a timed-out command
    namespace sm=nabu_usb::smmu;
    std::map<unsigned,unsigned> mmio;
    mmio[sm::kIdr0]=0x4c017e5e;mmio[sm::kIdr1]=0x20000038;mmio[sm::kIdr2]=0x1010;
    auto read=[&](unsigned o){return mmio[o];};sm::Info info{};assert(sm::read_info(read,&info));
    // Existing UFS mapping remains byte-for-byte intact; USB takes an unused bank.
    mmio[sm::kSmrBase]=sm::kSmrValid|0x300;mmio[sm::kS2crBase]=55;
    auto plan=nabu::usbIdentityPlan(read,info,0x140);assert(plan.valid&&plan.index==1&&plan.bank==54);
    assert(mmio[sm::kSmrBase]==(sm::kSmrValid|0x300)&&mmio[sm::kS2crBase]==55);
    mmio[sm::kSmrBase+4]=sm::kSmrValid|0x140;mmio[sm::kS2crBase+4]=53;
    plan=nabu::usbIdentityPlan(read,info,0x140);assert(plan.valid&&plan.index==1&&plan.bank==54);
    mmio[sm::kSmrBase+8]=sm::kSmrValid|0x140;assert(!nabu::usbIdentityPlan(read,info,0x140).valid);
    mmio[sm::kSmrBase+8]=0;mmio[sm::kSmrBase+4]=0;
    for(unsigned b=2;b<info.num_cb;++b)mmio[sm::context_offset(info.global_pages,b)]=1;
    assert(!nabu::usbIdentityPlan(read,info,0x140).valid); // CB1 must stay reserved even if apparently idle
    mmio.clear();mmio[sm::kSmrBase]=sm::kSmrValid|1;mmio[sm::kSmrBase+4]=sm::kSmrValid|2;
    plan=nabu::usbIdentityPlan(read,info,0x140);assert(plan.valid&&plan.index!=2&&plan.bank!=1);
    assert(!nabu::usbIdentityBlock(0)&&!nabu::usbIdentityBlock(1)&&!nabu::usbIdentityBlock(8));
    assert((nabu::usbIdentityBlock(3)&~0x3fffffffull)==0xc0000000ull);
    puts("SPMI v5, debounce, long press, USB core sequencing/timeouts, VBUS role, CommandDB: PASS");
}
