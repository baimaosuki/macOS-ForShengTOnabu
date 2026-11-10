// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once

using NabuStorageStart = const char *(*)();
enum class NabuStorageProviders { Ready, Retry, Ambiguous };

// Registry activity during boot may invalidate a scan or delay provider
// arrival. Retry bounded snapshots, dropping every failed snapshot's retains.
// This waits for providers only, never IOBSD or derived APFS volume media.
template <class Ops>
static const char *nabuFindStorageProviders(Ops &ops)
{
  for (unsigned elapsed = 0; elapsed <= 5000; elapsed += 100) {
    NabuStorageProviders result = ops.scanProviders();
    if (result == NabuStorageProviders::Ready) return nullptr;
    ops.releaseProviders();
    if (result == NabuStorageProviders::Ambiguous) return "storage providers ambiguous";
    if (elapsed == 5000) break;
    ops.pause(100);
  }
  return "storage provider discovery timed out (missing or unstable registry)";
}

static inline bool nabuRegisterStorageStart(NabuStorageStart &slot, NabuStorageStart callback)
{
  if (!callback) return false;
  NabuStorageStart empty = nullptr;
  if (__atomic_compare_exchange_n(&slot, &empty, callback, false,
                                  __ATOMIC_RELEASE, __ATOMIC_ACQUIRE)) return true;
  return empty == callback; // Immutable bundle may register the same callback.
}

// Called only after vfsinit. Loading must return before entering the module's
// callback: starting storage inside OSKext's module/class initialization locks
// would change both its lock order and the normal metaclass lifecycle.
template <class Ops>
static const char *nabuStartStorageOnce(unsigned &state, NabuStorageStart &slot, Ops &ops)
{
  unsigned idle = 0;
  if (!__atomic_compare_exchange_n(&state, &idle, 1U, false,
                                   __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE))
    return "storage startup already attempted";
  ops.stage("VFS_READY");
  const char *failure = nullptr;
  if (!ops.load("com.apple.iokit.IOStorageFamily")) failure = "IOStorageFamily load failed";
  if (!failure) {
    ops.stage("FAMILY_READY");
    if (!ops.load("com.apple.filesystems.apfs")) failure = "APFS load failed";
  }
  if (!failure) {
    ops.stage("APFS_LOADED");
    if (!ops.load("org.blu-sharky.NativeUfs")) failure = "NativeUfs module load failed";
  }
  if (!failure) {
    NabuStorageStart callback = __atomic_load_n(&slot, __ATOMIC_ACQUIRE);
    failure = callback ? callback() : "NativeUfs startup callback missing";
  }
  __atomic_store_n(&state, failure ? 3U : 2U, __ATOMIC_RELEASE);
  return failure;
}

// Keep the allocation's owner reference after start succeeds. Asynchronous
// media matching and the UFS publisher can already hold/use this object.
template <class Ops, class Device, class Provider>
static const char *nabuStartStorageDevice(Ops &ops, const char *name, Provider *provider, Device *&live)
{
  Device *device = ops.allocate(name);
  if (!device) return "allocation";
  if (!ops.init(device)) { ops.release(device); return "init"; }
  if (!ops.attach(device, provider)) { ops.release(device); return "attach"; }
  if (!ops.start(device, provider)) {
    ops.detach(device, provider);
    ops.release(device);
    return "start";
  }
  live = device;
  return nullptr;
}

template <class Ops>
static const char *nabuStartStorageModuleOnce(unsigned &state, Ops &ops)
{
  unsigned idle = 0;
  if (!__atomic_compare_exchange_n(&state, &idle, 1U, false,
                                   __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE))
    return "storage module startup already attempted";
  const char *failure = ops.checkAbi();
  if (!failure) failure = ops.findProviders();
  if (!failure) failure = ops.startUfs();
  if (!failure) { ops.stage("UFS_STARTED"); failure = ops.startData(); }
  if (!failure) ops.stage("DATA_STARTED");
  // Providers are retained during discovery; started devices retain their own
  // owner references and are deliberately not rolled back on a later failure.
  ops.releaseProviders();
  __atomic_store_n(&state, failure ? 3U : 2U, __ATOMIC_RELEASE);
  return failure;
}
