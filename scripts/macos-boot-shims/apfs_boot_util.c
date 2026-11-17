// SPDX-License-Identifier: MIT
// Owner replacement of apfs.fs/Contents/Resources/apfs_boot_util for a macOS root on a read-only container.
//
// launchd's boot tasks mount-phase-1 and mount-phase-2 (RequireSuccess) run `mount -P N`, which runs
// apfs_boot_util N to mount the role volumes of the root container: VM (created when missing), Preboot, Update,
// xART, Hardware. Here the root container is the unchanged, owner-verified OS image: it holds the System volume
// only and cannot be written, so creating the VM volume fails and launchd stops the boot. The kernel mounts the
// Data volume itself (/System/Volumes/Data) and shows the OS cryptex at /System/Volumes/Preboot/Cryptexes/OS;
// the other role volumes do not exist on this layout, and the system runs without swap. Every phase therefore
// has nothing to mount and succeeds. The kernel shows this file over the original only with apfs-boot-shim=1.
#include <stdio.h>
#include "nabu_session_observer.h"

int
main(int argc, char **argv)
{
	if (argc == 2 && !strcmp(argv[1], "--nabu-session-watch"))
		return nabu_session_watch();
	fprintf(stderr, "apfs_boot_util (owner): phase %s: Data is mounted by the kernel; the read-only root container "
	    "has no other role volumes\n", argc > 1 ? argv[1] : "?");
	if (argc == 2) nabu_session_start(argv[1]);
	return 0;
}
