# NABU 25F71 watchdogd startup policy

Device kernel UUID 2F3C1EB9-0AF9-3202-B003-68EA7119B062 reaches userland, then panics because watchdogd exits with OS_REASON_SIGNAL / SIGTRAP (namespace 2, subcode 5). The reported ESR is a later SVC64 context, not an original watchdogd BRK PC. This photograph does not establish the exact watchdogd assertion site or prove /Search recovered.

Original 25F71 watchdogd connects to an IOWatchdog service. Its actual connection instructions were replayed with only IOKit service lookup, VM query and logging modeled. Missing service on a non-VM path reaches __os_crash with `connect_to_kext() unexpectedly failed - unable to lookup watchdog KEXT service`; a VM takes a separate clean-exit path. The native/prelinked NABU closure does not provide the hardware watchdog service. This is a concrete unsupported interface; the original user trap PC is still needed to prove that exact device assertion.

The sheng macOS 27.3 board profile explicitly requests disable-watchdogd=1. Its pinned package/BootWatchdogPolicy.c (UniXNU-sheng 81f43ebaaa3317a5917b76093f7bc44c4949bc02) writes launchd's root disabled.plist and disabled.migrated before launchd. It does not implement a hardware watchdog or inject wdt=-1. The relevant mainline policy is reused for the same temporary bring-up purpose on NABU 26.5.

The new 16-macos-watchdog-policy.patch installs only a com.apple.watchdogd boolean true override, explicitly gated by disable-watchdogd=1, after checked writable Data/firmlinks/boot shims and before launchd starts. Existing policy or migration files are refused. Exclusive creation, root:wheel ownership, modes, filesystem attribute support, fsync and full content readback are checked; the migration marker is created last. Failures propagate through the existing checked Data startup path. Partial failures can leave a policy file; no later run overwrites it. The current volatile Data seed resets at each boot.

The original Data image has a root:wheel 0755 launchd directory, with neither policy nor migration marker present. No root image, Data seed, original Apple signed executable or partition layout needs to change. This is an explicit launchd bring-up policy: watchdogd is disabled until a real compatible watchdog backend is provided. Kernel panic handling and other jobs remain intact.

Pair the new kernel with the generated configuration containing disable-watchdogd=1 and exception_log_max_pid=255. The latter is an existing DEVELOPMENT exception-triage log and records early process exception codes and original user PCs without adding a new fault hook. It does not suppress or recover faults.

Evidence: out/nabu-watchdog-fix/connect-machine-code.json, data-policy-list.txt, launchd-policy-loader.txt and the original daemon/plist. Device startup of this new candidate remains unverified.
