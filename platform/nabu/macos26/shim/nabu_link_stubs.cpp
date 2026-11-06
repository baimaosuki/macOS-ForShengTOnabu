/* SPDX-License-Identifier: MIT */
/*
 * nabu: stand-ins for the entry points Apple ships inside the per-SoC kernel
 * support library (KDKROOT/System/Library/KernelSupport/lib<T6040>.os.*.a).
 * SM8150 has no such library, so the board supplies the minimal semantics the
 * bring-up profile needs instead of borrowing another SoC's platform code.
 *
 * Built with the kernel C++ driver record (Apple kext mode), hence extern "C".
 */

extern "C" {

typedef unsigned int nabu_boolean_t;

nabu_boolean_t ml_device_is_prod_fused(void);
bool pmap_pending_preemption(void);
void tb_transport_startup(void);

nabu_boolean_t
ml_device_is_prod_fused(void)
{
	return 0;                       /* not production fused */
}

bool
pmap_pending_preemption(void)
{
	return false;                   /* no SPTM/PPL preemption hand-off */
}

void
tb_transport_startup(void)
{
	/* Tightbeam/exclaves transport is not instantiated on nabu */
}

}
