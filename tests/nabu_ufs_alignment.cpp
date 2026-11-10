// SPDX-License-Identifier: BSD-2-Clause-Patent
// Host test for the nabu read window contract. Every expectation below is a
// literal, not a recomputation of the implementation: a covering read that is
// one block short, or a headSkip that swallowed a partial block, fails here.
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>

#include "NabuUfsResources.h"

static int failures = 0;
static int cases = 0;

#define CHECK(cond) do {                                                   \
    ++cases;                                                               \
    if (!(cond)) {                                                         \
      ++failures;                                                          \
      std::printf("  line %d: %s\n", __LINE__, #cond);                     \
    }                                                                      \
  } while (0)

static bool resolve(uint64_t offset, uint64_t length, uint64_t capacity,
                    uint64_t maxBlocks, NabuUfsWindow &out)
{
  return nabuUfsWindowFromBytes(offset, length, capacity, maxBlocks, out);
}

static void test_board_block_size()
{
  CHECK(nabu_ufs::kBoard.logicalBlockBytes == 4096);
}

static void test_aligned_window_is_exact()
{
  NabuUfsWindow w{};
  CHECK(resolve(0x8000, 0x40000, 1u << 24, 1024, w));
  CHECK(w.firstLba == 8);
  CHECK(w.headSkip == 0);
  CHECK(w.blocks == 0x40);
  CHECK(w.tailSlack == 0);
  CHECK(w.blockBytes == 4096);
}

static void test_sub_block_request_reads_one_block()
{
  NabuUfsWindow w{};
  CHECK(resolve(0x200, 0x200, 1u << 24, 1024, w));
  CHECK(w.firstLba == 0);
  CHECK(w.headSkip == 0x200);
  CHECK(w.blocks == 1);
  CHECK(w.tailSlack == 0xC00);
}

static void test_cross_block_request()
{
  NabuUfsWindow w{};
  CHECK(resolve(0xE00, 0x1000, 1u << 24, 1024, w));
  CHECK(w.firstLba == 0);
  CHECK(w.headSkip == 0xE00);
  CHECK(w.blocks == 2);
  CHECK(w.tailSlack == 0x200);
}

static void test_tail_of_last_block()
{
  NabuUfsWindow w{};
  CHECK(resolve(0x1E00, 0x200, 1u << 24, 1024, w));
  CHECK(w.firstLba == 1);
  CHECK(w.headSkip == 0xE00);
  CHECK(w.blocks == 1);
  CHECK(w.tailSlack == 0);
}

static void test_refusals()
{
  NabuUfsWindow w{};
  CHECK(!resolve(0x1000, 0, 1u << 24, 1024, w));                 // zero length
  CHECK(!resolve(0x1201, 0x200, 1u << 24, 1024, w));             // not a 512 sector
  CHECK(!resolve(0x1000, 0x2000, 0x1000, 1024, w));              // past the medium
  CHECK(!resolve(0x1000, 0x2000, 0x1000, 1, w));                 // more blocks than allowed
  CHECK(!resolve(UINT64_MAX - 511, 512, UINT64_MAX, 1024, w));   // ends one byte past
  CHECK(!resolve(0, 0x2000, 1u << 24, 0, w));                    // no blocks permitted
}

static void test_reassembly_matches_the_medium()
{
  const uint64_t mediumBytes = 64 * 4096;
  std::vector<uint8_t> medium(mediumBytes);
  for (uint64_t i = 0; i < mediumBytes; ++i) medium[i] = static_cast<uint8_t>((i * 7 + 3) & 0xFF);

  const uint64_t offset = 0x1200, length = 0x800;
  NabuUfsWindow w{};
  CHECK(resolve(offset, length, mediumBytes, 1024, w));
  CHECK(w.blocks == 1);
  CHECK(w.headSkip == 0x200);

  std::vector<uint8_t> delivered(length, 0);
  std::memcpy(delivered.data(), medium.data() + w.firstLba * w.blockBytes + w.headSkip, length);
  CHECK(std::memcmp(delivered.data(), medium.data() + offset, length) == 0);

  // The same request spanning a block boundary must be stitched from two reads.
  const uint64_t offset2 = 0x0E00, length2 = 0x1400;
  NabuUfsWindow w2{};
  CHECK(resolve(offset2, length2, mediumBytes, 1024, w2));
  CHECK(w2.blocks == 3);
  std::vector<uint8_t> stitched(length2, 0);
  std::memcpy(stitched.data(),
              medium.data() + w2.firstLba * w2.blockBytes + w2.headSkip, length2);
  CHECK(std::memcmp(stitched.data(), medium.data() + offset2, length2) == 0);
}

int main()
{
  test_board_block_size();
  test_aligned_window_is_exact();
  test_sub_block_request_reads_one_block();
  test_cross_block_request();
  test_tail_of_last_block();
  test_refusals();
  test_reassembly_matches_the_medium();
  std::printf("%d/%d window-contract checks passed\n", cases - failures, cases);
  return failures ? 1 : 0;
}
