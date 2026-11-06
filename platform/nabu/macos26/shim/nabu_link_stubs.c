/* SPDX-License-Identifier: MIT */
/*
 * nabu: stand-ins for the entry points Apple ships inside the per-SoC kernel
 * support library (KDKROOT/System/Library/KernelSupport/lib<T6040>.os.*.a).
 * SM8150 has no such library, so the board supplies the minimal semantics the
 * bring-up profile needs instead of borrowing another SoC's platform code.
 */

typedef unsigned int nabu_boolean_t;

extern nabu_boolean_t ml_device_is_prod_fused(void);
extern _Bool pmap_pending_preemption(void);
extern void tb_transport_startup(void);

nabu_boolean_t
ml_device_is_prod_fused(void)
{
	return 0;                       /* not production fused: keep eng sampling allowed */
}

_Bool
pmap_pending_preemption(void)
{
	return 0;                       /* no SPTM/PPL preemption hand-off on this board */
}

void
tb_transport_startup(void)
{
	/* Tightbeam/exclaves transport is not instantiated on nabu */
}
