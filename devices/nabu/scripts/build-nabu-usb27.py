#!/usr/bin/env python3
"""Native 26C5035r AppleUSBXHCI declaration and original method bindings."""
import json
from pathlib import Path

def header(abi):
    # Original XHCI inherits IOService::Dispatch (native kernel), not a kext override.
    # Redeclaring it would bind to the discarded original kernel address.
    text='''// Generated from original 26C5035r class, plain arm64 ABI.
#pragma once
#include <IOKit/IOService.h>
#include <IOKit/IOBufferMemoryDescriptor.h>
#include <IOKit/IODMACommand.h>
class AppleUSBXHCI : public IOService {
 OSDeclareDefaultStructors(AppleUSBXHCI)
 unsigned char originalPrivate[0xf88-sizeof(IOService)];
public:
 void free() override;
 bool init(OSDictionary *properties=nullptr) override;
 IOReturn setProperties(OSObject *) override;
 bool didTerminate(IOService *,IOOptionBits,bool *) override;
 void systemWillShutdown(IOOptionBits) override;
 void registerService(IOOptionBits options=0) override;
 bool start(IOService *) override;
 void stop(IOService *) override;
 bool open(IOService *,IOOptionBits=0,void * =nullptr) override;
 void close(IOService *,IOOptionBits=0) override;
 bool handleOpen(IOService *,IOOptionBits,void *) override;
 void handleClose(IOService *,IOOptionBits) override;
 bool handleIsOpen(const IOService *) const override;
 bool terminate(IOOptionBits=0) override;
 bool matchPropertyTable(OSDictionary *,SInt32 *) override;
 bool attach(IOService *) override;
 IOWorkLoop *getWorkLoop() const override;
 IOReturn message(UInt32,IOService *,void *) override;
 void PMstop() override;
 IOReturn setPowerState(unsigned long,IOService *) override;
 unsigned long maxCapabilityForDomainState(IOPMPowerFlags) override;
 unsigned long initialPowerStateForDomainState(IOPMPowerFlags) override;
 IOReturn powerStateWillChangeTo(IOPMPowerFlags,unsigned long,IOService *) override;
 IOReturn powerStateDidChangeTo(IOPMPowerFlags,unsigned long,IOService *) override;
'''
    methods={286:'IOReturn lowerOnePowerStateTo(unsigned long)',292:'uint32_t desiredPower()',
        335:'IOBufferMemoryDescriptor *createIOBuffer(uint32_t,uint64_t)',
        336:'IODMACommand *getDMACommand(uint32_t,uint32_t)',
        342:'uint32_t getBufferOptions(uint32_t) const',
        418:'uint32_t regRead32(void *,uint32_t)',419:'uint64_t regRead64(void *,uint32_t)',
        420:'void regWrite32(void *,uint32_t,uint32_t)',421:'void regWrite64(void *,uint32_t,uint64_t)'}
    for i in range(272,427):
        text+=' virtual '+methods.get(i,f'void opaque{i}()')+';\n'
    return text+'};\nstatic_assert(sizeof(AppleUSBXHCI)==0xf88,"original XHCI instance");\n'
