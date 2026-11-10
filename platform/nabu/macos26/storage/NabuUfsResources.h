// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once
#include "NabuBoard.h"

struct NabuUfsResources {
  uint64_t hc[2], gcc[2], smmu[2];
  uint32_t stream, blockBytes;
};

static bool nabuUfsResourcesMatch(const NabuUfsResources &r)
{
  const auto &b = nabu_ufs::kBoard;
  return r.hc[0] == b.hcBase && r.hc[1] == b.hcBytes &&
         r.gcc[0] == b.gccBase + 0x77000 && r.gcc[1] == 0x1000 &&
         r.smmu[0] == b.smmuBase && r.smmu[1] == b.smmuBytes &&
         r.stream == b.streamId && r.blockBytes == b.logicalBlockBytes;
}

static bool nabuUfsClocksReady(uint32_t gdsc, uint32_t axi, uint32_t ahb)
{
  return gdsc != UINT32_MAX && axi != UINT32_MAX && ahb != UINT32_MAX &&
         (gdsc & 0x80000000U) && !(axi & 0x80000000U) && !(ahb & 0x80000000U);
}

// A read request expressed in bytes, resolved once into the device's own units.
//
// The controller moves whole 4096-byte blocks (NabuBoard.h logicalBlockBytes), a
// GPT or an APFS container is addressed in 512-byte sectors, and a caller may ask
// for any byte range inside a window.  So a request is never a block number plus
// a guessed unit: it carries the byte offset and length, and this resolves the
// aligned device read that covers it plus the head and tail bytes that fall
// outside the request and must not be handed back to the caller.
//
//   device bytes [firstLba * blockBytes, (firstLba + blocks) * blockBytes)
//   request bytes [offsetBytes, offsetBytes + lengthBytes)  (headSkip .. headSkip+lengthBytes)
//
// Refusals are explicit: a zero length, an offset that is not a whole 512-byte
// sector, a range past the medium, an arithmetic overflow, or a covering read
// larger than one request may carry.
struct NabuUfsWindow {
  uint64_t offsetBytes;   // requested start, inside the medium
  uint64_t lengthBytes;   // requested length, never zero
  uint64_t blockBytes;    // device logical block, 4096 on this board
  uint64_t firstLba;      // aligned start, in device blocks
  uint64_t blocks;        // covering blocks, >= 1
  uint64_t headSkip;      // bytes inside the first block before the request
  uint64_t tailSlack;     // bytes inside the last block after the request
};

static bool nabuUfsWindowFromBytes(uint64_t offsetBytes, uint64_t lengthBytes,
                                   uint64_t capacityBytes, uint64_t maxBlocks,
                                   NabuUfsWindow &out)
{
  const uint64_t blockBytes = nabu_ufs::kBoard.logicalBlockBytes;
  const uint64_t sectorBytes = 512;
  if (blockBytes != 4096 || maxBlocks == 0) return false;
  if (lengthBytes == 0) return false;
  if (sectorBytes == 0 || (offsetBytes % sectorBytes) != 0) return false;
  if (offsetBytes > capacityBytes) return false;
  if (lengthBytes > capacityBytes - offsetBytes) return false;
  if (offsetBytes > UINT64_MAX - lengthBytes) return false;

  const uint64_t firstLba = offsetBytes / blockBytes;
  const uint64_t headSkip = offsetBytes % blockBytes;
  const uint64_t span = headSkip + lengthBytes;
  if (span < lengthBytes) return false;
  const uint64_t blocks = (span + blockBytes - 1) / blockBytes;
  if (blocks == 0 || blocks > maxBlocks) return false;
  const uint64_t covered = blocks * blockBytes;
  if (covered < span) return false;
  out.offsetBytes = offsetBytes;
  out.lengthBytes = lengthBytes;
  out.blockBytes = blockBytes;
  out.firstLba = firstLba;
  out.blocks = blocks;
  out.headSkip = headSkip;
  out.tailSlack = covered - span;
  return true;
}
