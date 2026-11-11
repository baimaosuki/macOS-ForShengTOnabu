// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once
#include <IOKit/IOService.h>
// Invoke after the companion OSKext has initialized its C++ classes, outside
// OSKext's module-load lock. IOResources must already exist. No manual ctors.
extern "C" IOReturn nabu_keystore_start(IOService *resources);
