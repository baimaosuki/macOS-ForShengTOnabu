// SPDX-License-Identifier: BSD-2-Clause-Patent
#include <cassert>
#include <cstdio>
#include <vector>
#include "NabuRootMedia.h"

struct Node { NabuRootMediaView view; };
struct Registry {
  std::vector<Node> nodes;
  unsigned at = 0;
  bool stable = true;
  Node *next () { return at < nodes.size () ? &nodes[at++] : nullptr; }
  NabuRootMediaView view (Node *node) { return node->view; }
  bool valid () { return stable; }
};

static NabuRootMediaView volume ()
{
  NabuRootMediaView v;
  v.media = v.inWindow = v.registered = v.volume = v.uuidMatches = true;
  v.whole = false;
  v.bsdName = v.bsdMajor = v.bsdMinor = true;
  return v;
}

int main ()
{
  auto good = volume ();
  // There is intentionally no MatchedState input: an attached, registered
  // APFS volume can remain busy in matching after publishing its BSD identity.
  Registry real = {{{good}}};
  assert (nabuScanRootMedia (real).ready ());
  for (unsigned field = 0; field < 11; ++field) {
    auto bad = good;
    switch (field) {
      case 0: bad.media = false; break;
      case 1: bad.inWindow = false; break;
      case 2: bad.registered = false; break;
      case 3: bad.inactive = true; break;
      case 4: bad.whole = true; break;
      case 5: bad.volume = false; break;
      case 6: bad.uuidMatches = false; break; // absent or wrong native UUID
      case 7: bad.bsdName = false; break;
      case 8: bad.bsdMajor = false; break;
      case 9: bad.bsdMinor = false; break;
      case 10: bad.whole = true; bad.volume = false; break; // raw container
    }
    Registry rejected = {{{bad}}};
    assert (!nabuScanRootMedia (rejected).ready ());
  }
  Registry duplicate = {{{good}, {good}}};
  auto d = nabuScanRootMedia (duplicate);
  assert (!d.ready () && d.identities == 2);
  auto fake = good; fake.whole = true; fake.volume = false;
  Registry rawAndVolume = {{{fake}, {good}}};
  assert (!nabuScanRootMedia (rawAndVolume).ready ());
  auto otherWindow = good; otherWindow.inWindow = false;
  Registry other = {{{otherWindow}, {good}}};
  assert (nabuScanRootMedia (other).ready ());
  Registry changed = {{{good}}, 0, false};
  assert (!nabuScanRootMedia (changed).ready ());
  Registry truncated = {{{good}, {good}}};
  auto t = nabuScanRootMedia (truncated, 1);
  assert (!t.ready () && !t.complete);
  Registry exactLimit = {{{good}}};
  assert (nabuScanRootMedia (exactLimit, 1).ready ());
  Registry zero = {{{good}}};
  assert (!nabuScanRootMedia (zero, 0).ready ());
  Registry empty;
  assert (!nabuScanRootMedia (empty).ready ());
  std::puts ("read-only unmatched volume accepted; raw, duplicate and incomplete scans rejected");
}
