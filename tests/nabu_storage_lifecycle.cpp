// SPDX-License-Identifier: BSD-2-Clause-Patent
#include <cassert>
#include <cstring>
#include <string>
#include <vector>
#include "NabuStorageLifecycle.h"

static unsigned callbacks;
static bool loading;
static bool callbackFails;
static const char *callback() { assert(!loading); ++callbacks; return callbackFails ? "module failed" : nullptr; }
static const char *other() { return "other"; }
struct KernelOps {
  std::vector<std::string> events;
  NabuStorageStart &slot;
  unsigned loads = 0, fail = 0;
  bool missing = false;
  void stage(const char *s) { events.push_back(s); }
  bool load(const char *s) {
    loading = true;
    events.push_back(s);
    ++loads;
    if (loads == 3 && !missing) assert(nabuRegisterStorageStart(slot, callback));
    loading = false;
    return loads != fail;
  }
};
struct ModuleOps {
  std::vector<std::string> events;
  unsigned fail = 0, providersReleased = 0, calls = 0;
  const char *step(const char *s) { events.push_back(s); return ++calls == fail ? s : nullptr; }
  const char *checkAbi() { return step("ABI"); }
  const char *findProviders() { return step("providers"); }
  const char *startUfs() { return step("UFS"); }
  const char *startData() { return step("Data"); }
  void stage(const char *s) { events.push_back(s); }
  void releaseProviders() { ++providersReleased; }
};
struct Device { bool attached = false, started = false, released = false; };
struct DeviceOps {
  Device object;
  unsigned calls = 0, fail = 0, detached = 0;
  bool succeeds() { return ++calls != fail; }
  Device *allocate(const char *) { return succeeds() ? &object : nullptr; }
  bool init(Device *) { return succeeds(); }
  bool attach(Device *d, Device *) { return d->attached = succeeds(); }
  bool start(Device *d, Device *) { return d->started = succeeds(); }
  void detach(Device *d, Device *) { assert(d->attached); d->attached = false; ++detached; }
  void release(Device *d) { assert(!d->started); d->released = true; }
};

int main()
{
  NabuStorageStart slot = nullptr;
  assert(!nabuRegisterStorageStart(slot, nullptr));
  assert(nabuRegisterStorageStart(slot, callback));
  assert(nabuRegisterStorageStart(slot, callback));
  assert(!nabuRegisterStorageStart(slot, other));
  for (unsigned failure = 0; failure <= 5; ++failure) {
    slot = nullptr; unsigned state = 0; callbacks = 0;
    KernelOps ops = {{}, slot, 0, failure <= 3 ? failure : 0, failure == 4};
    callbackFails = failure == 5;
    const char *error = nabuStartStorageOnce(state, slot, ops);
    assert((error != nullptr) == (failure != 0));
    assert(state == (failure ? 3U : 2U));
    assert(callbacks == (!failure || failure == 5 ? 1U : 0U));
    auto before = ops.events;
    assert(nabuStartStorageOnce(state, slot, ops));
    assert(ops.events == before); // No repeated load, callback or retry.
    if (!failure) assert((ops.events == std::vector<std::string>{"VFS_READY",
      "com.apple.iokit.IOStorageFamily", "FAMILY_READY", "com.apple.filesystems.apfs",
      "APFS_LOADED", "org.blu-sharky.NativeUfs"}));
  }
  for (unsigned failure = 0; failure <= 4; ++failure) {
    unsigned state = 0; ModuleOps ops; ops.fail = failure;
    const char *error = nabuStartStorageModuleOnce(state, ops);
    assert((error != nullptr) == (failure != 0));
    assert(ops.providersReleased == 1);
    if (!failure) assert((ops.events == std::vector<std::string>{"ABI", "providers", "UFS", "UFS_STARTED", "Data", "DATA_STARTED"}));
    if (failure == 3) assert(ops.events.back() == "UFS"); // Never attempt Data.
    if (failure == 4) assert(ops.events[3] == "UFS_STARTED"); // Keep already-started UFS.
    auto before = ops.events;
    assert(nabuStartStorageModuleOnce(state, ops));
    assert(ops.events == before && ops.providersReleased == 1);
  }
  for (unsigned failure = 0; failure <= 4; ++failure) {
    DeviceOps ops; ops.fail = failure; Device provider, *live = nullptr;
    const char *error = nabuStartStorageDevice(ops, "device", &provider, live);
    assert((error != nullptr) == (failure != 0));
    if (!failure) assert(live == &ops.object && live->started && !live->released);
    else assert(!live && ops.object.released == (failure > 1));
    assert(ops.detached == (failure == 4 ? 1U : 0U));
  }
}
