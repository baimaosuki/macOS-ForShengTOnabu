/* SPDX-License-Identifier: MIT */
#ifndef NABU_GPU_PHYSMAP_H
#define NABU_GPU_PHYSMAP_H
#include <stdint.h>
#include <mach/vm_types.h>
/* XNU 12377.121.6 arm/pmap/pmap.h and arm64/arm_vm_init.c.
 * arm64 pmap_paddr_t is uint64_t. Use the real aperture (ptov_table/slide),
 * never gVirtBase arithmetic. range() caps each operation at an aperture span. */
extern "C" vm_map_address_t phystokv(uint64_t pa);
extern "C" vm_map_address_t phystokv_range(uint64_t pa,vm_size_t *max_len);
static_assert(sizeof(vm_map_address_t)==8 && sizeof(vm_size_t)==8,"arm64 physmap ABI");
#endif
