// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once

// Read-only publication gate. The adapter walks only the verified window's
// IOService subtree and snapshots existing properties; it never supplies an
// identity to a media. Matching completion is deliberately not a prerequisite.
struct NabuRootMediaView {
  bool media = false, inWindow = false, registered = false, inactive = false;
  bool whole = true, volume = false, uuidMatches = false;
  bool bsdName = false, bsdMajor = false, bsdMinor = false;
};

struct NabuRootMediaScan {
  unsigned examined = 0, identities = 0, volumes = 0;
  bool complete = false, valid = false;
  bool ready () const { return complete && valid && identities == 1 && volumes == 1; }
};

// next() returns a subtree entry or nullptr at exhaustion; view() returns a
// scalar snapshot for the requested UUID. valid() must detect registry changes.
// Consume the whole bounded traversal: an early success could hide a duplicate
// UUID, and an invalid or truncated traversal must never establish readiness.
template <class Registry>
static NabuRootMediaScan nabuScanRootMedia (Registry &registry, unsigned limit = 256)
{
  NabuRootMediaScan result;
  for (;;) {
    auto *entry = registry.next ();
    if (!entry) { result.complete = true; break; }
    if (result.examined == limit) break;
    ++result.examined;
    const NabuRootMediaView view = registry.view (entry);
    if (!view.media || !view.inWindow || !view.uuidMatches) continue;
    ++result.identities;
    if (view.registered && !view.inactive && !view.whole && view.volume &&
        view.bsdName && view.bsdMajor && view.bsdMinor) ++result.volumes;
  }
  result.valid = registry.valid ();
  return result;
}
