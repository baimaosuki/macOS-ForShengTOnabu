// SPDX-License-Identifier: BSD-2-Clause-Patent
#include "NabuInputHardware.h"
namespace nabu {
static PmicBus bus;
PmicBus &pmic(){return bus;}
IOLock *familyRegistrationLock(){
    static IOLock *shared=nullptr;
    IOLock *value=__atomic_load_n(&shared,__ATOMIC_ACQUIRE);
    if(value)return value;
    IOLock *created=IOLockAlloc();if(!created)return nullptr;
    IOLock *expected=nullptr;
    if(!__atomic_compare_exchange_n(&shared,&expected,created,false,__ATOMIC_RELEASE,__ATOMIC_ACQUIRE)){
        IOLockFree(created);return expected;
    }
    return created;
}
bool PmicBus::initialize() {
    // Called once on the input worker before USB/power workers begin.
    static unsigned initializing=0;
    while (__atomic_exchange_n(&initializing,1,__ATOMIC_ACQUIRE)) IOSleep(1);
    if(mapped){__atomic_store_n(&initializing,0,__ATOMIC_RELEASE);return true;}
    if(!lock)lock=IOLockAlloc();
    if(!lock){__atomic_store_n(&initializing,0,__ATOMIC_RELEASE);return false;}
    IOLockLock(lock);
    bool ok=core.map(0x0c440000,0x1100) && owner.map(0x0c40a000,0x26000) &&
            observer.map(0x0e600000,0x100000) && channels.map(0x0c600000,0x2000000);
    if(ok){SpmiV5<PmicBus> arb(*this);ok=arb.geometry();}
    mapped=ok;IOLockUnlock(lock);
    IOLog("NABU INPUT: SPMI-v5 mapped=%u version=%08x\n",ok,core.words?core.read(0):0);
    __atomic_store_n(&initializing,0,__ATOMIC_RELEASE);
    return ok;
}
InputError PmicBus::read(uint8_t sid,uint16_t address,uint8_t &value) {
    if(!mapped)return InputError::Geometry;
    IOLockLock(lock);SpmiV5<PmicBus> arb(*this);auto e=arb.read(sid,address,value);IOLockUnlock(lock);return e;
}
InputError PmicBus::update(uint8_t sid,uint16_t address,uint8_t mask,uint8_t value) {
    if(!mapped)return InputError::Geometry;
    IOLockLock(lock);SpmiV5<PmicBus> arb(*this);auto e=arb.update(sid,address,mask,value);IOLockUnlock(lock);return e;
}
}
