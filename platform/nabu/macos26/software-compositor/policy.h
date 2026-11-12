// SPDX-License-Identifier: MIT
#pragma once

#define NABU_CPU_PAGE_BYTES 16384u
#define NABU_CPU_MAX_EDITS 4u
#define NABU_CPU_MAX_EDIT_BYTES 16u

struct nabu_cpu_edit {
    uint32_t offset;
    uint32_t size;
    uint8_t before[NABU_CPU_MAX_EDIT_BYTES];
    uint8_t after[NABU_CPU_MAX_EDIT_BYTES];
};
struct nabu_cpu_page {
    const char *file;
    uint64_t file_offset;
    uint8_t before_sha256[32];
    uint8_t after_sha256[32];
    uint32_t edit_count;
    struct nabu_cpu_edit edits[NABU_CPU_MAX_EDITS];
};
typedef void (*nabu_cpu_digest_t)(const void *, size_t, uint8_t[32]);

/* The caller owns an originally authenticated, busy physical page. This is a
 * shared cache policy, not a process-private mapping. Validate every preimage
 * before changing bytes; leave the page unchanged on any rejected input. */
static inline int
nabu_cpu_apply_page(uint8_t *page, size_t size, const struct nabu_cpu_page *rule,
    nabu_cpu_digest_t digest)
{
    uint8_t hash[32];
    if (size != NABU_CPU_PAGE_BYTES || !rule->edit_count ||
        rule->edit_count > NABU_CPU_MAX_EDITS)
        return -1;
    for (uint32_t i = 0; i < rule->edit_count; ++i) {
        const struct nabu_cpu_edit *edit = &rule->edits[i];
        if (!edit->size || edit->size > NABU_CPU_MAX_EDIT_BYTES ||
            ((edit->size | edit->offset) & 3u) || edit->offset > size - edit->size)
            return -1;
        for (uint32_t j = 0; j < i; ++j) {
            const struct nabu_cpu_edit *other = &rule->edits[j];
            if (edit->offset < other->offset + other->size &&
                other->offset < edit->offset + edit->size)
                return -1;
        }
    }
    digest(page, size, hash);
    if (!memcmp(hash, rule->after_sha256, sizeof(hash)))
        return 0;
    if (memcmp(hash, rule->before_sha256, sizeof(hash)))
        return -1;
    for (uint32_t i = 0; i < rule->edit_count; ++i) {
        const struct nabu_cpu_edit *edit = &rule->edits[i];
        if (memcmp(page + edit->offset, edit->before, edit->size))
            return -1;
    }
    for (uint32_t i = 0; i < rule->edit_count; ++i) {
        const struct nabu_cpu_edit *edit = &rule->edits[i];
        memcpy(page + edit->offset, edit->after, edit->size);
    }
    digest(page, size, hash);
    if (!memcmp(hash, rule->after_sha256, sizeof(hash)))
        return 1;
    for (uint32_t i = 0; i < rule->edit_count; ++i) {
        const struct nabu_cpu_edit *edit = &rule->edits[i];
        memcpy(page + edit->offset, edit->before, edit->size);
    }
    return -1;
}
