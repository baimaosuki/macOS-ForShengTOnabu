// SPDX-License-Identifier: BSD-2-Clause-Patent
#include "NabuInputHardware.h"
#include <IOKit/IOService.h>
#include <pexpert/pexpert.h>
#include <IOKit/IODeviceTreeSupport.h>
#include <libkern/c++/OSData.h>
namespace {
bool hardwareDescription(){
    auto *entry=IORegistryEntry::fromPath("/nabu-input",gIODTPlane);
    if(!entry)return false;
    auto *data=OSDynamicCast(OSData,entry->getProperty("reg"));
    const uint64_t expected[]={0x100000,0x100000,0xc84000,0x4000,0x3517000,0x4000,0x3927000,0x1000};
    bool ok=data&&data->getLength()==sizeof(expected)&&!memcmp(data->getBytesNoCopy(),expected,sizeof(expected));
    entry->release();return ok;
}
}
extern "C" bool nabu_touch_publish(IOService *);
extern "C" bool nabu_power_publish(IOService *);
extern "C" bool nabu_usb_publish(IOService *);
extern "C" void nabu_input_start(IOService *platform){
    static unsigned started=0;
    if(!platform || __atomic_exchange_n(&started,1,__ATOMIC_ACQ_REL))return;
    if(!hardwareDescription()){IOLog("NABU INPUT: missing or incompatible production ADT resources\n");return;}
    // Create the PMIC bus and registration lock before launching independent workers.
    if(!nabu::familyRegistrationLock()){IOLog("NABU INPUT: registration lock allocation failed\n");return;}
    if(!nabu::pmic().initialize())IOLog("NABU INPUT: SPMI unavailable; USB and power will retry probe\n");
    uint32_t enabled=0;
    if(PE_parse_boot_argn("nabu-usb-host",&enabled,sizeof(enabled))&&enabled==1)
        IOLog("NABU INPUT: USB worker=%u\n",nabu_usb_publish(platform));
    enabled=0;
    if(PE_parse_boot_argn("nabu-power-key",&enabled,sizeof(enabled))&&enabled==1)
        IOLog("NABU INPUT: power worker=%u\n",nabu_power_publish(platform));
    IOLog("NABU INPUT: touch worker=%u\n",nabu_touch_publish(platform));
}
