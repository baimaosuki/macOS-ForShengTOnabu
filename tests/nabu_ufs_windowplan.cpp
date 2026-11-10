// SPDX-License-Identifier: BSD-2-Clause-Patent
// Host test for the nabu root window gate. Expectations are literals.
#include <cstdint>
#include <cstdio>
#include <cstring>

#include "NabuUfsWindowPlan.h"

static int failures = 0;
static int cases = 0;

#define CHECK(cond) do {                                                   \
    ++cases;                                                               \
    if (!(cond)) {                                                         \
      ++failures;                                                          \
      std::printf("  line %d: %s\n", __LINE__, #cond);                     \
    }                                                                      \
  } while (0)

static NabuRootWindowSpec OS{ "OS", 0x1000, 0x40000 };
static NabuRootWindowSpec SYS{ "SystemOS", 0x41000, 0x40000 };
static NabuRootWindowSpec APP{ "App", 0x81000, 0x40000 };
static const uint64_t kImageBytes = 0xC1000;

static const char *check(NabuRootWindowSpec *spec, unsigned count, NabuRootWindowGate &gate,
                         uint64_t imageBytes = kImageBytes, uint64_t blockBytes = 4096)
{
  return nabuUfsRootWindowsCheck(spec, count, imageBytes, blockBytes, &gate);
}

static void test_complete_set_is_accepted()
{
  NabuRootWindowSpec spec[3] = { OS, SYS, APP };
  NabuRootWindowGate gate{};
  CHECK(check(spec, 3, gate) == nullptr);
  CHECK(gate.found == 3);
  CHECK(gate.order[0] == 0 && gate.order[1] == 1 && gate.order[2] == 2);
  CHECK(gate.firstLba[0] == 1);          // 0x1000 / 4096
  CHECK(gate.blocks[0] == 0x40);         // 0x40000 / 4096
  CHECK(gate.headSkip[0] == 0);
  CHECK(gate.firstLba[2] == 0x81);       // 0x81000 / 4096
}

static void test_order_does_not_matter()
{
  NabuRootWindowSpec spec[3] = { APP, OS, SYS };
  NabuRootWindowGate gate{};
  CHECK(check(spec, 3, gate) == nullptr);
  CHECK(gate.order[0] == 1 && gate.order[1] == 2 && gate.order[2] == 0);
  CHECK(gate.firstLba[0] == 1);
}

static void test_missing_app_is_refused()
{
  NabuRootWindowSpec spec[2] = { OS, SYS };
  NabuRootWindowGate gate{};
  const char *why = check(spec, 2, gate);
  CHECK(why != nullptr);
  CHECK(why && std::strstr(why, "lacks a required window"));
}

static void test_unexpected_window_is_refused()
{
  NabuRootWindowSpec spec[4] = { OS, SYS, APP, { "Recovery", 0xC1000, 0x1000 } };
  NabuRootWindowGate gate{};
  const char *why = check(spec, 4, gate, kImageBytes + 0x1000);
  CHECK(why != nullptr);
  CHECK(why && std::strstr(why, "does not expect"));
}

static void test_duplicate_name_is_refused()
{
  NabuRootWindowSpec spec[3] = { OS, SYS, { "OS", 0x81000, 0x40000 } };
  NabuRootWindowGate gate{};
  const char *why = check(spec, 3, gate);
  CHECK(why != nullptr);
  CHECK(why && std::strstr(why, "share a name"));
}

static void test_unaligned_offset_is_refused()
{
  NabuRootWindowSpec spec[3] = { OS, SYS, { "App", 0x81001, 0x40000 } };
  NabuRootWindowGate gate{};
  const char *why = check(spec, 3, gate, kImageBytes + 0x1000);
  CHECK(why != nullptr);
  CHECK(why && std::strstr(why, "4096-byte aligned"));
}

static void test_partial_sector_size_is_refused()
{
  NabuRootWindowSpec spec[3] = { OS, SYS, { "App", 0x81000, 0x40100 } };
  NabuRootWindowGate gate{};
  const char *why = check(spec, 3, gate, kImageBytes + 0x1000);
  CHECK(why != nullptr);
  CHECK(why && std::strstr(why, "512-byte sectors"));
}

static void test_overlap_is_refused()
{
  NabuRootWindowSpec spec[3] = { OS, SYS, { "App", 0x40000, 0x40000 } };
  NabuRootWindowGate gate{};
  const char *why = check(spec, 3, gate);
  CHECK(why != nullptr);
  CHECK(why && std::strstr(why, "overlap"));
}

static void test_past_image_is_refused()
{
  NabuRootWindowSpec spec[3] = { OS, SYS, APP };
  NabuRootWindowGate gate{};
  const char *why = check(spec, 3, gate, 0xC0000);
  CHECK(why != nullptr);
  CHECK(why && std::strstr(why, "ends past the image"));
}

static void test_empty_and_oversized_sets_are_refused()
{
  NabuRootWindowSpec spec[5] = { OS, SYS, APP, OS, SYS };
  NabuRootWindowGate gate{};
  CHECK(check(spec, 0, gate) != nullptr);
  CHECK(check(spec, 5, gate) != nullptr);
}

static void test_block_size_must_be_4096()
{
  NabuRootWindowSpec spec[3] = { OS, SYS, APP };
  NabuRootWindowGate gate{};
  CHECK(check(spec, 3, gate, kImageBytes, 512) != nullptr);
}

static void test_sub_block_window_is_refused()
{
  // The manifest keeps windows 4096-aligned, so a sub-block window is a contract
  // violation rather than something the reader silently expands.
  NabuRootWindowSpec spec[3] = { OS, SYS, { "App", 0x200, 0x200 } };
  NabuRootWindowGate gate{};
  const char *why = check(spec, 3, gate);
  CHECK(why != nullptr);
  CHECK(why && std::strstr(why, "4096-byte aligned"));
}

static void test_head_skip_is_zero_for_aligned_windows()
{
  NabuRootWindowSpec spec[3] = { OS, SYS, APP };
  NabuRootWindowGate gate{};
  CHECK(check(spec, 3, gate) == nullptr);
  for (unsigned i = 0; i < 3; ++i) CHECK(gate.headSkip[i] == 0);
  CHECK(gate.blocks[1] == 0x40);
  CHECK(gate.firstLba[1] == 0x41);
}

int main()
{
  test_complete_set_is_accepted();
  test_order_does_not_matter();
  test_missing_app_is_refused();
  test_unexpected_window_is_refused();
  test_duplicate_name_is_refused();
  test_unaligned_offset_is_refused();
  test_partial_sector_size_is_refused();
  test_overlap_is_refused();
  test_past_image_is_refused();
  test_empty_and_oversized_sets_are_refused();
  test_block_size_must_be_4096();
  test_sub_block_window_is_refused();
  test_head_skip_is_zero_for_aligned_windows();
  std::printf("%d/%d root-window gate checks passed\n", cases - failures, cases);
  return failures ? 1 : 0;
}
