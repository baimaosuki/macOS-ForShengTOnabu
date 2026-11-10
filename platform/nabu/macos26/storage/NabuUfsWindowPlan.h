// SPDX-License-Identifier: BSD-2-Clause-Patent
// nabu root window gate.
//
// The production root path accepts any manifest with an OS and a SystemOS window
// (ufsBuildRootPlan, RootPublication.h). A desktop boot also needs the App
// cryptex, and every window has to be reachable by whole 4096-byte device reads.
// This gate states that requirement once, in a form a host test can exercise,
// so a root image that only carries a recovery pair is refused with a reason
// instead of booting into a half-populated volume.
//
// Pure data in, reason string out: no IOKit, no allocation, no I/O.
#pragma once

#include <stdint.h>
#include <string.h>

struct NabuRootWindowSpec {
  const char *name;      // manifest name, NUL terminated, non-empty
  uint64_t offsetBytes;  // inside the root image
  uint64_t sizeBytes;
};

// The desktop root carries exactly these three windows.
static constexpr unsigned kNabuRootWindowCount = 3;
static const char *const kNabuRootWindowNames[kNabuRootWindowCount] = { "OS", "SystemOS", "App" };

struct NabuRootWindowGate {
  unsigned found;                                   // how many of the three are present
  unsigned order[kNabuRootWindowCount];             // index into the caller's array, per required name
  uint64_t blocks[kNabuRootWindowCount];            // covering device blocks, per required name
  uint64_t firstLba[kNabuRootWindowCount];          // relative to the root image, per required name
  uint64_t headSkip[kNabuRootWindowCount];
};

// Returns nullptr when the window set is complete and readable, else a static reason.
// blockBytes is the device's logical block (4096 on this board); imageBytes bounds
// every offset+size so a truncated image cannot pass.
static inline const char *nabuUfsRootWindowsCheck (const NabuRootWindowSpec *spec, unsigned count,
                                                   uint64_t imageBytes, uint64_t blockBytes,
                                                   NabuRootWindowGate *gate)
{
  if (!spec || !gate) return "root window gate: no input";
  if (blockBytes != 4096) return "root window gate: device block size is not 4096";
  if (!count) return "root manifest carries no windows";
  if (count > 4) return "root manifest carries more windows than the layout allows";
  for (unsigned i = 0; i < count; ++i) {
    if (!spec[i].name || !spec[i].name[0]) return "root window has no name";
    if (!spec[i].sizeBytes) return "root window has no bytes";
    if (spec[i].offsetBytes & 4095) return "root window is not 4096-byte aligned";
    if (spec[i].sizeBytes & 511) return "root window is not a whole number of 512-byte sectors";
    if (spec[i].offsetBytes > imageBytes) return "root window starts past the image";
    if (spec[i].sizeBytes > imageBytes - spec[i].offsetBytes) return "root window ends past the image";
    for (unsigned j = 0; j < i; ++j) {
      const uint64_t aStart = spec[i].offsetBytes, aEnd = aStart + spec[i].sizeBytes;
      const uint64_t bStart = spec[j].offsetBytes, bEnd = bStart + spec[j].sizeBytes;
      if (aStart < bEnd && bStart < aEnd) return "two root windows overlap";
      if (spec[i].name[0] && spec[j].name && !strcmp (spec[i].name, spec[j].name))
        return "two root windows share a name";
    }
  }
  gate->found = 0;
  for (unsigned want = 0; want < kNabuRootWindowCount; ++want) {
    unsigned match = count;
    for (unsigned i = 0; i < count; ++i) {
      if (!strcmp (spec[i].name, kNabuRootWindowNames[want])) { match = i; break; }
    }
    if (match == count) return "root manifest lacks a required window (OS, SystemOS, App)";
    const uint64_t headSkip = spec[match].offsetBytes % blockBytes;
    const uint64_t span = headSkip + spec[match].sizeBytes;
    if (span < spec[match].sizeBytes) return "root window length overflows";
    const uint64_t blocks = (span + blockBytes - 1) / blockBytes;
    if (!blocks) return "root window covers no device block";
    if (blocks * blockBytes < span) return "root window covering read overflows";
    gate->order[want] = match;
    gate->firstLba[want] = spec[match].offsetBytes / blockBytes;
    gate->blocks[want] = blocks;
    gate->headSkip[want] = headSkip;
    ++gate->found;
  }
  for (unsigned i = 0; i < count; ++i) {
    bool known = false;
    for (unsigned want = 0; want < kNabuRootWindowCount; ++want) {
      if (gate->order[want] == i) known = true;
    }
    if (!known) return "root manifest carries a window this board does not expect";
  }
  return nullptr;
}
