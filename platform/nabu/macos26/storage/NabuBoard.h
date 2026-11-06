// SPDX-License-Identifier: BSD-2-Clause-Patent
// nabu (Xiaomi Pad 5, SM8150) UFS facts for the read-only production path.
//
// Sources, read off this device or from public Linux:
//   /soc/ufshc@1d84000       reg 0x1d84000+0x2500 (reg-names "ufs_mem","ufs_ice"),
//                            compatible "qcom,sm8150-ufshc","qcom,ufshc",
//                            iommus = <&apps_smmu 0x300 0>
//                            (diagnostics/nabu-2026-10-21: android-live.dtb and
//                             linux-runtime/dtb-6.17.0-xiaomi-nabu-tmm-UFS-slowdown+)
//   /soc/ufsphy_mem@1d87000  qcom,sm8150-qmp-ufs-phy -- never touched by this port
//   /soc/apps-smmu@0x15000000 reg 0x15000000+0x100000, ARM SMMU-500 register layout
//   GCC                      drivers/clk/qcom/gcc-sm8150.c, read-only table
//   logical block            4096 bytes (storage.txt: Samsung KLUDG4UHDC-B0E1)
//
// A readable controller (CAP/VER non-zero) only proves that the GCC UFS branches are
// running. It says nothing about the PHY or the link: that needs HCS.DP and a UIC
// status read, and this port writes neither GCC nor PHY.
#ifndef NABU_UFS_BOARD_H
#define NABU_UFS_BOARD_H

#include <stdint.h>

#if defined(NABU_UFS_ALLOW_WRITES) && NABU_UFS_ALLOW_WRITES
#error "nabu UFS: the write / Data-store path is not qualified on this board"
#endif

namespace nabu_ufs {

struct Board {
  const char *compatible;          // the node this port was written for
  const char *compatibleFallback;  // the generic compatible the same node also carries
  uint64_t gccBase;                // clock-controller@100000
  uint64_t hcBase;                 // ufshc@1d84000
  uint64_t hcBytes;                // DT reg size
  uint64_t hcMapBytes;             // page-rounded mapping of hcBytes
  uint64_t smmuBase;               // apps_smmu@0x15000000
  uint64_t smmuBytes;              // DT reg size; one mapping covers SMR/S2CR/CBAR space
  uint16_t streamId;               // iommus = <&apps_smmu 0x300 0>
  uint32_t logicalBlockBytes;      // READ CAPACITY(10), verified before enabling the read path
  uint32_t gdscr;                  // GCC (gcc-sm8150.c), read only
  uint32_t axiEnable;
  uint32_t ahbEnable;
  uint32_t uniproEnable;
  uint32_t iceEnable;
  uint32_t phyAuxEnable;
  uint32_t txSymbol0Enable;
  uint32_t rxSymbol0Enable;
  uint32_t rxSymbol1Enable;
  uint32_t aggreAxiEnable;
  uint32_t bcr;
  bool writesAllowed;
};

constexpr Board kBoard = {
  "qcom,sm8150-ufshc",
  "qcom,ufshc",
  0x00100000ULL,
  0x01d84000ULL,
  0x00002500ULL,
  0x00003000ULL,
  0x15000000ULL,
  0x00100000ULL,
  0x0300,
  4096,
  0x77004, 0x77010, 0x77014, 0x77058, 0x7705c, 0x77090,
  0x77018, 0x7701c, 0x770ac, 0x770c0, 0x77000,
  false,
};

} // namespace nabu_ufs

#endif
