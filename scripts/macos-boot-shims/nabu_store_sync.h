/* SPDX-License-Identifier: MIT
 * Flush an actual store, without importing machine-bound caches. Size is a sync
 * trigger, not proof that LaunchServices construction has completed.
 */
#ifndef NABU_STORE_SYNC_H
#define NABU_STORE_SYNC_H
#include <errno.h>
#include <dirent.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#include <string.h>
static int nabu_store_sync(const char *path)
{
    int fd = open(path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return 0;
    DIR *directory = fdopendir(fd);
    if (!directory) { close(fd); return 0; }
    struct dirent *entry;
    unsigned scanned = 0;
    int flushed = 0;
    while (scanned++ < 4096 && (entry = readdir(directory))) {
        size_t length = strlen(entry->d_name);
        if (length < 8 || strcmp(entry->d_name + length - 8, ".csstore")) continue;
        int file = openat(fd, entry->d_name, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
        if (file < 0) continue;
        struct stat status;
        if (!fstat(file, &status) && S_ISREG(status.st_mode) && status.st_size >= 4 * 1024 * 1024) {
            int result = fsync(file);
            int error = result ? errno : 0;
            nabu_raw_printf("NABU-RAWSTACK store-sync file=%.120s bytes=%lld fsync_result=%d errno=%d complete_not_inferred=1\n",
                entry->d_name, (long long)status.st_size, result, error);
            if (!result) flushed = 1;
        }
        close(file);
    }
    if (flushed) {
        sync(); sleep(2); sync();
        nabu_raw_printf("NABU-RAWSTACK store-sync flushes=2 clean_unmount=0\n");
    }
    closedir(directory);
    return flushed;
}
#endif
