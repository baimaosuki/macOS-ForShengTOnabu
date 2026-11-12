// SPDX-License-Identifier: MIT
#pragma once

#include <arm/caches_internal.h>
#include <libkern/crypto/sha2.h>
#include <sys/csr.h>
#include <vm/nabu_cpu_compositor_policy.h>
#include <vm/nabu_cpu_compositor_pages.h>

extern const char *vnode_getname(struct vnode *);
extern void vnode_putname(const char *);
extern uint32_t sheng_recovery_ui_enabled;
TUNABLE(uint32_t, nabu_cpu_compositor_enabled, "nabu-cpu-compositor", 0);
volatile uint32_t nabu_cpu_compositor_pages_applied;
volatile uint32_t nabu_cpu_compositor_page_mask;
_Static_assert(sizeof(nabu_cpu_pages) / sizeof(nabu_cpu_pages[0]) == 3,
    "The CPU compositor policy changes exactly three cache pages");

static void
nabu_cpu_compositor_digest(const void *data, size_t size, uint8_t result[32])
{
    SHA256_CTX context;
    SHA256_Init(&context);
    SHA256_Update(&context, data, size);
    SHA256_Final(result, &context);
}

/* Invoked only after cs_validate_page, while the original vnode page remains
 * busy under its VM object lock. Do not copyout into an existing user mapping,
 * fabricate validation results, or make an executable mapping writable. */
static bool
nabu_cpu_compositor_page(vm_page_t page, struct vnode *vnode, uint64_t offset,
    bool original_validated)
{
    if (!nabu_cpu_compositor_enabled)
        return false;
    bool candidate = false;
    for (size_t i = 0; i < 3; ++i)
        candidate |= offset == nabu_cpu_pages[i].file_offset;
    if (!candidate)
        return false;
    const char *name = vnode_getname(vnode);
    if (!name)
        return false;
    for (size_t i = 0; i < 3; ++i) {
        const struct nabu_cpu_page *rule = &nabu_cpu_pages[i];
        if (offset != rule->file_offset || strcmp(name, rule->file))
            continue;
        vnode_putname(name);
        if (nabu_cpu_compositor_enabled != 1 || sheng_recovery_ui_enabled ||
            PAGE_SIZE != NABU_CPU_PAGE_BYTES ||
            csr_check(CSR_ALLOW_UNAUTHENTICATED_ROOT) != 0 || !original_validated ||
            !page->vmp_busy || page->vmp_cs_validated != VMP_CS_ALL_TRUE ||
            page->vmp_cs_tainted != VMP_CS_ALL_FALSE ||
            page->vmp_cs_nx != VMP_CS_ALL_FALSE || page->vmp_dirty || page->vmp_wpmapped)
            panic("NABU: CPU compositor page %zu requires an authenticated immutable development page", i);
        uint8_t *bytes = (uint8_t *)phystokv(
            (pmap_paddr_t)VM_PAGE_GET_PHYS_PAGE(page) << PAGE_SHIFT);
        int applied = nabu_cpu_apply_page(bytes, PAGE_SIZE, rule, nabu_cpu_compositor_digest);
        if (applied < 0)
            panic("NABU: CPU compositor page %zu digest or instructions differ", i);
        if (applied) {
            cache_sync_page(VM_PAGE_GET_PHYS_PAGE(page));
            OSAddAtomic(1, (volatile SInt32 *)&nabu_cpu_compositor_pages_applied);
            OSBitOrAtomic((uint32_t)1 << i, &nabu_cpu_compositor_page_mask);
            printf("NABU: CPU compositor page %zu applied to authenticated shared memory\n", i);
        }
        return true;
    }
    vnode_putname(name);
    return false;
}
