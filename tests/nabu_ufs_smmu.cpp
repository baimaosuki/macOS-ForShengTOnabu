// SPDX-License-Identifier: BSD-2-Clause-Patent
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <utility>
#include <vector>
#include "SmmuTakeover.h" // The test runner supplies the actual staged production header.

int main() {
  std::array<uint32_t, 0x100000 / 4> registers{};
  std::vector<std::pair<unsigned, uint32_t>> writes;
  auto read = [&](unsigned at) { return registers.at(at / 4); };
  auto write = [&](unsigned at, uint32_t value) { writes.emplace_back(at, value); registers.at(at / 4) = value; };
  auto reset = [&] {
    registers.fill(0); writes.clear();
    registers[0x20/4] = 0x40000080; // 128 stream matches, stage 1.
    registers[0x24/4] = 0x60000046; // 128 global pages, 70 banks.
    registers[0x28/4] = 0x5111; // AArch64 and 36-bit widths.
    // Photo: slot 2, SMR=80000300, S2CR=1. Firmware UFS uses low CB1.
    registers[(0x800 + 4 * 2)/4] = 0x80000300;
    registers[(0xc00 + 4 * 2)/4] = 1;
    registers[(0x1000 + 4)/4] = 0x5a010012; // S1 with S2 bypass; preserve VMID/attributes.
    registers[(0x1800 + 4)/4] = 0x10001; // VA64 and firmware ASID state.
    registers[(128 + 1)*4096/4] = 0x64; // Other SCTLR flags; translation M=0.
  };
  auto plan = [&] { return planUfsSmmu(0x300, 0x8cb5e9000ULL, 0x22000, read, 0x100000); };
  auto refused_without_writes = [&] {
    auto p = plan(); assert(p.error);
    assert(configureUfsSmmu(p, 0x300, read, write)); assert(writes.empty());
  };
  reset(); auto original = registers; auto p = plan();
  assert(!p.error && p.reuse && p.slot == 2 && p.bank == 1 && p.bankFromTop == 68);
  assert(!configureUfsSmmu(p, 0x300, read, write));
  assert(writes.empty() && registers == original); // Do not disable/retarget the firmware context.

  reset(); registers[(128 + 1)*4096/4] |= 1; refused_without_writes(); // Live translation.
  reset(); registers[(0x800 + 4 * 3)/4] = 0x80000301;
  registers[(0xc00 + 4 * 3)/4] = 1; refused_without_writes(); // Shared CB1.
  reset(); registers[(0x800 + 4 * 3)/4] = 0x80000300;
  registers[(0xc00 + 4 * 3)/4] = 1; refused_without_writes(); // Duplicate exact SID.
  reset(); registers[(0x800 + 4 * 2)/4] = 0x80010300; refused_without_writes(); // Wildcard.
  reset(); registers[(0x1800 + 4)/4] &= ~1U; refused_without_writes(); // No VA64.
  reset(); registers[(0x1000 + 4)/4] &= ~(3U << 16); refused_without_writes(); // Wrong bypass type.
  reset(); registers[(0xc00 + 4 * 2)/4] = 0; refused_without_writes(); // Reserved bank zero.
  reset(); registers[0x20/4] = 0x40000081; refused_without_writes(); // Never truncate geometry.

  reset(); p = plan(); registers[(128 + 1)*4096/4] ^= 0x20;
  assert(configureUfsSmmu(p, 0x300, read, write) && writes.empty()); // Plan/configure drift.
  reset(); p = plan(); registers[(0x800 + 4 * 3)/4] = 0x80000301;
  registers[(0xc00 + 4 * 3)/4] = 1;
  assert(configureUfsSmmu(p, 0x300, read, write) && writes.empty()); // New shared owner.
  reset(); p = plan(); assert(configureUfsSmmu(p, 0x301, read, write) && writes.empty());

  // Existing unmapped allocation path remains bounded and ordered.
  reset(); registers[(0x800 + 4 * 2)/4] = 0; p = plan();
  assert(!p.error && !p.reuse && p.bank == 69);
  assert(!configureUfsSmmu(p, 0x300, read, write) && writes.size() == 5);
  assert(writes.back().first == 0x800 + 4 * p.slot && writes.back().second == 0x80000300);
  puts("PASS: photo SID300/CB1 reuse without writes; M=1/shared/wildcard/VA64/drift denied; new allocation ordered");
}
