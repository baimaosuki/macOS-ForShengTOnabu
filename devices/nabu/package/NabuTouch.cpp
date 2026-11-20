// SPDX-License-Identifier: BSD-2-Clause-Patent
// nabu touch: Novatek NT36523 (TDDI) on QUPv3 wrapper 2 SE1 (SE18) SPI,
// Published through the original macOS 26C5035r IOHIDDevice and native HID
// pointer stack. The SPI/firmware protocol follows the NABU iOS port.
// Protocol and registers follow
// device_bringup/out/nabu-touch-protocol-20261028-01/spec.md; this is new code.
// The proprietary firmware and its partition table come from the private ADT
// node /nabu-touch; nothing runs without the nabu-touch=1 boot argument.
#include "OriginalHidDeviceABI.h"
#include "NabuInputHardware.h"
#include "NabuClockBranch.h"
#include <IOKit/IOLib.h>
#include <IOKit/IOCatalogue.h>
#include <libkern/c++/OSNumber.h>
#include <IOKit/IOBufferMemoryDescriptor.h>
#include <IOKit/IOWorkLoop.h>
#include <IOKit/IODeviceTreeSupport.h>
#include <libkern/c++/OSData.h>
#include <libkern/c++/OSArray.h>
#include <libkern/c++/OSIterator.h>
#include <mach/kmod.h>
#include <kern/thread.h>
#include <kern/clock.h>
#include <pexpert/pexpert.h>

extern "C" {
vm_offset_t ml_io_map(vm_offset_t pa, vm_size_t size);
extern kmod_info_t g_kernel_kmod_info;
kern_return_t OSKextLoadKextWithIdentifier(const char *);
void nabu_touch_static_init(void) __asm__("__GLOBAL__sub_I_NabuTouch.cpp");
bool nabu_touch_publish(IOService *platform);
// [0] stage reached, [1] frames read, [2] frames with fingers, [3] checksum drops,
// [4] HID reports sent, [5] last handleReport status, [6] latch hits, [7] firmware downloads
volatile uint32_t nabu_touch_stats[8] = {};
volatile uint64_t nabu_touch_registry_id = 0;
}

#define TLOG(fmt, ...) IOLog("NABU TOUCH " fmt "\n", ##__VA_ARGS__)

