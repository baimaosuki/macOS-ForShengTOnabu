// SPDX-License-Identifier: BSD-2-Clause-Patent
// Plan before any SMMU write. Interface register facts: ARM SMMUv2.
struct UfsSmmuPlan {
  unsigned slot = 0xffffffffU, bank = 0, pages = 0;
  uint32_t smr = 0, s2cr = 0;
  uint32_t cbar = 0, sctlr = 0, cba2r = 0;
  uint32_t idr0 = 0, idr1 = 0, idr2 = 0;
  unsigned groups = 0, banks = 0, bankFromTop = 0;
  uint16_t stream = 0;
  uint64_t dma = 0, bytes = 0;
  bool reuse = false;
  const char *error = nullptr;
};
// ARM IHI 0062D.c sections 1.6 and 3.7: VA64=0 still rejects inputs above
// 32 bits with SCTLR.M=0. With VA64=1 the bypass range is bounded by all
// three hardware widths, not TCR2.PASize (which only limits translation).
static unsigned ufsSmmuAddressBits(unsigned code)
{
  switch (code) {
    case 0: return 32; case 1: return 36; case 2: return 40;
    case 3: return 42; case 4: return 44; case 5: return 48;
    default: return 0;
  }
}
// One attempt with a given context bank (counted from the top). The caller falls back to lower banks when this one is held by another stream.
template <class Read>
static UfsSmmuPlan planUfsSmmuBank(uint16_t stream, uint64_t dma, uint64_t bytes, Read read, unsigned bankFromTop)
{
  UfsSmmuPlan p;
  p.stream = stream; p.dma = dma; p.bytes = bytes; p.bankFromTop = bankFromTop;
  uint32_t idr0 = read(0x20), idr2 = read(0x28);
  p.idr0 = idr0; p.idr2 = idr2;
  unsigned count = idr0 & 255;
  if (!(idr0 & (1U << 30)) || !(idr2 & (1U << 12))) {
    p.error = "SMMU AArch64 stage 1 unsupported"; return p;
  }
  for (unsigned shift = 0; shift <= 8; shift += 4) {
    unsigned bits = ufsSmmuAddressBits((idr2 >> shift) & 15);
    if (!bits || !bytes || dma >= (1ULL << bits) || bytes > (1ULL << bits) - dma) {
      p.error = "DMA exceeds SMMU address width"; return p;
    }
  }
  uint32_t idr1 = read(0x24);
  unsigned banks = idr1 & 255;
  p.idr1 = idr1; p.groups = count; p.banks = banks;
  p.pages = 1U << (((idr1 >> 28) & 7) + 1);
  if ((idr1 >> 31) || !count || p.pages > 128 || banks < 2 || banks > 128 ||
      (p.pages + banks) * 4096 > 0xd0000) {
    p.error = "unsupported SMMU geometry"; return p;
  }
  // Never claim exclusive ownership after inspecting only part of the table.
  if (count > 128) {p.error = "unsupported SMMU stream-match geometry"; return p;}
  if (bankFromTop >= banks - 1) {p.error = "no free context bank"; return p;}
  p.bank = banks - 1 - bankFromTop;
  unsigned matches = 0;
  for (unsigned i = 0; i < count; ++i) {
    uint32_t smr = read(0x800 + 4 * i);
    uint32_t s2cr = read(0xc00 + 4 * i);
    if (!(smr & 0x80000000U)) {
      if (!p.reuse) {p.slot = i; p.smr = smr; p.s2cr = s2cr;}
      continue;
    }
    uint32_t mask = (smr >> 16) & 0x7fff;
    bool match = ((smr ^ stream) & 0xffff & ~mask) == 0;
    bool ourBank = ((s2cr >> 16) & 3) == 0 && (s2cr & 255) == p.bank;
    if (match) {
      ++matches;
      if (matches > 1 || mask || !ourBank) {
        p.slot = i; p.smr = smr; p.s2cr = s2cr;
        p.error = "conflicting UFS stream mapping"; return p;
      }
      p.reuse = true; p.slot = i; p.smr = smr; p.s2cr = s2cr;
    } else if (ourBank) {
      p.slot = i; p.smr = smr; p.s2cr = s2cr;
      p.error = "reserved context bank already used"; return p;
    }
  }
  p.sctlr = read((p.pages + p.bank) * 4096);
  p.cbar = read(0x1000 + 4 * p.bank);
  p.cba2r = read(0x1800 + 4 * p.bank);
  // Preserve firmware's fault/attribute/VMID fields. M=0 disables stage 1,
  // CBAR.TYPE=1 bypasses stage 2, and VA64 makes high physical DMA legal.
  bool bypassReuse = !(p.sctlr & 1U) && ((p.cbar >> 16) & 3U) == 1U && (p.cba2r & 1U);
  if ((p.sctlr & 1U) || (p.reuse && !bypassReuse)) {
    p.error = "reserved context bank not bypass"; return p;
  }
  if (p.slot == 0xffffffffU) p.error = "no free stream-match group";
  return p;
}

