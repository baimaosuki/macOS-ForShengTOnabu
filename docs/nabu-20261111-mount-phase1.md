# NABU 25F71 mount-phase-1 on an immutable root container

Device kernel UUID D3E35924-ACA5-3D53-A7BA-56C32CF9B682 has reached launchd
boot tasks. Photo codex-clipboard-7d2bf256-5771-4e2f-ada2-a921b0d32f7c.png
shows original apfs_boot_util trying to create the missing VM role volume in
the read-only System container. Creation fails EROFS (errno30), mount fails,
and the RequireSuccess mount-phase-1 task aborts, causing userspace panic.

The working sheng mainline has two separate adaptations for that layout:
macos-vm-volume.patch provides /System/Volumes/VM -> /private/var/vm on Data;
macos-apfs-boot-shim.patch exposes an owner-built apfs_boot_util through a
read-only union over apfs.fs/Contents/Resources. The original sealed image
and neighboring tools remain intact. Those pieces were absent here.

This port adds the VM firmlink and checks every error before launchd, then
installs the opt-in apfs-boot-shim=1 tool through the existing addon handoff.
The mount is attempted only after Data and VM preparation succeed, the
source tool exists, and addon installation reports no failures. Missing
inputs or union errors fail startup instead of masquerading as success.

The owner tool accepts phases1/2 and requires writable APFS Data, a VM
directory on that same device, and actual exclusive create/write/close/unlink
probes on both directories. This replaces the mainline's unconditional
success with checks of the adapted layout. It creates no fake role volumes.
The VM path is volatile because the current Data volume is a RAM seed;
persistent storage and desktop boot are not claimed by these host checks.

The tool is compiled for ordinary arm64-apple-macos26.5 in macOS CI, ad-hoc
signed and verified, and its actual CDHash becomes an additional owner
trust-cache module. The three manifest-verified original25F71 modules remain
byte-for-byte intact. One NGPUADD2 payload carries only bootshim/apfs_boot_util
via the existing loader GpuAddon interface; no GPU driver is included.

Tests cover checked kernel helper success/failure paths, missing/readonly/
cross-device Data/VM, write-probe failure, original8 arm64e compatibility
checks, and complete100-patch source preparation and audits. The delivery
must contain the new kernel, paired ADT, trustcache, addon, and boot config.
Root disk, Data seed, loader, and partition layout need no replacement.

Build result: CI42992038667 succeeded for ca38bc9b8dfb851d5d95e8b085dbf13bddb136ba.
The macOS-signed tool CDHash is0bb7c7dbd7e2e87006a1ec1ac079f5afa1ed0868;
its3 CodeDirectory pages verify. Independent final-kernel prelink verification
passes1000 pointers,20245 branches and1000 kernel bindings. All five ZIP
payload files read back correctly; the boot config differs from the previous
one only by the added apfs-boot-shim=1 argument. The ADT differs only by the
new zero GpuAddon handoff slot. Kernel UUID8452F068-9912-33F5-9FCB-50E9A2F146D2.
No device test has been performed for this candidate.
