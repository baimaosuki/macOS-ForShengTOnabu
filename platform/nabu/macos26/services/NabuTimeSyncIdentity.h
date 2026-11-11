/* SPDX-License-Identifier: GPL-3.0-or-later */
#pragma once
#ifdef __cplusplus
extern "C" {
#endif
/* Call after the original IONetworkingFamily has run its constructors and this
 * translation unit's ordinary OSKext constructors have registered its class.
 * Runs once on a worker, never synchronously in IOResources::start(). */
void nabu_timesync_identity_start(void);
extern volatile int nabu_timesync_identity_state;
#ifdef __cplusplus
}
#endif
