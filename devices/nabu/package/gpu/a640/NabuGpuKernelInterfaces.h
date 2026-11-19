/* SPDX-License-Identifier: MIT */
#pragma once
#include <IOKit/IOUserClient.h>
/* Nonvirtual XNU12377 interfaces omitted from the public SDK. The three
 * callbacks match iokit/IOKit/IOUserClient.h; no private class layout is used. */
struct NabuGpuFilterCallbacks {
    IOReturn (*io_filter_resolver)(task_t,IOUserClient *,uint32_t,uintptr_t *);
    IOReturn (*io_filter_applier)(IOUserClient *,uintptr_t,uint32_t,uint32_t);
    void (*io_filter_release)(uintptr_t);
};
extern const NabuGpuFilterCallbacks *nabuGpuFilterCallbacks __asm__("_gIOUCFilterCallbacks");
extern "C" boolean_t IOTaskHasEntitlement(task_t,const char *);
extern "C" IOReturn nabuGpuCallExternal(IOUserClient *,uint32_t,IOExternalMethodArguments *)
    __asm__("__ZN12IOUserClient18callExternalMethodEjP25IOExternalMethodArguments");
