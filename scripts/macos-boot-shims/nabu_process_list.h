/* Bounded KERN_PROC_ALL snapshot: fork/exec can outgrow the size probe. */
#ifndef NABU_PROCESS_LIST_H
#define NABU_PROCESS_LIST_H
#include <errno.h>
#include <stdlib.h>

static struct kinfo_proc *
nabu_session_process_list(size_t *bytes)
{
    const size_t limit = 8 * 1024 * 1024;
    int mib[] = {CTL_KERN, KERN_PROC, KERN_PROC_ALL, 0};
    *bytes = 0;
    for (unsigned attempt = 0; attempt < 5; ++attempt) {
        size_t needed = 0;
        if (sysctl(mib, 4, NULL, &needed, NULL, 0)) {
            if (errno == ENOMEM) continue;
            return NULL;
        }
        if (!needed || needed > limit) { errno = EOVERFLOW; return NULL; }
        size_t capacity = needed > (limit - 65536) / 2 ? limit : needed * 2 + 65536;
        struct kinfo_proc *processes = malloc(capacity);
        if (!processes) return NULL;
        size_t actual = capacity;
        int result = sysctl(mib, 4, processes, &actual, NULL, 0);
        if (!result && actual <= capacity && !(actual % sizeof(*processes))) {
            *bytes = actual;
            return processes;
        }
        int error = result ? errno : EIO;
        free(processes);
        if (error != ENOMEM) { errno = error; return NULL; }
    }
    errno = ENOMEM;
    return NULL;
}
#endif
