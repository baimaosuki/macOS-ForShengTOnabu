// SPDX-License-Identifier: BSD-2-Clause-Patent
#include "OriginalHidDeviceABI.h"
#include "NabuInputHardware.h"
#include "NabuPowerReport.h"
#include <IOKit/IOBufferMemoryDescriptor.h>
#include <IOKit/IOWorkLoop.h>
#include <IOKit/IOCatalogue.h>
#include <libkern/c++/OSArray.h>
#include <libkern/c++/OSNumber.h>
#include <kern/thread.h>
#include <kern/clock.h>
#include <mach/kmod.h>
extern "C" {
extern kmod_info_t g_kernel_kmod_info;
kern_return_t OSKextLoadKextWithIdentifier(const char *);
void nabu_power_static_init(void) __asm__("__GLOBAL__sub_I_NabuPowerKey.cpp");
}
// Consumer Control / Power. Native IOHIDEventDriver dispatches a keyboard
// event with usage page 0x0c / usage 0x30; no private IOHIDEvent layout.
class NabuPowerHid : public IOHIDDevice {
    OSDeclareDefaultStructors(NabuPowerHid)
    IOWorkLoop *loop=nullptr;
    static IOReturn sendAction(OSObject *owner,void *arg,void *,void *,void *) {
        auto *self=static_cast<NabuPowerHid *>(owner);
        uint8_t bytes[2];nabuPowerReport(*static_cast<bool *>(arg),bytes);
        auto *buffer=IOBufferMemoryDescriptor::withBytes(bytes,sizeof(bytes),kIODirectionNone);
        IOReturn r=buffer?self->handleReport(buffer,kIOHIDReportTypeInput,0):kIOReturnNoMemory;
        OSSafeReleaseNULL(buffer);return r;
    }
public:
    IOWorkLoop *getWorkLoop() const override {return loop;}
    OSString *newTransportString() const override {return OSString::withCString("SPMI");}
    OSString *newProductString() const override {return OSString::withCString("NABU PM8150 power key");}
    IOReturn newReportDescriptor(IOMemoryDescriptor **out) const override {
        if(!out)return kIOReturnBadArgument;
        *out=IOBufferMemoryDescriptor::withBytes(kNabuPowerDescriptor,sizeof(kNabuPowerDescriptor),kIODirectionNone);
        return *out?kIOReturnSuccess:kIOReturnNoMemory;
    }
    bool start(IOService *provider) override {
        loop=IOWorkLoop::workLoop();if(!loop)return false;
        setProperty("Built-In",true);setProperty("HIDDefaultBehavior",true);
        return IOHIDDevice::start(provider);
    }
    void stop(IOService *provider) override {IOHIDDevice::stop(provider);}
    void free() override {OSSafeReleaseNULL(loop);IOHIDDevice::free();}
    IOReturn report(bool pressed){return loop?loop->runAction(sendAction,this,&pressed):kIOReturnNotReady;}
};
OSDefineMetaClassAndStructors(NabuPowerHid,IOHIDDevice)
namespace {
bool addPersonality(){
    auto *p=OSDictionary::withCapacity(5);auto *u=OSDictionary::withCapacity(2);
    auto *pairs=OSArray::withCapacity(1);auto *list=OSArray::withCapacity(1);
    auto *page=OSNumber::withNumber(0x0c,32);auto *usage=OSNumber::withNumber(1,32);
    auto *family=OSString::withCString("com.apple.iokit.IOHIDFamily");
    auto *klass=OSString::withCString("IOHIDEventDriver");auto *provider=OSString::withCString("IOHIDInterface");
    bool ok=p&&u&&pairs&&list&&page&&usage&&family&&klass&&provider;
    if(ok)ok=u->setObject("DeviceUsagePage",page)&&u->setObject("DeviceUsage",usage)&&pairs->setObject(u)&&
        p->setObject("CFBundleIdentifier",family)&&p->setObject("IOClass",klass)&&p->setObject("IOProviderClass",provider)&&
        p->setObject("DeviceUsagePairs",pairs)&&list->setObject(p);
    if(ok)ok=gIOCatalogue&&gIOCatalogue->addDrivers(list,true);
    OSSafeReleaseNULL(provider);OSSafeReleaseNULL(klass);OSSafeReleaseNULL(family);
    OSSafeReleaseNULL(page);OSSafeReleaseNULL(usage);OSSafeReleaseNULL(list);OSSafeReleaseNULL(pairs);
    OSSafeReleaseNULL(u);OSSafeReleaseNULL(p);return ok;
}
void worker(void *context,wait_result_t){
    auto *platform=static_cast<IOService *>(context);
    if(!nabu::pmic().initialize()){IOLog("NABU POWER: SPMI unavailable\n");platform->release();return;}
    uint8_t revision=0,status=0;
    auto e=nabu::pmic().read(0,0x0805,revision);
    if(e!=nabu::InputError::Ok || revision!=4 || nabu::pmic().read(0,0x0810,status)!=nabu::InputError::Ok){
        IOLog("NABU POWER: PON probe failed error=%u subtype=%02x\n",unsigned(e),revision);platform->release();return;
    }
    NabuPowerHid *hid=nullptr;
    {
    nabu::FamilyRegistration registration;
    if(!registration.valid()){platform->release();return;}
    auto load=OSKextLoadKextWithIdentifier("com.apple.iokit.IOHIDFamily");
    if(load!=KERN_SUCCESS || !addPersonality()){IOLog("NABU POWER: HID load=%x\n",load);platform->release();return;}
    void *handle=OSMetaClass::preModLoad(g_kernel_kmod_info.name);
    if(!handle){platform->release();return;}
    nabu_power_static_init();auto meta=OSMetaClass::postModLoad(handle);
    if(meta!=kOSReturnSuccess){IOLog("NABU POWER: metaclass=%x\n",meta);platform->release();return;}
    hid=new NabuPowerHid;
    if(!hid || !hid->init() || !hid->attach(platform)){
        OSSafeReleaseNULL(hid);platform->release();return;
    }
    if(!hid->start(platform)){hid->detach(platform);hid->release();platform->release();return;}
    hid->registerService();platform->release();
    }
    IOLog("NABU POWER: PM8150 SID0 PON=0800 RT_STS=0810 KPDPWR=bit0 subtype=%02x polling=10ms debounce=30ms\n",revision);
    nabu::PowerKeyState state;unsigned failures=0;bool pressed=false;
    for(;;){
        e=nabu::pmic().read(0,0x0810,status);
        if(e!=nabu::InputError::Ok){
            if(++failures==3 && pressed){auto r=hid->report(false);pressed=false;IOLog("NABU POWER: bus lost release=%x\n",r);}
            if(failures==3)state={};
            IOSleep(10);continue;
        }
        failures=0;uint64_t ns=0;absolutetime_to_nanoseconds(mach_absolute_time(),&ns);
        unsigned events=state.sample(status&1,ns/1000000);
        if(events&3){pressed=events&1;auto r=hid->report(pressed);IOLog("NABU POWER: %s RT_STS=%02x HID=%x\n",pressed?"down":"up",status,r);}
        if(events&4)IOLog("NABU POWER: short press\n");
        if(events&8)IOLog("NABU POWER: long press (hardware reset policy preserved)\n");
        IOSleep(10);
    }
}
}
extern "C" bool nabu_power_publish(IOService *platform){
    if(!platform)return false;platform->retain();thread_t t;
    if(kernel_thread_start(worker,platform,&t)!=KERN_SUCCESS){platform->release();return false;}
    thread_deallocate(t);return true;
}
