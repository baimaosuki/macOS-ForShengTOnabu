/* SPDX-License-Identifier: MIT */
#include <sys/sysctl.h>
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
    const char *directory = "/System/Volumes/Data/Library/LaunchDaemons";
    const char *plist = "/System/Volumes/Data/Library/LaunchDaemons/org.nabu.session-probe.plist";
    static const char xml[] =
        "<?xml version=\"1.0\"?><plist version=\"1.0\"><dict>"
        "<key>Label</key><string>org.nabu.session-probe</string>"
        "<key>ProgramArguments</key><array><string>/bin/sh</string>"
        "<string>/private/var/db/nabu-session-probe.sh</string></array>"
        "<key>RunAtLoad</key><true/>"
        "<key>StandardOutPath</key><string>/dev/console</string>"
        "<key>StandardErrorPath</key><string>/dev/console</string>"
        "<key>ProcessType</key><string>Background</string>"
        "</dict></plist>";
    if (!nabu_session_probe_enabled()) return;
    int error = 0;
    if (mkdir(directory, 0755) != 0 && errno != EEXIST) error = errno;
    if (!error) error = nabu_probe_write(script, nabu_session_probe_script, 0600);
    if (!error) error = nabu_probe_write(plist, xml, 0644);
    fprintf(stderr, "apfs_boot_util (owner): session stack probe installation status=%d\n", error);
}
