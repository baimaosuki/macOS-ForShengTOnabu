/* SPDX-License-Identifier: MIT */
/* Display owner is the actual AGX handoff connection; GOP owns frame lifetime. */
#include <IOKit/IOMemoryDescriptor.h>
#include <mach/mach_types.h>
extern "C" IOReturn native_gop_handoff_owned_call(uint32_t,task_t,const void *,const void *,size_t,void *,size_t);
extern "C" IOMemoryDescriptor *native_gop_handoff_owned_memory(task_t,const void *);
extern "C" void native_gop_handoff_owned_close(const void *);
extern "C" IOReturn nabu_gpu_scanout_display_call(uint32_t op,task_t task,const void *owner,
    const void *input,size_t inputBytes,void *output,size_t outputBytes)
{
    return native_gop_handoff_owned_call(op,task,owner,input,inputBytes,output,outputBytes);
}
extern "C" IOMemoryDescriptor *nabu_gpu_scanout_display_memory(task_t task,const void *owner)
{
    return native_gop_handoff_owned_memory(task,owner);
}
extern "C" void nabu_gpu_scanout_display_close(const void *owner)
{
    native_gop_handoff_owned_close(owner);
}