namespace {

// ---------------------------------------------------------------- MMIO
struct Mmio {
  volatile uint32_t *base = nullptr;
  uint32_t r(uint32_t off) const { return base[off / 4]; }
  void w(uint32_t off, uint32_t value) const {
    base[off / 4] = value;
    __asm__ volatile("dsb sy" ::: "memory");
  }
};

Mmio map(uint64_t pa, uint32_t size)
{
  Mmio m;
  m.base = reinterpret_cast<volatile uint32_t *>(ml_io_map(static_cast<vm_offset_t>(pa), size));
  return m;
}

uint64_t deadlineUs(uint64_t us)
{
  uint64_t delta;
  nanoseconds_to_absolutetime(us * 1000, &delta);
  return mach_absolute_time() + delta;
}

// ------------------------------------------------- GCC / TLMM / GENI SE18
constexpr uint64_t kGccQupPa = 0x0011e000;   // GCC + 0x1e000: wrap2 AHB CBCRs, S1 CBCR/RCG
constexpr uint64_t kGccVotePa = 0x00152000;  // GCC + 0x52000: APCS clock branch vote at +0x14
constexpr uint64_t kSePa = 0x00c84000;
constexpr uint64_t kTlmmSpiPa = 0x03517000;  // GPIO 23..26, one 0x1000 window each
constexpr uint64_t kTlmmIntPa = 0x03927000;  // GPIO 39
constexpr uint32_t kRcg = 0x278;             // wrap2 S1 RCG CMD within kGccQupPa

enum : uint32_t {
  GENI_FORCE_DEFAULT = 0x20, GENI_OUTPUT_CTRL = 0x24, GENI_CGC_CTRL = 0x28, GENI_STATUS = 0x40,
  GENI_M_CLK_CFG = 0x48, GENI_FIFO_DISABLE = 0x64, GENI_FW_REVISION = 0x68, GENI_CLK_SEL = 0x7c,
  SPI_CPHA = 0x224, SPI_LOOPBACK = 0x22c, SPI_CPOL = 0x230, SPI_DEMUX_INV = 0x24c,
  SPI_DEMUX_SEL = 0x250, GENI_BYTE_GRAN = 0x254, GENI_DMA_MODE = 0x258, SPI_TRANS_CFG = 0x25c,
  TX_PACK0 = 0x260, TX_PACK1 = 0x264, SPI_WORD_LEN = 0x268, SPI_TX_LEN = 0x26c, SPI_RX_LEN = 0x270,
  SPI_PRE_POST_DLY = 0x274, SPI_DELAY = 0x278, RX_PACK0 = 0x284, RX_PACK1 = 0x288,
  M_CMD0 = 0x600, M_CMD_CTRL = 0x604, M_IRQ_STATUS = 0x610, M_IRQ_EN = 0x614, M_IRQ_CLEAR = 0x618,
  S_IRQ_CLEAR = 0x648, TX_FIFO = 0x700, RX_FIFO = 0x780, TX_FIFO_STATUS = 0x800,
  RX_FIFO_STATUS = 0x804, RX_WM = 0x810, RX_RFR_WM = 0x814, GSI_EVENT_EN = 0xe18, SE_IRQ_EN = 0xe1c,
};
constexpr uint32_t kMErrors = 0x3300003e;          // command + TX/RX FIFO faults
uint32_t gFifoWords = 0;

Mmio gSe;
uint8_t *gScratch = nullptr;                 // full-duplex receive buffer
constexpr uint32_t kScratch = 256;

bool clocksUp()
{
  Mmio gcc = map(kGccQupPa, 0x1000), vote = map(kGccVotePa, 0x1000);
  if (!gcc.base || !vote.base) { return false; }
  TLOG("clk before vote=%#x m-ahb=%#x s-ahb=%#x se=%#x rcg=%#x/%#x dfs=%#x", vote.r(0x14),
       gcc.r(0x004), gcc.r(0x008), gcc.r(0x274), gcc.r(kRcg), gcc.r(kRcg + 4), gcc.r(kRcg + 0x14));
  // Shared vote register: only add SE18 core (bit5), wrap2 m-ahb (bit2) and s-ahb (bit1).
  vote.w(0x14, vote.r(0x14) | (1u << 5) | (1u << 2) | (1u << 1));
  uint64_t end = deadlineUs(20000);
  // wrap2_s_ahb has HWCG bit1: 0x80000002 is idle hardware gating,
  // not a failed software vote. Mainline clk_branch_wait skips halt in HWCG.
  auto ready=[&]() { return nabu::clockBranchReady(gcc.r(0x004)) &&
      nabu::clockBranchReady(gcc.r(0x008),(gcc.r(0x008)&2)!=0) &&
      nabu::clockBranchReady(gcc.r(0x274)); };
  while (!ready() && mach_absolute_time() < end) { IODelay(10); }
  uint32_t m = gcc.r(0x004), se = gcc.r(0x274), slave=gcc.r(0x008);
  TLOG("clk after vote=%#x m-ahb=%#x s-ahb=%#x se=%#x", vote.r(0x14), m, gcc.r(0x008), se);
  if (!nabu::clockBranchReady(m) || !nabu::clockBranchReady(slave,(slave&2)!=0) || !nabu::clockBranchReady(se)) { return false; }
  // Pick an SE clock: DFS levels are preset by firmware and selected by GENI CLK_SEL.
  uint32_t level = 0;
  uint64_t hz = 0;
  if (gcc.r(kRcg + 0x14) & 1) {
    for (uint32_t l = 0; l < 8; l++) {
      uint32_t perf = gcc.r(kRcg + 0x1c + 4 * l);
      uint32_t src = (perf >> 8) & 7, div = perf & 0x1f, mode = (perf >> 12) & 3;
      uint64_t f = src == 0 ? 19200000ull : src == 1 ? 600000000ull : src == 6 ? 300000000ull : 0;
      if (div) { f = f * 2 / (div + 1); }
      if (mode && f) {
        uint32_t mv = gcc.r(kRcg + 0x5c + 4 * l) & 0xffff;
        uint32_t nv = ((~gcc.r(kRcg + 0x9c + 4 * l)) & 0xffff) + mv;
        f = nv ? f * mv / nv : 0;
      }
      TLOG("clk dfs level=%u perf=%#x hz=%llu", l, perf, (unsigned long long)f);
      if (f && (!hz || f < hz)) { hz = f; level = l; }
    }
  } else {
    // No DFS: run the RCG straight from TCXO.
    gcc.w(kRcg + 4, gcc.r(kRcg + 4) & ~0x371fu);
    gcc.w(kRcg, gcc.r(kRcg) | 1);
    end = deadlineUs(2000);
    while ((gcc.r(kRcg) & 1) && mach_absolute_time() < end) { IODelay(1); }
    hz = 19200000ull;
  }
  if (!hz) return false; // Unknown DFS parents cannot safely set SPI timing.
  uint32_t div = static_cast<uint32_t>((hz + 9600000 - 1) / 9600000);
  if (div < 1) { div = 1; }
  if (div > 0xfff) { div = 0xfff; }
  gSe.w(GENI_CLK_SEL, level);
  gSe.w(GENI_M_CLK_CFG, (div << 4) | 1);
  TLOG("clk se level=%u hz=%llu geni-div=%u sclk=%llu", level, (unsigned long long)hz, div, (unsigned long long)(hz / div));
  return true;
}

bool pinsUp()
{
  static const uint32_t ctl[4] = {0x088, 0x08c, 0x08c, 0x088};   // qup18 function, 6 mA, no pull
  Mmio spi = map(kTlmmSpiPa, 0x4000);
  if (!spi.base) return false;
  {
    for (uint32_t i = 0; i < 4; i++) {
      TLOG("pin gpio%u ctl=%#x -> %#x", 23 + i, spi.r(i * 0x1000), ctl[i]);
      spi.w(i * 0x1000, ctl[i]);
      if ((spi.r(i * 0x1000) & 0x3ff) != ctl[i]) return false;
    }
  }
  return true;
}

bool seInit()
{
  uint32_t fw = gSe.r(GENI_FW_REVISION), fifoOff = gSe.r(GENI_FIFO_DISABLE);
  TLOG("se fw=%#x proto=%u fifo-disabled=%u status=%#x m-irq=%#x", fw, (fw >> 8) & 0xff,
       fifoOff & 1, gSe.r(GENI_STATUS), gSe.r(M_IRQ_STATUS));
  const uint32_t hw = gSe.r(0xe24);
  gFifoWords=(hw>>16)&0x3f;
  if(gFifoWords<4 || gFifoWords>63 || ((hw>>24)&0x3f)!=32) return false;
  if (((fw >> 8) & 0xff) != 1 || (fifoOff & 1)) { return false; }
  if (gSe.r(GENI_STATUS) & 1) {
    gSe.w(M_CMD_CTRL, 1u << 2);
    IODelay(100);
    gSe.w(M_CMD_CTRL, 1u << 1);
    IODelay(100);
  }
  gSe.w(GENI_CGC_CTRL, gSe.r(GENI_CGC_CTRL) | 0x7f);
  gSe.w(0xe30, gSe.r(0xe30) | 0xf); // internal AHB and DMA clock gating
  gSe.w(GENI_OUTPUT_CTRL, 0x7f);
  gSe.w(GENI_FORCE_DEFAULT, 1);
  gSe.w(GSI_EVENT_EN, 0);
  gSe.w(SE_IRQ_EN, 0);                 // polled: keep the GIC line quiet
  gSe.w(GENI_DMA_MODE, gSe.r(GENI_DMA_MODE) & ~1u);
  gSe.w(M_IRQ_CLEAR, 0xffffffffu);
  gSe.w(S_IRQ_CLEAR, 0xffffffffu);
  gSe.w(M_IRQ_EN, gSe.r(M_IRQ_EN) | 0x7f00003fu);
  gSe.w(RX_WM, 0);
  gSe.w(RX_RFR_WM, gFifoWords - 2);
  gSe.w(TX_PACK0, 0x0007f8fe); gSe.w(TX_PACK1, 0x000ffefe);
  gSe.w(RX_PACK0, 0x0007f8fe); gSe.w(RX_PACK1, 0x000ffefe);
  gSe.w(GENI_BYTE_GRAN, 0);
  gSe.w(SPI_WORD_LEN, 8 - 4);
  gSe.w(SPI_CPHA, 0); gSe.w(SPI_CPOL, 0); gSe.w(SPI_LOOPBACK, 0);
  gSe.w(SPI_DEMUX_SEL, 0); gSe.w(SPI_DEMUX_INV, 0);
  gSe.w(SPI_TRANS_CFG, gSe.r(SPI_TRANS_CFG) & ~1u);
  gSe.w(SPI_PRE_POST_DLY, 0); gSe.w(SPI_DELAY, 0);
  return true;
}

// One CS-asserted transfer: header bytes then payload (payload nullptr sends zeros).
// rx, when given, receives all hlen + plen bytes clocked in.
bool spiXfer(const uint8_t *hdr, uint32_t hlen, const uint8_t *payload, uint32_t plen, uint8_t *rx)
{
  if(hlen>3 || plen>64*1024 || (!hdr&&hlen))return false;
  const uint32_t len = hlen + plen;
  uint64_t end = deadlineUs(5000);
  while ((gSe.r(GENI_STATUS) & 1) && mach_absolute_time() < end) { IODelay(1); }
  if (gSe.r(GENI_STATUS) & 1) { return false; }
  gSe.w(M_IRQ_CLEAR, 0xffffffffu);
  gSe.w(SPI_TX_LEN, len);
  if (rx) { gSe.w(SPI_RX_LEN, len); }
  gSe.w(M_CMD0, (rx ? 3u : 1u) << 27);
  uint32_t tx = 0, got = 0;
  end = deadlineUs(50000 + static_cast<uint64_t>(len) * 4);
  for (;;) {
    while (tx < len && (gSe.r(TX_FIFO_STATUS) & 0x0fffffffu) < gFifoWords) {
      uint32_t word = 0;
      for (uint32_t b = 0; b < 4 && tx < len; b++, tx++) {
        uint8_t v = tx < hlen ? hdr[tx] : (payload ? payload[tx - hlen] : 0);
        word |= static_cast<uint32_t>(v) << (8 * b);
      }
      gSe.w(TX_FIFO, word);
    }
    if (rx) {
      uint32_t words = gSe.r(RX_FIFO_STATUS) & 0x1ffffffu;
      if(words>gFifoWords)break; // bad/all-ones hardware must not spin indefinitely
      while (words--) {
        uint32_t word = gSe.r(RX_FIFO);
        for (uint32_t b = 0; b < 4 && got < len; b++, got++) { rx[got] = static_cast<uint8_t>(word >> (8 * b)); }
      }
    }
    uint32_t irq = gSe.r(M_IRQ_STATUS);
    if (irq & kMErrors) { break; }
    if ((irq & 1) && tx >= len && (!rx || got >= len)) {
      gSe.w(M_IRQ_CLEAR, irq);
      return true;
    }
    if (mach_absolute_time() > end) { break; }
  }
  static unsigned failures = 0;
  if (++failures <= 3 || (failures % 64) == 0)
    TLOG("spi fail count=%u len=%u tx=%u rx=%u irq=%#x status=%#x", failures, len, tx, got, gSe.r(M_IRQ_STATUS), gSe.r(GENI_STATUS));
  gSe.w(M_CMD_CTRL, 1u << 2);
  IODelay(100);
  if (gSe.r(GENI_STATUS) & 1) { gSe.w(M_CMD_CTRL, 1u << 1); IODelay(100); }
  gSe.w(M_IRQ_CLEAR, 0xffffffffu);
  return false;
}

// ---------------------------------------------------------- NT36523 protocol
bool ntPage(uint32_t addr)
{
  const uint8_t b[3] = {0xff, static_cast<uint8_t>(addr >> 15), static_cast<uint8_t>(addr >> 7)};
  return spiXfer(b, 3, nullptr, 0, nullptr);
}

bool ntWrite(uint32_t addr, const uint8_t *data, uint32_t n)
{
  const uint8_t cmd = static_cast<uint8_t>(0x80 | (addr & 0x7f));
  return spiXfer(&cmd, 1, data, n, nullptr);
}

bool ntRead(uint32_t addr, uint8_t *out, uint32_t n)
{
  if (n + 2 > kScratch) { return false; }
  const uint8_t cmd = static_cast<uint8_t>(addr & 0x7f);
  if (!spiXfer(&cmd, 1, nullptr, n + 1, gScratch)) { return false; }
  memcpy(out, gScratch + 2, n);
  return true;
}

bool ntWriteAddr(uint32_t addr, uint8_t value)
{
  return ntPage(addr) && ntWrite(addr, &value, 1);
}

bool ntReadAddr(uint32_t addr, uint8_t *value)
{
  return ntPage(addr) && ntRead(addr, value, 1);
}

bool ntBootloaderReset()
{
  if (!ntWriteAddr(0x3f0fe, 0x69)) return false;
  IOSleep(5);
  return ntWriteAddr(0x3f310, 0x00);
}

bool ntChipId()
{
  if (!ntWriteAddr(0x7fff80, 0x5a)) return false;
  IOSleep(11);
  for (int attempt = 0; attempt < 5; attempt++) {
    if (!ntBootloaderReset()) continue;
    uint8_t id[6] = {};
    bool ok = ntPage(0x3f004) && ntRead(0x3f004, id, 6);
    TLOG("chip-id try=%d ok=%d %02x %02x %02x %02x %02x %02x", attempt, ok, id[0], id[1], id[2], id[3], id[4], id[5]);
    if (ok && id[3] == 0x23 && id[4] == 0x65 && id[5] == 0x03) { return true; }
    IOSleep(10);
  }
  return false;
}

struct Firmware {
  const uint8_t *blob = nullptr;
  uint32_t blobSize = 0;
  const uint32_t *parts = nullptr;   // {sram, bin, length-to-write} triples
  uint32_t partCount = 0;
  uint32_t bank[6] = {};             // ILM sram/size/crc, DLM sram/size/crc
};

bool ntCrcBank(uint32_t des, uint32_t len, uint32_t gchk, uint32_t sram, uint32_t size, uint32_t crc)
{
  if (!ntPage(des)) return false;
  const uint8_t a[3] = {static_cast<uint8_t>(sram), static_cast<uint8_t>(sram >> 8), static_cast<uint8_t>(sram >> 16)};
  if (!ntWrite(des, a, 3)) return false;
  const uint8_t l[3] = {static_cast<uint8_t>(size), static_cast<uint8_t>(size >> 8), static_cast<uint8_t>((size >> 16) & 1)};
  if (!ntPage(len) || !ntWrite(len, l, 3)) return false;
  const uint8_t c[4] = {static_cast<uint8_t>(crc), static_cast<uint8_t>(crc >> 8), static_cast<uint8_t>(crc >> 16),
                        static_cast<uint8_t>(crc >> 24)};
  return ntPage(gchk) && ntWrite(gchk, c, 4);
}

uint32_t gXMax = 1600, gYMax = 2560;

bool ntDownload(const Firmware &fw)
{
  for (int attempt = 0; attempt < 3; attempt++) {
    uint64_t start = mach_absolute_time();
    if (!ntBootloaderReset()) continue;
    if (!ntCrcBank(0x3f128, 0x3f118, 0x3f100, fw.bank[0], fw.bank[1], fw.bank[2]) ||
        !ntCrcBank(0x3f12c, 0x3f130, 0x3f104, fw.bank[3], fw.bank[4], fw.bank[5]) ||
        !ntWriteAddr(0x3f7e8, 0x69)) continue;
    bool sent = true;
    for (uint32_t p = 0; p < fw.partCount && sent; p++) {
      uint32_t sram = fw.parts[3 * p], bin = fw.parts[3 * p + 1], left = fw.parts[3 * p + 2];
      while (left && sent) {
        uint32_t chunk = left < 63 * 1024 ? left : 63 * 1024;
        sent = ntPage(sram) && ntWrite(sram, fw.blob + bin, chunk);
        sram += chunk; bin += chunk; left -= chunk;
      }
    }
    uint8_t dma = 0xee;
    for (int i = 0; i < 200 && sent; i++) {
      if (ntReadAddr(0x3f7f1, &dma) && dma == 0) { break; }
      IOSleep(1);
    }
    uint8_t crcEn = 0;
    if (!sent || dma != 0 || !ntReadAddr(0x3f30e, &crcEn) ||
        !ntWriteAddr(0x3f30e, crcEn | 0x80) || !ntPage(0x2fe00)) continue;
    uint8_t zero = 0, crcCmd = 0xae;
    if (!ntWrite(0x2fe60, &zero, 1) || !ntWrite(0x2fe50, &crcCmd, 1) ||
        !ntWriteAddr(0x3f10d, 1)) continue;
    IOSleep(5);
    uint8_t state[5] = {};
    bool booted = false;
    for (int i = 0; i < 10 && !booted; i++) {
      booted = ntPage(0x2fe60) && ntRead(0x2fe60, state, 5) && state[0] >= 0xa0 && state[0] <= 0xaf;
      if (!booted) { IOSleep(10); }
    }
    uint64_t ns;
    absolutetime_to_nanoseconds(mach_absolute_time() - start, &ns);
    TLOG("fw download try=%d sent=%d dma=%#x reset-state=%#x booted=%d ms=%llu", attempt, sent, dma,
         state[0], booted, (unsigned long long)(ns / 1000000));
    if (!booted) {
      uint8_t crcFlags = 0;
      ntReadAddr(0x3f133, &crcFlags);
      TLOG("fw crc flags=%#x", crcFlags);
      continue;
    }
    uint8_t info[38] = {};
    if (ntPage(0x2fe78) && ntRead(0x2fe78, info, sizeof(info))) {
      uint32_t xm = (info[4] << 8) | info[5], ym = (info[6] << 8) | info[7];
      TLOG("fw info ver=%#x/%#x xnum=%u ynum=%u xmax=%u ymax=%u type=%#x", info[0], info[1], info[2], info[3], xm,
           ym, info[13]);
      if (static_cast<uint8_t>(info[0] + info[1]) == 0xff && xm && ym) { gXMax = xm; gYMax = ym; }
    }
    uint8_t pid[2] = {};
    if (ntPage(0x2fe9a) && ntRead(0x2fe9a, pid, 2)) { TLOG("fw pid=%#x", pid[0] | (pid[1] << 8)); }
    if(!ntPage(0x2fe00)) continue;
    nabu_touch_stats[7] = nabu_touch_stats[7] + 1;
    return true;
  }
  return false;
}

// Contact transport is shared; no iOS edge-event vtable patch is used.
constexpr unsigned kFingers = 10;
struct Frame {
  unsigned count=0;
  int ids[kFingers], xs[kFingers], ys[kFingers];   // normalised 0..32767
};

}  // namespace