// New mappings use the bounded top-bank allocator. An existing exact UFS
// stream can live anywhere in the supported table (NABU firmware uses CB1),
// so discover its bank independently and apply the same ownership checks.
template <class Read>
static UfsSmmuPlan planUfsSmmu(uint16_t stream, uint64_t dma, uint64_t bytes, Read read)
{
  enum {kBankAttempts = 8};
  UfsSmmuPlan first = planUfsSmmuBank(stream, dma, bytes, read, 0);
  if (!first.error) return first;
  auto retryable = [](const char *e) {
    return !__builtin_strcmp(e, "reserved context bank already used") || !__builtin_strcmp(e, "reserved context bank not bypass") ||
           !__builtin_strcmp(e, "conflicting UFS stream mapping");
  };
  if (!retryable(first.error)) return first;
  for (unsigned i = 0; i < first.groups; ++i) {
    uint32_t smr = read(0x800 + 4 * i), s2cr = read(0xc00 + 4 * i);
    if (smr != (0x80000000U | stream) || ((s2cr >> 16) & 3U) != 0) continue;
    unsigned existingBank = s2cr & 255U;
    if (existingBank > 0 && existingBank < first.banks) {
      // Return its actual refusal too: do not hide an unsafe firmware context
      // behind an unrelated top-bank conflict or allocate a duplicate SID.
      return planUfsSmmuBank(stream, dma, bytes, read, first.banks - 1 - existingBank);
    }
  }
  for (unsigned attempt = 1; attempt < kBankAttempts; ++attempt) {
    UfsSmmuPlan next = planUfsSmmuBank(stream, dma, bytes, read, attempt);
    if (!next.error) return next;
    if (!retryable(next.error)) break;
  }
  return first;
}

static bool sameUfsSmmuPlan(const UfsSmmuPlan &a, const UfsSmmuPlan &b)
{
  return a.stream == b.stream && a.dma == b.dma && a.bytes == b.bytes &&
         a.idr0 == b.idr0 && a.idr1 == b.idr1 && a.idr2 == b.idr2 &&
         a.groups == b.groups && a.banks == b.banks && a.pages == b.pages &&
         a.bankFromTop == b.bankFromTop && a.bank == b.bank && a.slot == b.slot &&
         a.reuse == b.reuse && a.smr == b.smr && a.s2cr == b.s2cr &&
         a.cbar == b.cbar && a.sctlr == b.sctlr && a.cba2r == b.cba2r;
}

// Caller must quiesce both UFS queues and exclusively own SMMU programming.
// Revalidate before writes; firmware-owned bypass reuse performs no writes.
// Active translation is never disabled. MMIO writes require a barrier.
template <class Read, class Write>
static const char *configureUfsSmmu(const UfsSmmuPlan &p, uint16_t stream, Read read, Write write)
{
  if (p.error) return p.error;
  if (stream != p.stream) return "SMMU request identity mismatch";
  UfsSmmuPlan current = planUfsSmmuBank(p.stream, p.dma, p.bytes, read, p.bankFromTop);
  if (current.error || !sameUfsSmmuPlan(p, current)) return "SMMU revalidation failed";
  unsigned cb = (p.pages + p.bank) * 4096;
  if (p.reuse) {
    if ((read(cb) & 1U) || ((read(0x1000 + 4 * p.bank) >> 16) & 3U) != 1U ||
        !(read(0x1800 + 4 * p.bank) & 1U)) return "SMMU reused bypass changed";
  } else {
    write(cb, 0);
    if (read(cb) != 0) return "SMMU bypass disable readback";
    write(0x1800 + 4 * p.bank, 1);
    write(0x1000 + 4 * p.bank, 0x10000);
    if (read(0x1800 + 4 * p.bank) != 1 || read(0x1000 + 4 * p.bank) != 0x10000 || read(cb) != 0)
      return "SMMU VA64 bypass readback";
  }
  if (!p.reuse) {
    write(0xc00 + 4 * p.slot, p.bank);
    if (read(0xc00 + 4 * p.slot) != p.bank) return "SMMU stream context readback";
    write(0x800 + 4 * p.slot, 0x80000000U | stream);
  }
  if (read(0x800 + 4 * p.slot) != (0x80000000U | stream) ||
      read(0xc00 + 4 * p.slot) != (p.reuse ? p.s2cr : p.bank))
    return "SMMU stream mapping readback";
  return nullptr;
}
