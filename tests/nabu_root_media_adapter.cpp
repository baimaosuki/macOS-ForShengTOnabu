// SPDX-License-Identifier: BSD-2-Clause-Patent
// Compile the production adapter extracted from the staged source against a
// small property/registry API fixture, so property type and UUID checks are
// exercised in addition to the allocation-free scan policy.
#include <cassert>
#include <cstdint>
#include <map>
#include <string>
#include <vector>
#include "NabuRootMedia.h"

using IOOptionBits = uint32_t;
constexpr unsigned kIOServiceRegisteredState = 2, kIOServiceInactiveState = 1;
constexpr const char *kIOBSDNameKey = "BSD Name";
constexpr const char *kIOBSDMajorKey = "BSD Major";
constexpr const char *kIOBSDMinorKey = "BSD Minor";
struct OSObject { virtual ~OSObject () = default; void release () {} };
struct OSString : OSObject {
  std::string text;
  explicit OSString (const char *value) : text (value) {}
  bool isEqualTo (const char *value) { return text == value; }
  unsigned getLength () { return text.size (); }
};
struct OSNumber : OSObject {};
OSObject booleanFalse, booleanTrue;
OSObject *kOSBooleanFalse = &booleanFalse;
struct IORegistryEntry : OSObject {
  std::map<std::string, OSObject *> properties;
  OSObject *copyProperty (const char *key) {
    auto it = properties.find (key);
    return it == properties.end () ? nullptr : it->second;
  }
};
struct IOService : IORegistryEntry {
  bool media = true, volume = true;
  unsigned state = kIOServiceRegisteredState;
  void *metaCast (const char *name) {
    return ((std::string (name) == "IOMedia" && media) ||
      (std::string (name) == "AppleAPFSVolume" && volume)) ? this : nullptr;
  }
  IOOptionBits getState () { return state; }
};
struct IORegistryIterator {
  std::vector<IORegistryEntry *> entries;
  unsigned at = 0;
  bool stable = true;
  IORegistryEntry *getNextObject () { return at < entries.size () ? entries[at++] : nullptr; }
  bool isValid () { return stable; }
};
#define OSDynamicCast(type, value) dynamic_cast<type *> (value)
#define OSSafeReleaseNULL(value) do { if (value) (value)->release (); (value) = nullptr; } while (0)
#include "NabuRootMediaAdapterForTest.inc"

int main ()
{
  OSString wanted ("signed-volume"), other ("container-id"), bsd ("disk1s1"), empty ("");
  OSNumber major, minor;
  IOService service;
  service.properties = {{"UUID", &wanted}, {"Whole", kOSBooleanFalse},
    {kIOBSDNameKey, &bsd}, {kIOBSDMajorKey, &major}, {kIOBSDMinorKey, &minor}};
  auto ready = [&] {
    IORegistryIterator iterator = {{&service}};
    NabuRootMediaRegistry registry = {&iterator, "signed-volume"};
    return nabuScanRootMedia (registry).ready ();
  };
  assert (ready ()); // Registered, without MatchedState.
  service.properties.erase ("UUID"); assert (!ready ());
  service.properties["UUID"] = &other; assert (!ready ());
  service.properties["UUID"] = &major; assert (!ready ());
  service.properties["UUID"] = &wanted;
  service.properties["Whole"] = &booleanTrue; assert (!ready ());
  service.properties.erase ("Whole"); assert (!ready ());
  service.properties["Whole"] = &minor; assert (!ready ());
  service.properties["Whole"] = kOSBooleanFalse;
  service.volume = false; assert (!ready ()); // Raw IOMedia/container.
  service.volume = true;
  service.properties[kIOBSDNameKey] = &empty; assert (!ready ());
  service.properties[kIOBSDNameKey] = &bsd;
  service.properties[kIOBSDMajorKey] = &other; assert (!ready ());
  service.properties[kIOBSDMajorKey] = &major;
  service.properties.erase (kIOBSDMinorKey); assert (!ready ());
  service.properties[kIOBSDMinorKey] = &minor;
  service.state = 0; assert (!ready ());
  service.state = kIOServiceRegisteredState | kIOServiceInactiveState; assert (!ready ());
  service.state = kIOServiceRegisteredState;
  service.media = false; assert (!ready ());
  // No fixture operation can assign the wanted UUID to a missing identity.
  assert (service.properties["UUID"] == &wanted);
}
