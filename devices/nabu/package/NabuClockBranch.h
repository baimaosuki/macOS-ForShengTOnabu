// SPDX-License-Identifier: GPL-2.0-only
// Qualcomm clk_branch2_ops: CBCR_NOC_FSM_STATUS=2 is ON even if CLK_OFF=1.
// A branch with active HWCG intentionally gates when idle; halt is not polled.
#pragma once
#include <stdint.h>
namespace nabu {
inline bool clockBranchReady(uint32_t status,bool hardwareGated=false) {
    return hardwareGated || !(status&(1u<<31)) || ((status>>28)&7u)==2;
}
}
