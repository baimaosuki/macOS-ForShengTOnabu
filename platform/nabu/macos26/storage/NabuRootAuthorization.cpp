// SPDX-License-Identifier: BSD-2-Clause-Patent
// Kernel-side owner queries, usable before any dynamically prelinked transport
// exists. Only the UFS runtime supplies the callback; absent registration denies.
#include <IOKit/IOService.h>
#include <IOKit/IOBSD.h>
#include <libkern/c++/OSData.h>
#include <libkern/c++/OSDictionary.h>
#include <libkern/c++/OSIterator.h>
#include <libkern/c++/OSNumber.h>
#include <libkern/c++/OSString.h>
#include <libkern/crypto/sha2.h>
#include <libkern/libkern.h>
#include <sys/param.h>

using NabuRootAuthenticator = int (*)(IOService *, const char *, uint8_t *);
static NabuRootAuthenticator gAuthenticator;
using NabuRamSeed = int (*)(const char *, dev_t *, char *, size_t, uint64_t *, uint64_t *, uint8_t *);
static NabuRamSeed gRamSeed;

extern "C" int nabu_register_ram_seed(NabuRamSeed callback)
{
  if (!callback) return 0;
  NabuRamSeed empty = nullptr;
  if (__atomic_compare_exchange_n(&gRamSeed, &empty, callback, false,
                                  __ATOMIC_RELEASE, __ATOMIC_ACQUIRE)) return 1;
  return empty == callback;
}

extern "C" int native_ram_block_volume_seed(const char *uuid, dev_t *device, char *path,
    size_t size, uint64_t *base, uint64_t *bytes, uint8_t *digest)
{
  if (!uuid || strcmp(uuid, "0A5EED00-0000-4000-8000-000000000001") ||
      !device || !path || !base || !bytes || !digest) return 0;
  auto callback = __atomic_load_n(&gRamSeed, __ATOMIC_ACQUIRE);
  return callback ? callback(uuid, device, path, size, base, bytes, digest) : 0;
}

extern "C" int nabu_register_root_authenticator(NabuRootAuthenticator callback)
{
  if (!callback) return 0;
  NabuRootAuthenticator empty = nullptr;
  if (__atomic_compare_exchange_n(&gAuthenticator, &empty, callback, false,
                                  __ATOMIC_RELEASE, __ATOMIC_ACQUIRE)) return 1;
  return empty == callback; // The immutable module cannot unload or replace it.
}

static bool nabuAuthorizeMedia(IOService *media, const char *expected, uint8_t *digest)
{
  auto callback = __atomic_load_n(&gAuthenticator, __ATOMIC_ACQUIRE);
  if (!callback || !media) return false;
  OSString *uuid = OSDynamicCast(OSString, media->getProperty("UUID"));
  if (!uuid || uuid->getLength() != 36 || (expected && strcmp(expected, uuid->getCStringNoCopy())))
    return false;
  unsigned depth = 0;
  for (IOService *provider = media; provider && depth < 64; provider = provider->getProvider(), ++depth) {
    int result = callback(provider, uuid->getCStringNoCopy(), digest);
    if (result >= 0) return result == 1;
  }
  return false;
}

static int nabuQueryDevice(dev_t device, const char *expected, uint8_t *digest)
{
  if (digest) bzero(digest, SHA384_DIGEST_LENGTH);
  OSDictionary *matching = IOService::serviceMatching("IOMedia");
  OSDictionary *properties = OSDictionary::withCapacity(2);
  OSNumber *mjr = OSNumber::withNumber(static_cast<unsigned>(major(static_cast<uint32_t>(device))), 32);
  OSNumber *mnr = OSNumber::withNumber(static_cast<unsigned>(minor(static_cast<uint32_t>(device))), 32);
  bool ready = matching && properties && mjr && mnr && properties->setObject(kIOBSDMajorKey, mjr) &&
               properties->setObject(kIOBSDMinorKey, mnr) && matching->setObject("IOPropertyMatch", properties);
  OSSafeReleaseNULL(mnr); OSSafeReleaseNULL(mjr); OSSafeReleaseNULL(properties);
  OSIterator *iterator = ready ? IOService::getMatchingServices(matching) : nullptr;
  OSSafeReleaseNULL(matching);
  if (!iterator) return 0;
  bool authorized = false;
  unsigned count = 0;
  while (OSObject *object = iterator->getNextObject()) {
    IOService *media = OSDynamicCast(IOService, object);
    if (!media) continue;
    if (++count != 1) { authorized = false; break; }
    authorized = nabuAuthorizeMedia(media, expected, digest);
  }
  bool valid = iterator->isValid();
  iterator->release();
  if (!valid || count != 1 || !authorized) {
    if (digest) bzero(digest, SHA384_DIGEST_LENGTH);
    return 0;
  }
  return 1;
}

extern "C" int native_verified_block_root_authorized(dev_t device)
{
  return nabuQueryDevice(device, nullptr, nullptr);
}

extern "C" int native_verified_block_manifest_sha384(dev_t device, const char *uuid, uint8_t digest[48])
{
  return uuid && strlen(uuid) == 36 && digest ? nabuQueryDevice(device, uuid, digest) : 0;
}

extern "C" int native_verified_block_volume_device(const char *uuid, dev_t *device, char *path, size_t size)
{
  if (!uuid || strlen(uuid) != 36 || !device || !path || size < 6) return 0;
  *device = NODEV;
  path[0] = 0;
  OSDictionary *matching = IOService::serviceMatching("IOMedia");
  OSDictionary *properties = OSDictionary::withCapacity(1);
  OSString *identity = OSString::withCString(uuid);
  bool ready = matching && properties && identity && properties->setObject("UUID", identity) &&
               matching->setObject("IOPropertyMatch", properties);
  OSSafeReleaseNULL(identity); OSSafeReleaseNULL(properties);
  OSIterator *iterator = ready ? IOService::getMatchingServices(matching) : nullptr;
  OSSafeReleaseNULL(matching);
  if (!iterator) return 0;
  unsigned count = 0;
  dev_t candidate = NODEV;
  char candidatePath[64] = {};
  while (OSObject *object = iterator->getNextObject()) {
    IOService *media = OSDynamicCast(IOService, object);
    if (!media) continue;
    if (++count != 1) { candidate = NODEV; break; }
    OSString *name = OSDynamicCast(OSString, media->getProperty(kIOBSDNameKey));
    OSNumber *mjr = OSDynamicCast(OSNumber, media->getProperty(kIOBSDMajorKey));
    OSNumber *mnr = OSDynamicCast(OSNumber, media->getProperty(kIOBSDMinorKey));
    if (!name || !mjr || !mnr || !name->getLength() || name->getLength() >= sizeof(candidatePath) - 6 ||
        name->getLength() >= size - 6 || mjr->unsigned64BitValue() > 255 || mnr->unsigned64BitValue() > 0xffffff ||
        !nabuAuthorizeMedia(media, uuid, nullptr)) continue;
    candidate = static_cast<dev_t>(makedev(mjr->unsigned32BitValue(), mnr->unsigned32BitValue()));
    snprintf(candidatePath, sizeof(candidatePath), "/dev/%s", name->getCStringNoCopy());
  }
  bool valid = iterator->isValid();
  iterator->release();
  if (!valid || count != 1 || candidate == NODEV) return 0;
  *device = candidate;
  strlcpy(path, candidatePath, size);
  return 1;
}
