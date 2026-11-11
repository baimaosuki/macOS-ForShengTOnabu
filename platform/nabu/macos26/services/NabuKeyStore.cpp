// SPDX-License-Identifier: BSD-2-Clause-Patent
// NABU software-only AppleKeyStore glue. No SEP, passcode or hardware security.
#include "NabuKeyStore.h"
#include <IOKit/IODeviceTreeSupport.h>
#include <libkern/c++/OSData.h>
#include <libkern/c++/OSSymbol.h>
#include <libkern/OSAtomic.h>
#include <pexpert/pexpert.h>
extern "C" IOService *native_keyevents_allocate(void);
namespace {
volatile UInt32 startState;
IOService *ownedService;
uint8_t identity[16];
bool identityReady;
volatile UInt32 opens, calls, refused;
volatile UInt32 selectors[161];
volatile SInt32 results[161];
bool loadIdentity() {
    // The loader's existing stable platform UUID lives in /options. Never use
    // boot UUID, a volume UUID, a random per-boot value, or a shared fallback.
    IORegistryEntry *options = IORegistryEntry::fromPath("/options", gIODTPlane);
    OSData *data = options ? OSDynamicCast(OSData, options->getProperty("platform-uuid")) : nullptr;
    bool valid = data && data->getLength() == sizeof identity;
    if (valid) {
        const uint8_t *p = static_cast<const uint8_t *>(data->getBytesNoCopy());
        bool nonzero = false;
        for (unsigned i = 0; i < sizeof identity; ++i) { identity[i] = p[i]; nonzero |= p[i] != 0; }
        valid = nonzero;
    }
    if (options) options->release();
    return valid;
}
}
extern "C" bool native_aks_state_enabled(void) { return true; }
extern "C" uint32_t native_aks_lock_state(void) { return 0; }
extern "C" size_t native_aks_device_id(uint8_t *out, size_t capacity) {
    if (!identityReady || !out || capacity < sizeof identity) return 0;
    for (unsigned i = 0; i < sizeof identity; ++i) out[i] = identity[i];
    return sizeof identity;
}
extern "C" void native_aks_opened(void) { OSIncrementAtomic(reinterpret_cast<volatile SInt32 *>(&opens)); }
extern "C" void native_keyevents_open_refused(void) { OSIncrementAtomic(reinterpret_cast<volatile SInt32 *>(&refused)); }
extern "C" void native_aks_call(uint32_t selector, int32_t result) {
    OSIncrementAtomic(reinterpret_cast<volatile SInt32 *>(&calls));
    if (selector < 161) {
        SInt32 old = OSIncrementAtomic(reinterpret_cast<volatile SInt32 *>(&selectors[selector]));
        results[selector] = result;
        if (old < 4) IOLog("NABU AKS: selector=%u result=0x%x\n", selector, (unsigned)result);
    }
}
extern "C" IOReturn nabu_keystore_start(IOService *resources) {
    if (!resources || resources != IOService::getResourceService()) return kIOReturnNotReady;
    if (!OSCompareAndSwap(0, 1, &startState)) return ownedService ? kIOReturnSuccess : kIOReturnBusy;
    identityReady = loadIdentity();
    IOService *service = native_keyevents_allocate();
    bool initialized = service && service->init();
    bool attached = initialized && service->attach(resources);
    bool started = attached && service->start(resources);
    if (!started) {
        if (attached) service->detach(resources);
        if (service) service->release();
        startState = 0;
        return kIOReturnError;
    }
    // Attachment owns the active service's lifetime; retain the allocation
    // reference because this companion is never unloaded after early startup.
    ownedService = service;
    service->setProperty("NabuSoftwareKeyStore", true);
    service->setProperty("HardwareDataProtection", false);
    service->setProperty("StableIdentityAvailable", identityReady);
    IOLog("NABU AKS: registered software service stable-platform-uuid=%u\n", identityReady);
    return kIOReturnSuccess;
}
