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

#ifdef __APPLE__
static int
skip_od_overlay(const char *operation, const char *path, int error)
{
    fprintf(stderr, "apfs_boot_util (owner): optional OD overlay inactive: %s %s: errno=%d (%s); namespace unchanged, continuing boot\n",
        operation, path, error, strerror(error));
    return 0;
}

static int
verify_od_namespace(const struct stat *original, const struct stat *lower,
    const struct statfs *baseline)
{
    struct stat current, current_lower;
    struct statfs filesystem;
    if (stat("/usr/libexec/opendirectoryd", &current) == -1 ||
        stat("/usr/libexec/xpcproxy", &current_lower) == -1 ||
        statfs("/usr/libexec", &filesystem) == -1)
        return errno;
    if (current.st_dev != original->st_dev || current.st_ino != original->st_ino ||
        current_lower.st_dev != lower->st_dev || current_lower.st_ino != lower->st_ino ||
        filesystem.f_flags != baseline->f_flags ||
        strcmp(filesystem.f_fstypename, baseline->f_fstypename) != 0 ||
        strcmp(filesystem.f_mntonname, baseline->f_mntonname) != 0 ||
        strcmp(filesystem.f_mntfromname, baseline->f_mntfromname) != 0)
        return EIO;
    return 0;
}

static int
same_file_bytes(const char *left, const char *right)
{
    unsigned char a[8192], b[8192];
    int l = open(left, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    int r = open(right, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    int error = 0;
    if (l == -1 || r == -1) {
        error = errno;
    } else {
        for (;;) {
            ssize_t na, nb;
            do { na = read(l, a, sizeof(a)); } while (na == -1 && errno == EINTR);
            if (na == -1) { error = errno; break; }
            do { nb = read(r, b, sizeof(b)); } while (nb == -1 && errno == EINTR);
            if (nb == -1) { error = errno; break; }
            if (na != nb || (na > 0 && memcmp(a, b, (size_t)na) != 0)) {
                error = EIO; break;
            }
            if (na == 0) break;
        }
    }
    if (l != -1) close(l);
    if (r != -1) close(r);
    return error;
}

static int
prepare_od_overlay(void)
{
    const char *source = "/private/var/ngpu/od";
    const char *binary = "/private/var/ngpu/od/opendirectoryd";
    const char *point = "/usr/libexec";
    const char *visible = "/usr/libexec/opendirectoryd";
    const char *lower = "/usr/libexec/xpcproxy";
    struct stat directory, file, before, after, original;
    struct statfs filesystem, baseline;
    int mounted = 0, error;

    // A missing candidate retains the original service for older addon bundles.
    // Any present candidate must be complete, root-owned and immutable to users.
    if (lstat(source, &directory) == -1)
        return skip_od_overlay("stat OD source", source, errno);
    if (!S_ISDIR(directory.st_mode) || directory.st_uid != 0 || directory.st_gid != 0 ||
        (directory.st_mode & 0022) != 0)
        return skip_od_overlay("validate OD source ownership", source, EPERM);
    if (lstat(binary, &file) == -1)
        return skip_od_overlay("stat OD candidate", binary, errno);
    if (!S_ISREG(file.st_mode) || file.st_uid != 0 || file.st_gid != 0 ||
        (file.st_mode & 0022) != 0 || (file.st_mode & 0111) == 0 || file.st_size <= 0)
        return skip_od_overlay("validate OD candidate ownership", binary, EPERM);
    if (stat(lower, &before) == -1)
        return skip_od_overlay("lookup lower xpcproxy", lower, errno);
    if (statfs(point, &filesystem) == -1)
        return skip_od_overlay("statfs OD mountpoint", point, errno);
    if (strcmp(filesystem.f_fstypename, "bindfs") == 0 &&
        strcmp(filesystem.f_mntonname, point) == 0) {
        if (strcmp(filesystem.f_mntfromname, source) != 0 ||
            (filesystem.f_flags & (MNT_RDONLY | MNT_UNION)) != (MNT_RDONLY | MNT_UNION))
            return skip_od_overlay("unexpected OD overlay", point, EBUSY);
    } else {
        // MNT_UNION is essential: fallback lookups must still see every sealed
        // /usr/libexec tool. A plain bind mount would hide xpcproxy and others.
        if (stat(visible, &original) == -1)
            return skip_od_overlay("lookup original OD", visible, errno);
        baseline = filesystem;
        if (mount("bindfs", point, MNT_RDONLY | MNT_UNION, (void *)source) == -1) {
            int mount_error = errno;
            error = verify_od_namespace(&original, &before, &baseline);
            if (error != 0)
                return failure("OD mount rejected but namespace differs", point, error);
            return skip_od_overlay("mount OD union rejected; original OD retained", point, mount_error);
        }
        mounted = 1;
    }
    error = same_file_bytes(binary, visible);
    if (error == 0 && stat(lower, &after) == -1)
        error = errno;
    if (error == 0 && (before.st_dev != after.st_dev || before.st_ino != after.st_ino))
        error = EXDEV;
    if (error == 0 && (statfs(point, &filesystem) == -1 ||
        strcmp(filesystem.f_fstypename, "bindfs") != 0 ||
        strcmp(filesystem.f_mntfromname, source) != 0 ||
        strcmp(filesystem.f_mntonname, point) != 0 ||
        (filesystem.f_flags & (MNT_RDONLY | MNT_UNION)) != (MNT_RDONLY | MNT_UNION)))
        error = EIO;
    if (error != 0) {
        int validation_error = error;
        // Only the exact owner source passed the existing-mount check above.
        // If this mount existed on entry, remove that failed owner view too.
        if (unmount(point, 0) == -1)
            return failure("rollback OD union", point, errno);
        if (mounted && (error = verify_od_namespace(&original, &before, &baseline)) != 0)
            return failure("OD rollback namespace differs", point, error);
        fprintf(stderr, "apfs_boot_util (owner): optional OD overlay inactive: validation errno=%d (%s); rollback succeeded, original OD retained, continuing boot\n",
            validation_error, strerror(validation_error));
        return 0;
    }
    fprintf(stderr, "apfs_boot_util (owner): OD readonly union ready: replacement bytes match; lower xpcproxy preserved\n");
    return 0;
}
#endif

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
#ifdef __APPLE__
    if (prepare_od_overlay() != 0)
        return 1;
#endif
    fprintf(stderr, "apfs_boot_util (owner): phase %s ready: writable Data and VM device=%llu\n",
        argv[1], (unsigned long long)data_status.st_dev);
    return 0;
}
