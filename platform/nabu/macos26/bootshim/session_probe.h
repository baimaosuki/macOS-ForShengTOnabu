/* SPDX-License-Identifier: MIT */
#include <sys/sysctl.h>
#include <spawn.h>
#include "nabu_session_probe_script.h"

static int
nabu_session_probe_enabled(void)
{
    char args[2048];
    size_t length = sizeof(args);
    const char *arg = "nabu-session-probe=1";
    if (sysctlbyname("kern.bootargs", args, &length, NULL, 0) != 0 ||
        length == 0 || length > sizeof(args)) return 0;
    args[length - 1] = '\0';
    for (char *p = args; (p = strstr(p, arg)) != NULL; ++p) {
        size_t n = strlen(arg);
        if ((p == args || p[-1] == ' ') && (p[n] == '\0' || p[n] == ' ')) return 1;
    }
    return 0;
}

static int
nabu_probe_write(const char *path, const char *contents, mode_t mode)
{
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW | O_CLOEXEC, mode);
    if (fd < 0) return errno;
    size_t remaining = strlen(contents);
    int error = 0;
    while (remaining) {
        ssize_t n = write(fd, contents, remaining);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) { error = n == 0 ? EIO : errno; break; }
        remaining -= (size_t)n;
        contents += n;
    }
    if (close(fd) != 0 && error == 0) error = errno;
    return error;
}

static void
nabu_install_session_probe(void)
{
    const char *script = "/System/Volumes/Data/private/var/db/nabu-session-probe.sh";
    if (!nabu_session_probe_enabled()) return;
    const char *reports = "/System/Volumes/Data/private/var/tmp/nabu-session-probe";
    struct stat directory;
    if ((mkdir(reports, 0700) != 0 && errno != EEXIST) ||
        lstat(reports, &directory) != 0 || !S_ISDIR(directory.st_mode) ||
        directory.st_uid != 0 || (directory.st_mode & 077) != 0) {
        fprintf(stderr, "apfs_boot_util (owner): session probe private report directory invalid errno=%d\n", errno);
        return;
    }
    int error = nabu_probe_write(script, nabu_session_probe_script, 0600);
    fprintf(stderr, "apfs_boot_util (owner): session probe script status=%d\n", error);
    if (error) return;
    /* A plist on Data is not evidence that launchd loaded or ran it. Start
     * directly, with a new session and explicit independent console handles. */
    posix_spawn_file_actions_t actions;
    posix_spawnattr_t attributes;
    error = posix_spawn_file_actions_init(&actions);
    if (error) {
        fprintf(stderr, "apfs_boot_util (owner): session probe actions status=%d\n", error);
        return;
    }
    error = posix_spawnattr_init(&attributes);
    if (error) {
        posix_spawn_file_actions_destroy(&actions);
        fprintf(stderr, "apfs_boot_util (owner): session probe attributes status=%d\n", error);
        return;
    }
    error = posix_spawnattr_setflags(&attributes, POSIX_SPAWN_SETSID);
    if (!error) error = posix_spawn_file_actions_addopen(&actions, 0, "/dev/null", O_RDONLY, 0);
    if (!error) error = posix_spawn_file_actions_addopen(&actions, 1, "/dev/console", O_WRONLY, 0);
    if (!error) error = posix_spawn_file_actions_addopen(&actions, 2, "/dev/console", O_WRONLY, 0);
    pid_t pid = -1;
    char *arguments[] = {"/bin/sh", "/private/var/db/nabu-session-probe.sh", NULL};
    char *environment[] = {"PATH=/usr/bin:/bin:/usr/sbin:/sbin", "HOME=/var/root", NULL};
    if (!error) error = posix_spawn(&pid, "/bin/sh", &actions, &attributes, arguments, environment);
    posix_spawnattr_destroy(&attributes);
    posix_spawn_file_actions_destroy(&actions);
    fprintf(stderr, "apfs_boot_util (owner): session probe direct spawn pid=%ld status=%d (%s)\n",
        (long)pid, error, error ? strerror(error) : "started; await child marker");
}
