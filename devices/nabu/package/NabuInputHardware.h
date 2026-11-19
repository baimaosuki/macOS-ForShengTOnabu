// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once
#include <IOKit/IODeviceMemory.h>
#include <IOKit/IOLib.h>
#include <IOKit/IOLocks.h>
#include "NabuSpmiV5.h"
namespace nabu {
struct RegisterWindow {
    IOMemoryMap *mapping=nullptr;
    volatile uint32_t *words=nullptr;
    uint64_t length=0;
    bool map(uint64_t address,uint64_t bytes) {
        if(mapping)return length==bytes;
        auto *memory=IODeviceMemory::withRange(address,bytes);
        mapping=memory?memory->map(kIOMapAnywhere|kIOMapInhibitCache):nullptr;
        OSSafeReleaseNULL(memory);
        if(!mapping)return false;
        words=reinterpret_cast<volatile uint32_t *>(mapping->getVirtualAddress());length=bytes;
        return words!=nullptr;
    }
    uint32_t read(uint32_t offset) const {return words[offset/4];}
    void write(uint32_t offset,uint32_t value) {words[offset/4]=value;asm volatile("dsb sy":::"memory");}
    bool update(uint32_t offset,uint32_t mask,uint32_t value) {
        write(offset,(read(offset)&~mask)|(value&mask));return (read(offset)&mask)==(value&mask);
    }
    bool wait(uint32_t offset,uint32_t mask,uint32_t value,unsigned timeoutUs) const {
        for(unsigned i=0;i<timeoutUs;i+=10){if((read(offset)&mask)==value)return true;IODelay(10);}
        return (read(offset)&mask)==value;
    }
};
// A single shared owner for USB VBUS and PON transactions.
class PmicBus {
    RegisterWindow core,owner,observer,channels;
    IOLock *lock=nullptr;
    bool mapped=false;
public:
    bool initialize();
    uint32_t coreRead(unsigned off){return core.read(off);}
    uint32_t ownerRead(unsigned off){return owner.read(off);}
    uint32_t readChannel(bool wr,unsigned off){return (wr?channels:observer).read(off);}
    void writeChannel(bool wr,unsigned off,uint32_t v){(wr?channels:observer).write(off,v);}
    void delayUs(unsigned us){IODelay(us);}
    InputError read(uint8_t sid,uint16_t address,uint8_t &value);
    InputError update(uint8_t sid,uint16_t address,uint8_t mask,uint8_t value);
};
PmicBus &pmic();
IOLock *familyRegistrationLock();
class FamilyRegistration {
    IOLock *lock;
public:
    FamilyRegistration():lock(familyRegistrationLock()){if(lock)IOLockLock(lock);}
    ~FamilyRegistration(){if(lock)IOLockUnlock(lock);}
    bool valid() const{return lock!=nullptr;}
};
}
