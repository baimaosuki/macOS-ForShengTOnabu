// SPDX-License-Identifier: MIT
// Owner DataRAM layout adapter, exposed over apfs_boot_util by the boot shim.
// The immutable System container has no role volumes to create. Both required
// mount phases must nevertheless prove that the kernel's Data and VM are usable.
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#ifdef __APPLE__
#include <sys/mount.h>
#else
#include <sys/statvfs.h>
#include <sys/vfs.h>
#ifndef NABU_BOOT_SHIM_HOST_TEST
#error "Non-Darwin builds are supported only for host logic tests"
#endif
#endif

static int
failure(const char *operation, const char *path, int error)
{
    fprintf(stderr, "apfs_boot_util (owner): %s %s: errno=%d (%s)\n",
        operation, path, error, strerror(error));
    return 1;
}

static int
probe_writable(const char *path, dev_t device)
{
    struct stat status;
    char name[96];
    const char payload[] = "nabu DataRAM boot layout probe\n";
    int directory, file = -1, error = 0;

    directory = open(path, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (directory == -1)
        return failure("open directory", path, errno);
    if (fstat(directory, &status) == -1)
        error = errno;
    else if (status.st_dev != device || !S_ISDIR(status.st_mode))
        error = EXDEV;
    if (error != 0) {
        close(directory);
        return failure("validate directory", path, error);
    }

    for (unsigned attempt = 0; attempt < 16; ++attempt) {
        snprintf(name, sizeof(name), ".nabu-apfs-boot-probe-%ld-%u",
            (long)getpid(), attempt);
        file = openat(directory, name,
            O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
        if (file != -1 || errno != EEXIST)
            break;
    }
    if (file == -1) {
        error = errno;
        close(directory);
        return failure("create exclusive probe", path, error);
    }
    if (fstat(file, &status) == -1)
        error = errno;
    else if (status.st_dev != device)
        error = EXDEV;
    for (size_t written = 0; error == 0 && written < sizeof(payload) - 1;) {
        ssize_t count = write(file, payload + written, sizeof(payload) - 1 - written);
        if (count == -1 && errno == EINTR)
            continue;
        if (count <= 0) {
            error = count == 0 ? EIO : errno;
            break;
        }
        written += (size_t)count;
    }
    if (error == 0)
        fprintf(stderr, "apfs_boot_util (owner): probe write %s/%s ok\n", path, name);
    if (close(file) == -1) {
        if (error == 0)
            error = errno;
    } else {
        fprintf(stderr, "apfs_boot_util (owner): probe close %s/%s ok\n", path, name);
    }
    if (unlinkat(directory, name, 0) == -1) {
        if (error == 0)
            error = errno;
    } else {
        fprintf(stderr, "apfs_boot_util (owner): probe unlink %s/%s ok\n", path, name);
    }
    if (close(directory) == -1 && error == 0)
        error = errno;
    return error == 0 ? 0 : failure("writable probe", path, error);
}

int
main(int argc, char **argv)
{
    const char *data = "/System/Volumes/Data";
    const char *vm = "/System/Volumes/VM";
    struct stat data_status, vm_status;
    struct statfs filesystem;

    if (argc != 2 || (strcmp(argv[1], "1") != 0 && strcmp(argv[1], "2") != 0))
        return failure("unsupported mount phase", argc > 1 ? argv[1] : "(missing)", EINVAL);
#ifdef NABU_BOOT_SHIM_HOST_TEST
    // Only host tests can redirect the paths; a shipping binary never reads these.
    data = getenv("NABU_BOOT_TEST_DATA");
    vm = getenv("NABU_BOOT_TEST_VM");
    if (data == NULL || vm == NULL)
        return failure("missing host test paths", "environment", EINVAL);
#endif
    fprintf(stderr, "apfs_boot_util (owner): checking mount phase %s DataRAM layout\n", argv[1]);
    if (stat(data, &data_status) == -1)
        return failure("stat Data", data, errno);
    if (!S_ISDIR(data_status.st_mode))
        return failure("Data must be a directory", data, ENOTDIR);
    if (statfs(data, &filesystem) == -1)
        return failure("statfs Data", data, errno);
#ifdef __APPLE__
    if (strcmp(filesystem.f_fstypename, "apfs") != 0)
        return failure("Data must use APFS", data, ENODEV);
    if ((filesystem.f_flags & MNT_RDONLY) != 0)
#else
    if ((filesystem.f_flags & ST_RDONLY) != 0)
#endif
        return failure("Data filesystem is read-only", data, EROFS);
    if (stat(vm, &vm_status) == -1)
        return failure("stat VM", vm, errno);
    if (!S_ISDIR(vm_status.st_mode))
        return failure("VM must be a directory", vm, ENOTDIR);
    if (vm_status.st_dev != data_status.st_dev)
        return failure("VM must resolve to the Data filesystem", vm, EXDEV);
    if (probe_writable(data, data_status.st_dev) != 0 ||
        probe_writable(vm, data_status.st_dev) != 0)
        return 1;
    fprintf(stderr, "apfs_boot_util (owner): phase %s ready: writable Data and VM device=%llu\n",
        argv[1], (unsigned long long)data_status.st_dev);
    return 0;
}