// --------------------------------------------------------- macOS HID pointer
// Absolute mouse reports let the macOS desktop use the panel without an
// iOS-only backboardd/digitizer consumer. Keep one primary contact until lift.
#include "NabuTouchPointer.h"
class NabuTouchHid : public IOHIDDevice {
  OSDeclareDefaultStructors(NabuTouchHid)
  IOWorkLoop *inputLoop = nullptr;
  NabuPointerState pointer;
  static IOReturn sendAction(OSObject *owner, void *arg, void *, void *, void *) {
    auto *self = static_cast<NabuTouchHid *>(owner);
    auto *frame = static_cast<Frame *>(arg);
    NabuPointerContact contacts[kFingers];
    for (unsigned i=0; i<frame->count; ++i)
      contacts[i] = {frame->ids[i], uint16_t(frame->xs[i]), uint16_t(frame->ys[i])};
    uint8_t report[6];
    if (!self->pointer.encode(contacts, frame->count, report)) return kIOReturnSuccess;
    auto *buffer=IOBufferMemoryDescriptor::withBytes(report,sizeof(report),kIODirectionNone);
    IOReturn result=buffer?self->handleReport(buffer,kIOHIDReportTypeInput,0):kIOReturnNoMemory;
    OSSafeReleaseNULL(buffer);
    nabu_touch_stats[4]=nabu_touch_stats[4]+1;nabu_touch_stats[5]=uint32_t(result);
    return result;
  }
public:
  void deliver(const Frame &frame) {
    if(inputLoop)inputLoop->runAction(&NabuTouchHid::sendAction,this,const_cast<Frame *>(&frame));
  }
  IOWorkLoop *getWorkLoop() const override {return inputLoop;}
  OSString *newTransportString() const override {return OSString::withCString("SPI");}
  OSString *newProductString() const override {return OSString::withCString("NABU NT36523 desktop pointer");}
  IOReturn newReportDescriptor(IOMemoryDescriptor **out) const override {
    if(!out)return kIOReturnBadArgument;
    *out=IOBufferMemoryDescriptor::withBytes(kNabuPointerDescriptor,sizeof(kNabuPointerDescriptor),kIODirectionNone);
    return *out?kIOReturnSuccess:kIOReturnNoMemory;
  }
  bool start(IOService *provider) override {
    inputLoop=IOWorkLoop::workLoop();if(!inputLoop)return false;
    setProperty("HIDDefaultBehavior",true);setProperty("Built-In",true);
    setProperty("AbsoluteAxisBoundsRemovalPercentage",0ull,32);
    return IOHIDDevice::start(provider);
  }
  void stop(IOService *provider) override {IOHIDDevice::stop(provider);}
  void free() override {OSSafeReleaseNULL(inputLoop);IOHIDDevice::free();}
};

