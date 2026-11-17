/* SPDX-License-Identifier: MIT */
#ifndef NABU_OBSERVER_OUTPUT_H
#define NABU_OBSERVER_OUTPUT_H
#include <errno.h>
#include <fcntl.h>
#include <stddef.h>
#include <stdio.h>
#include <sys/types.h>

/* The early wall clock and PID can repeat across boots. Reserve a fresh
 * suffix atomically; never truncate or delete any prior boot's evidence. */
static int
nabu_observer_output(int directory, unsigned long long timestamp, pid_t pid,
    char *name, size_t capacity)
{
    for (unsigned suffix = 0; suffix < 4096; ++suffix) {
        int size = snprintf(name, capacity, "session-%llu-%d-%u.log", timestamp, pid, suffix);
        if (size < 0 || (size_t)size >= capacity) { errno = ENAMETOOLONG; return -1; }
        int fd = openat(directory, name,
            O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
        if (fd >= 0 || errno != EEXIST) return fd;
    }
    errno = EEXIST;
    return -1;
}
#endif
