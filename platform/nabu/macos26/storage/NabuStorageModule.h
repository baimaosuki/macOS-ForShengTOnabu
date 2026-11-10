// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once
// Included by the actual NativeUfs translation-unit wrapper, after the driver.
// OSKext owns all existing constructors; no preModLoad or manual ctor calls.
#include "NabuStorageLifecycle.h"
#include <libkern/c++/OSSymbol.h>

extern "C" int nabu_register_storage_start(NabuStorageStart);
extern "C" const char *nabu_storage_module_start();
static unsigned gNabuStorageModuleState;
static IOService *gNabuLateUfs, *gNabuLateData;

struct NabuStorageDevices {
  IOService *allocate(const char *name) {
    return OSDynamicCast(IOService, OSMetaClass::allocClassWithName(name));
  }
  bool init(IOService *service) { return service->init(); }
  bool attach(IOService *service, IOService *provider) { return service->attach(provider); }
  bool start(IOService *service, IOService *provider) { return service->start(provider); }
  void detach(IOService *service, IOService *provider) { service->detach(provider); }
  void release(IOService *service) { service->release(); }
};

struct NabuStorageModuleOperations {
  IOService *ufsProvider = nullptr, *dataProvider = nullptr;
  void stage(const char *name) { IOLog("NABU storage: %s\n", name); }
  const char *checkAbi() {
    const OSSymbol *name = OSSymbol::withCString("IOBlockStorageDevice");
    const OSMetaClass *base = name ? OSMetaClass::getMetaClassWithName(name) : nullptr;
    OSSafeReleaseNULL(name);
    if (!base || base->getClassSize() != sizeof(IOBlockStorageDevice))
      return "IOBlockStorageDevice class size mismatch";
    return nullptr;
  }
  const char *findProviders() { return nabuFindStorageProviders(*this); }
  void pause(unsigned milliseconds) { IOSleep(milliseconds); }
  NabuStorageProviders scanProviders() {
    IORegistryIterator *iterator = IORegistryIterator::iterateOver(
      // The iterator excludes its starting entry. The service root itself is
      // IOPlatformExpertDevice, so start at the registry root to include it.
      IORegistryEntry::getRegistryRoot(), gIOServicePlane, kIORegistryIterateRecursively);
    if (!iterator) return NabuStorageProviders::Retry;
    unsigned entries = 0, ufsCount = 0, dataCount = 0;
    bool complete = false;
    while (IORegistryEntry *entry = iterator->getNextObject()) {
      if (++entries > 4096) break;
      IOService *service = OSDynamicCast(IOService, entry);
      if (!service || service->isInactive()) continue;
      // nabuRootProvider checks the exact board resources and clock state; the
      // very same check runs again inside NativeUfs::start before HC access.
      if (nabuRootProvider(service)) {
        ++ufsCount;
        if (!ufsProvider) { service->retain(); ufsProvider = service; }
      }
      if (service->metaCast("IOPlatformExpertDevice")) {
        ++dataCount;
        if (!dataProvider) { service->retain(); dataProvider = service; }
      }
    }
    complete = entries <= 4096 && iterator->isValid();
    iterator->release();
    if (!complete) return NabuStorageProviders::Retry;
    if (ufsCount > 1 || dataCount > 1) return NabuStorageProviders::Ambiguous;
    return ufsCount == 1 && dataCount == 1 ? NabuStorageProviders::Ready : NabuStorageProviders::Retry;
  }
  const char *startUfs() {
    NabuStorageDevices devices;
    const char *failure = nabuStartStorageDevice(devices, "NativeUfs", ufsProvider, gNabuLateUfs);
    if (failure) IOLog("NABU storage: UFS %s failed (root step=%u reason=%s)\n", failure,
      native_ufs_root_step, native_ufs_root_failure ? native_ufs_root_failure : "none");
    return failure ? "UFS init/attach/start failed (see stage log)" : nullptr;
  }
  const char *startData() {
    NabuStorageDevices devices;
    const char *failure = nabuStartStorageDevice(devices, "NativeRamBlock", dataProvider, gNabuLateData);
    if (failure) IOLog("NABU storage: Data %s failed\n", failure);
    return failure ? "Data init/attach/start failed (see stage log)" : nullptr;
  }
  void releaseProviders() {
    OSSafeReleaseNULL(ufsProvider);
    OSSafeReleaseNULL(dataProvider);
  }
};

extern "C" const char *nabu_storage_module_start()
{
  NabuStorageModuleOperations operations;
  return nabuStartStorageModuleOnce(gNabuStorageModuleState, operations);
}