OSDefineMetaClassAndStructors(NabuTouchHid, IOHIDDevice)
static_assert(sizeof(NabuTouchHid) > 0xd0, "Original device storage precedes the owned fields");

namespace {

IOService *gPlatform = nullptr;
uint32_t gSwapXY = 0, gInvertX = 0, gInvertY = 0;

// The macOS KC has IOHIDEventDriver code but no generic mouse personality
// in IOHIDFamily's prelink dictionary. Add a native-family personality for
// this board's SPI interface; no private IOHIDEvent object layout is used.
bool publishPointerPersonality()
{
  OSDictionary *personality=OSDictionary::withCapacity(7);
  OSDictionary *usage=OSDictionary::withCapacity(2);
  OSArray *pairs=OSArray::withCapacity(1), *drivers=OSArray::withCapacity(1);
  OSNumber *page=OSNumber::withNumber(1,32), *mouse=OSNumber::withNumber(2,32);
  OSString *family=OSString::withCString("com.apple.iokit.IOHIDFamily");
  OSString *klass=OSString::withCString("IOHIDEventDriver");
  OSString *provider=OSString::withCString("IOHIDInterface");
  OSString *product=OSString::withCString("NABU NT36523 desktop pointer");
  OSDictionary *match=OSDictionary::withCapacity(1);
  bool ok=personality && usage && pairs && drivers && page && mouse && family && klass && provider && product && match;
  if(ok) {
    ok=usage->setObject("DeviceUsagePage",page) && usage->setObject("DeviceUsage",mouse) && pairs->setObject(usage) &&
       match->setObject("Product",product) && personality->setObject("CFBundleIdentifier",family) &&
       personality->setObject("IOClass",klass) && personality->setObject("IOProviderClass",provider) &&
       personality->setObject("IOPropertyMatch",match) && personality->setObject("DeviceUsagePairs",pairs) &&
       drivers->setObject(personality);
  }
  if(ok)ok=gIOCatalogue && gIOCatalogue->addDrivers(drivers,true);
  OSSafeReleaseNULL(personality);OSSafeReleaseNULL(usage);OSSafeReleaseNULL(pairs);OSSafeReleaseNULL(drivers);
  OSSafeReleaseNULL(page);OSSafeReleaseNULL(mouse);OSSafeReleaseNULL(family);OSSafeReleaseNULL(klass);
  OSSafeReleaseNULL(provider);OSSafeReleaseNULL(product);OSSafeReleaseNULL(match);
  return ok;
}

NabuTouchHid *publishHid()
{
  nabu::FamilyRegistration registration;
  if(!registration.valid()) return nullptr;
  kern_return_t result = OSKextLoadKextWithIdentifier("com.apple.iokit.IOHIDFamily");
  TLOG("IOHIDFamily load=%d", result);
  if (result != KERN_SUCCESS) { return nullptr; }
  // The original IOHIDDevice metaclass must exist before the subclass initializer runs.
  void *handle = OSMetaClass::preModLoad(g_kernel_kmod_info.name);
  if (!handle) { return nullptr; }
  nabu_touch_static_init();
  if (OSMetaClass::postModLoad(handle) != KERN_SUCCESS) {
    TLOG("subclass registration failed");
    return nullptr;
  }
  if (!publishPointerPersonality()) { TLOG("native mouse personality registration failed"); return nullptr; }
  NabuTouchHid *device = new NabuTouchHid;
  if (!device) { return nullptr; }
  if (!device->init(nullptr) || !device->attach(gPlatform)) {
    device->release();
    return nullptr;
  }
  if (!device->start(gPlatform)) {
    device->detach(gPlatform);
    device->release();
    TLOG("original IOHIDDevice start failed");
    return nullptr;
  }
  nabu_touch_registry_id = device->getRegistryEntryID();
  TLOG("absolute mouse published registry-id=%llu", (unsigned long long)nabu_touch_registry_id);
  return device;
}

bool loadFirmware(Firmware &fw)
{
  IORegistryEntry *node = IORegistryEntry::fromPath("/nabu-touch", gIODTPlane);
  if (!node) { TLOG("ADT /nabu-touch missing"); return false; }
  OSData *blob = OSDynamicCast(OSData, node->getProperty("firmware"));
  OSData *parts = OSDynamicCast(OSData, node->getProperty("partitions"));
  OSData *bank = OSDynamicCast(OSData, node->getProperty("crc-bank"));
  bool ok = blob && parts && bank && parts->getLength() && parts->getLength() % 12 == 0 && bank->getLength() == 24;
  if (ok) {
    fw.blob = static_cast<const uint8_t *>(blob->getBytesNoCopy());
    fw.blobSize = blob->getLength();
    fw.parts = static_cast<const uint32_t *>(parts->getBytesNoCopy());
    fw.partCount = parts->getLength() / 12;
    memcpy(fw.bank, bank->getBytesNoCopy(), 24);
    for (uint32_t p = 0; p < fw.partCount && ok; p++) {
      ok = fw.parts[3 * p + 1] <= fw.blobSize && fw.parts[3 * p + 2] <= fw.blobSize - fw.parts[3 * p + 1];
    }
    const struct { const char *key; uint32_t *value; } flags[] = {
      {"swap-xy", &gSwapXY}, {"invert-x", &gInvertX}, {"invert-y", &gInvertY}};
    for (const auto &flag : flags) {
      OSData *d = OSDynamicCast(OSData, node->getProperty(flag.key));
      if (d && d->getLength() == 4) { *flag.value = *static_cast<const uint32_t *>(d->getBytesNoCopy()); }
    }
  }
  TLOG("firmware ok=%d bytes=%u partitions=%u swap=%u invx=%u invy=%u", ok, fw.blobSize, fw.partCount, gSwapXY,
       gInvertX, gInvertY);
  node->release();
  return ok;
}

int scale(int value, int maximum, bool invert)
{
  if (value < 0) { value = 0; }
  if (value > maximum) { value = maximum; }
  if (invert) { value = maximum - value; }
  return static_cast<int>(static_cast<long long>(value) * 32767 / maximum);
}

// Decode the stock 65-byte non-pen event payload; false means skip this read.
bool decode(const uint8_t *d, Frame &frame, bool &wdtPattern, unsigned logBudget)
{
  wdtPattern = true;
  bool esd = true;
  for (int i = 0; i < 6; i++) {
    esd &= d[i] == 0x77;
    wdtPattern &= d[i] == 0xfd || d[i] == 0xfe;
  }
  if (esd || wdtPattern) { return false; }
  uint8_t sum = 0;
  for (int i = 0; i < 64; i++) { sum = static_cast<uint8_t>(sum + d[i]); }
  if (static_cast<uint8_t>(-sum) != d[64]) {
    nabu_touch_stats[3] = nabu_touch_stats[3] + 1;
    return false;
  }
  frame.count = 0;
  for (unsigned i = 0; i < kFingers; i++) {
    const uint8_t *p = d + 6 * i;
    int id = p[0] >> 3, st = p[0] & 7;
    if (id < 1 || id > static_cast<int>(kFingers) || (st != 1 && st != 2)) { continue; }
    int x = (p[1] << 4) | (p[3] >> 4), y = (p[2] << 4) | (p[3] & 0x0f);
    if (x > static_cast<int>(gXMax) || y > static_cast<int>(gYMax)) { continue; }
    
    int sx = gSwapXY ? y : x, sy = gSwapXY ? x : y;
    int mx = static_cast<int>(gSwapXY ? gYMax : gXMax), my = static_cast<int>(gSwapXY ? gXMax : gYMax);
    frame.ids[frame.count] = id;
    frame.xs[frame.count] = scale(sx, mx, gInvertX);
    frame.ys[frame.count] = scale(sy, my, gInvertY);
    if(logBudget && frame.count==0)TLOG("contact id=%d raw(%d,%d) -> mapped(%d,%d) panel=%ux%u swap=%u invert=%u,%u",id,x,y,frame.xs[0],frame.ys[0],gXMax,gYMax,gSwapXY,gInvertX,gInvertY);
    frame.count++;
  }
  return true;
}

void touchThread(void *, wait_result_t)
{
  uint32_t delay = 10;
  PE_parse_boot_argn("nabu-touch-delay", &delay, sizeof(delay));
  IOSleep(delay * 1000);
  nabu_touch_stats[0] = 1;
  Firmware fw;
  gScratch = static_cast<uint8_t *>(IOMalloc(kScratch));
  if (!gScratch || !loadFirmware(fw)) { TLOG("stopped before hardware"); return; }
  nabu_touch_stats[0] = 2;
  gSe = map(kSePa, 0x4000);
  if (!gSe.base || !clocksUp()) { TLOG("clocks not available; stopped"); return; }
  nabu_touch_stats[0] = 3;
  if (!pinsUp()) { TLOG("TLMM setup failed; stopped"); return; }
  if (!seInit()) { TLOG("SE18 not usable as FIFO SPI; stopped"); return; }
  nabu_touch_stats[0] = 4;
  if (!ntChipId()) { TLOG("NT36523 not identified; stopped"); return; }
  nabu_touch_stats[0] = 5;
  if (!ntDownload(fw)) { TLOG("firmware download failed; stopped"); return; }
  nabu_touch_stats[0] = 6;
  NabuTouchHid *hid = publishHid();
  if (!hid) { TLOG("HID publication failed; stopped"); return; }
  Mmio irq = map(kTlmmIntPa, 0x1000);
  if (!irq.base) { TLOG("GPIO39 mapping failed; stopped"); return; }
  {
    TLOG("gpio39 ctl=%#x io=%#x cfg=%#x st=%#x", irq.r(0), irq.r(4), irq.r(8), irq.r(0xc));
    irq.w(0, 0x1c1);      // input, pull-down, 16 mA
    irq.w(8, 0x16);       // rising edge latched in RAW_STATUS, no CPU interrupt
    irq.w(0xc, 0);
    if((irq.r(0)&0x3ff)!=0x1c1 || (irq.r(8)&0x1f)!=0x16) { TLOG("GPIO39 configuration rejected"); return; }
  }
  TLOG("polling started xmax=%u ymax=%u", gXMax, gYMax);
  // The stock non-pen work function reads 65 payload bytes (64 + checksum).
  // Bytes beyond that page are pen/other state, outside this checksum.
  uint8_t event[65];
  uint8_t last[65] = {};
  bool latchSeen = false;
  unsigned pollRound=0, readFailures=0, reloads=0;
  unsigned wdtRun = 0, logBudget = 5, lastFingers = 0;
  uint64_t nextStatus = deadlineUs(30000000);
  uint64_t lastValid = mach_absolute_time();
  for (;;) {
    bool latched = irq.base && (irq.r(0xc) & 1);
    if (latched) {
      irq.w(0xc, 0);
      latchSeen = true;
      nabu_touch_stats[6] = nabu_touch_stats[6] + 1;
    }
    if (latched || !latchSeen || (++pollRound % 10)==0) {
      if (ntRead(0x2fe00, event, sizeof(event))) {
        readFailures = 0;
        nabu_touch_stats[1] = nabu_touch_stats[1] + 1;
        Frame frame;
        bool wdtPattern = false;
        const bool changed = memcmp(event, last, sizeof(event)) != 0;
        if (decode(event, frame, wdtPattern, changed?logBudget:0)) {
          wdtRun = 0;
          // A stationary finger still proves that the transport is alive.
          // Updating this only on a change caused a false button-up after
          // 250 ms when IRQs stopped and polling read the same valid packet.
          lastValid = mach_absolute_time();
          if(changed || frame.count!=lastFingers) {
            memcpy(last, event, sizeof(event));
            if (frame.count) {
              nabu_touch_stats[2] = nabu_touch_stats[2] + 1;
              if (logBudget) { logBudget--; }
            }
            if (frame.count || lastFingers) { hid->deliver(frame); }
            lastFingers = frame.count;
          }
        } else if (wdtPattern && ++wdtRun > 10) {
          if (++reloads > 3) {
            if(lastFingers){Frame released;hid->deliver(released);}
            TLOG("repeated firmware resets; stopped"); return;
          }
          TLOG("firmware reset pattern; downloading again (%u/3)", reloads);
          if(lastFingers){Frame released;hid->deliver(released);lastFingers=0;}
          wdtRun = 0;
          if (!ntDownload(fw)) { TLOG("re-download failed; stopped"); return; }
        }
      } else if (++readFailures >= 8) {
        if(lastFingers){Frame released;hid->deliver(released);}
        TLOG("eight consecutive SPI read failures; stopped"); return;
      }
    }
    uint64_t staleNs=0;
    absolutetime_to_nanoseconds(mach_absolute_time()-lastValid,&staleNs);
    if (lastFingers && staleNs>250000000) {
      Frame released; hid->deliver(released); lastFingers=0;
      TLOG("transport stalled: released pointer");
    }
    if (mach_absolute_time() > nextStatus) {
      TLOG("status reads=%u fingers=%u bad-sum=%u reports=%u last=%#x latch=%u", nabu_touch_stats[1],
           nabu_touch_stats[2], nabu_touch_stats[3], nabu_touch_stats[4], nabu_touch_stats[5], nabu_touch_stats[6]);
      nextStatus = deadlineUs(60000000);
    }
    IOSleep(8);
  }
}

}  // namespace

extern "C" bool nabu_touch_publish(IOService *platform)
{
  uint32_t enabled = 0;
  if (!PE_parse_boot_argn("nabu-touch", &enabled, sizeof(enabled)) || enabled != 1) { return false; }
  static unsigned started;
  if (__atomic_exchange_n(&started, 1, __ATOMIC_ACQ_REL)) return true;
  if (!platform) { __atomic_store_n(&started, 0, __ATOMIC_RELEASE); return false; }
  platform->retain();
  gPlatform = platform;
  thread_t thread;
  if (kernel_thread_start(touchThread, nullptr, &thread) != KERN_SUCCESS) {
    platform->release();gPlatform=nullptr;
    __atomic_store_n(&started, 0, __ATOMIC_RELEASE);
    TLOG("worker thread start failed");
    return false;
  }
  thread_deallocate(thread);
  TLOG("worker started");
  return true;
}
