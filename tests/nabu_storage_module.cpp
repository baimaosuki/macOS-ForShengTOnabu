// SPDX-License-Identifier: BSD-2-Clause-Patent
// Execute the actual staged production module adapter with IOKit API fixtures.
#include <cassert>
#include <cstdarg>
#include <cstdint>
#include <string>
#include <vector>
#include "NabuStorageLifecycle.h"

static bool abiOkay = true, registryValid = true;
static unsigned ufsStarts, dataStarts, ufsAllocations, dataAllocations;
static bool failUfs, failData;
static unsigned sleeps, recoverAfter;
static std::vector<std::string> events;
static void IOLog(const char *format, ...) { (void)format; }
static void IOSleep(unsigned milliseconds) {
  assert(milliseconds == 100);
  if (++sleeps == recoverAfter) registryValid = true;
}
struct IORegistryEntry {
  virtual ~IORegistryEntry() = default;
  std::vector<IORegistryEntry *> children;
  unsigned refs = 1;
  void retain() { ++refs; }
  void release() { assert(refs); --refs; }
  static IORegistryEntry *getRegistryRoot() { static IORegistryEntry root; return &root; }
};
struct OSSymbol : IORegistryEntry {
  static const OSSymbol *withCString(const char *) { static OSSymbol name; return &name; }
  void release() const {}
};
struct IOService;
struct OSMetaClass {
  unsigned getClassSize() const;
  static const OSMetaClass *getMetaClassWithName(const OSSymbol *) { static OSMetaClass m; return &m; }
  static IORegistryEntry *allocClassWithName(const char *);
};
struct IOService : IORegistryEntry {
  bool ufsProvider = false, dataProvider = false, inactive = false;
  bool isUfs = false, isData = false, attached = false, started = false;
  bool isInactive() const { return inactive; }
  void *metaCast(const char *name) { return std::string(name) == "IOPlatformExpertDevice" && dataProvider ? this : nullptr; }
  virtual bool init() { return true; }
  bool attach(IOService *) { attached = true; return true; }
  void detach(IOService *) { attached = false; }
  bool start(IOService *p) {
    if (isUfs) { assert(p->ufsProvider); ++ufsStarts; events.push_back("UFS"); return started = !failUfs; }
    assert(isData && p->dataProvider && ufsStarts == 1);
    ++dataStarts; events.push_back("Data"); return started = !failData;
  }
  static IOService *getServiceRoot();
};
static IOService platformRoot;
IOService *IOService::getServiceRoot() { return &platformRoot; }
struct IOBlockStorageDevice : IOService {};
unsigned OSMetaClass::getClassSize() const { return abiOkay ? sizeof(IOBlockStorageDevice) : 1; }
static IOService ufsDevice, dataDevice;
IORegistryEntry *OSMetaClass::allocClassWithName(const char *name) {
  if (std::string(name) == "NativeUfs") { ++ufsAllocations; ufsDevice.isUfs = true; return &ufsDevice; }
  assert(std::string(name) == "NativeRamBlock"); ++dataAllocations; dataDevice.isData = true; return &dataDevice;
}
static int plane;
static const int *gIOServicePlane = &plane;
static constexpr unsigned kIORegistryIterateRecursively = 1;
struct IORegistryIterator {
  std::vector<IORegistryEntry *> entries;
  unsigned index = 0;
  void collectChildren(IORegistryEntry *root) {
    for (IORegistryEntry *child : root->children) {
      entries.push_back(child);
      collectChildren(child);
    }
  }
  static IORegistryIterator *iterateOver(IORegistryEntry *root, const int *, unsigned) {
    auto *iterator = new IORegistryIterator;
    iterator->collectChildren(root); // Real API excludes root itself.
    return iterator;
  }
  IORegistryEntry *getNextObject() { return index < entries.size() ? entries[index++] : nullptr; }
  bool isValid() { return registryValid; }
  void release() { delete this; }
};
static bool nabuRootProvider(IOService *p) { return p->ufsProvider; }
static volatile uint32_t native_ufs_root_step;
static const char *volatile native_ufs_root_failure;
#define OSDynamicCast(type, value) dynamic_cast<type *>(value)
#define OSSafeReleaseNULL(value) do { if (value) (value)->release(); (value) = nullptr; } while (0)
#include "NabuStorageModule.h"
extern "C" int nabu_register_storage_start(NabuStorageStart) { return 1; }

int main()
{
  IOService ufs, duplicateUfs, duplicatePlatform;
  IOService &platform = platformRoot;
  ufs.ufsProvider = true; platform.dataProvider = true;
  duplicateUfs.ufsProvider = true; duplicatePlatform.dataProvider = true;
  // The only platform provider IS getServiceRoot(), never its descendant.
  // Starting traversal at the service root must miss it, reproducing the bug.
  platform.children = {&ufs};
  auto *serviceIterator = IORegistryIterator::iterateOver(IOService::getServiceRoot(), gIOServicePlane, 1);
  assert(serviceIterator->getNextObject() == &ufs);
  assert(!serviceIterator->getNextObject());
  serviceIterator->release();
  for (unsigned failure = 0; failure < 11; ++failure) {
    gNabuStorageModuleState = 0; gNabuLateUfs = gNabuLateData = nullptr;
    ufsStarts = dataStarts = ufsAllocations = dataAllocations = 0;
    sleeps = 0; recoverAfter = failure == 7 ? 1 : 0;
    ufsDevice = {}; dataDevice = {}; events.clear();
    abiOkay = failure != 1; registryValid = failure != 2 && failure != 7;
    failUfs = failure == 5; failData = failure == 6;
    ufs.inactive = failure == 10;
    IORegistryEntry::getRegistryRoot()->children = {&platform};
    platform.children = {&ufs};
    if (failure == 3) platform.children = {};
    if (failure == 4) platform.children = {&ufs, &duplicateUfs};
    if (failure == 8) IORegistryEntry::getRegistryRoot()->children = {&ufs};
    if (failure == 9) IORegistryEntry::getRegistryRoot()->children = {&platform, &duplicatePlatform};
    const char *error = nabu_storage_module_start();
    assert((error != nullptr) == (failure != 0 && failure != 7));
    if (!failure || failure == 7) {
      assert((events == std::vector<std::string>{"UFS", "Data"}));
      assert(platform.dataProvider && IOService::getServiceRoot() == &platform);
      assert(platform.children.size() == 1 && platform.children[0] == &ufs);
    }
    if (failure == 2 || failure == 3 || failure == 8 || failure == 10) assert(sleeps == 50);
    if (failure == 4 || failure == 9) assert(sleeps == 0); // Ambiguity never selects the first provider.
    if (failure == 7) assert(sleeps == 1); // Invalid snapshot recovers without leaking retains.
    if (failure < 5 && failure) assert(!ufsAllocations && !dataAllocations);
    if (failure == 5) assert(ufsStarts == 1 && !dataStarts && !ufsDevice.attached && !gNabuLateUfs);
    if (failure == 6) assert(gNabuLateUfs == &ufsDevice && ufsDevice.started && ufsDevice.refs == 1 && !dataDevice.attached);
    assert(ufs.refs == 1 && platform.refs == 1);
    assert(duplicateUfs.refs == 1 && duplicatePlatform.refs == 1);
    auto previous = events;
    assert(nabu_storage_module_start());
    assert(events == previous);
  }
}
