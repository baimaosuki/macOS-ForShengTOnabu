// SPDX-License-Identifier: BSD-2-Clause-Patent
#include <cassert>
#include <cstdio>
#include "NabuUfsResources.h"
int main()
{
  // Expected branch acceptance for low enable/HWCG bits under both idle states.
  const bool on[] = {false, true, true, true};
  const bool off[] = {false, false, true, true};
  unsigned cases = 0;
  for (unsigned bits = 0; bits < 4; ++bits) {
    assert(nabuUfsBranchReady(bits) == on[bits]);
    assert(nabuUfsBranchReady(0x80000000U | bits) == off[bits]);
    cases += 2;
  }
  assert(!nabuUfsBranchReady(UINT32_MAX));
  assert(!nabuUfsClocksReady(0, 1, 1));
  assert(!nabuUfsClocksReady(UINT32_MAX, 1, 1));
  assert(!nabuUfsClocksReady(0x80000000U, 0, 1));
  assert(!nabuUfsClocksReady(0x80000000U, 1, 0));
  assert(nabuUfsClocksReady(0x80000000U, 1, 0x80000003U));
  assert(nabuUfsClocksReady(0x80000000U, 0x80000002U, 0x80000003U));
  std::printf("%u clock combinations and power/idle refusal checks passed\n", cases);
}
