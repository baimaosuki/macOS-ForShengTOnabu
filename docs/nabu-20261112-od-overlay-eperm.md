# Optional OD overlay: EPERM failure and boot fallback

The first device attempt failed before OD could run the experimental queue-fence
binary. The owner shim made a refused optional overlay mount fatal to required
mount phase 1. This was a boot-shim failure, not evidence that the OD machine-code
adaptation ran or repaired anything.

Evidence: `/home/baimao/p1.txt`, SHA-256
`4636d1ebb861fbfc1f8bc7fe0f105fc849bf78ba6d4fc635fd854f01988a89bd`.
Lines 872–879 show successful Data/VM write, close and unlink probes. Line 880
reports `mount OD union /usr/libexec: errno=1 (Operation not permitted)`. Launchd
then reports mount-phase-1 exit 1 and plans to panic. No successful OD overlay
validation message appears. The original root/Data images do not need rebuilding
to correct this shim failure policy.

## Corrected optional behavior

`apfs_boot_util.c` now treats the OD adaptation as optional while retaining the
required Data/VM checks:

- Input, ownership or other pre-mount checks fail: log `optional OD overlay
  inactive`, make no namespace mutation, and continue boot.
- `mount()` is refused: compare the original OD and lower xpcproxy device/inode
  plus mount source, target, type and flags with the pre-call snapshot. If
  unchanged, explicitly log `original OD retained` and continue with success.
- The new mount succeeds but replacement/lower lookup validation fails: unmount
  it, verify the original snapshot, explicitly log rollback and continue.
- Unmount fails, or a failed mount/rollback unexpectedly leaves the namespace
  different: retain a fatal return. Continuing could hide system tools or run an
  unverified replacement, so it must not claim the original namespace survived.
- Only complete successful overlay validation reports `OD readonly union ready`.

Six overlay host tests compile the Darwin branch against a modeled mount API.
They cover the actual EPERM result, unchanged namespace, successful rollback,
rollback refusal, unexpected mutation, candidate checks, lower visibility and
idempotence. Seven existing boot-shim tests continue to verify required Data/VM
failures. These tests establish branch behavior only. They do not prove real
Darwin mount authorization, rollback or overlay success. A new macOS shim build
and a device boot must verify that EPERM no longer aborts mount phase 1 and that
the inactive log does not imply candidate activation.

## What EPERM establishes

The signed shim contains `com.apple.private.bindfs-allow`. That is a disk-signature
fact, not proof that every kernel authorization layer accepts the mount. The
existing bindfs implementation checks `IOCurrentTaskHasEntitlement` at
`bsd/miscfs/bindfs/bind_vfsops.c:140`. Normal userland `mount` calls
`__mac_mount`; it resolves the target with `OP_MOUNT`, checks the covered vnode,
and runs `mac_mount_check_mount` before bindfs admission and
`mac_mount_check_mount_late` afterward. A protected sealed-system directory or
runtime entitlement validation can therefore reject the operation independently
of the embedded XML. This log records only the final errno and cannot identify
which check returned it. It does not justify disabling rootless, MAC policy or
general mount authorization.

The `MNT_UNION` immediate-EPERM check in published `vfs_syscalls.c:430` belongs
to **fmount**, not the userland `mount` path used here. It is not a valid diagnosis
for this failure. The compiled kernel already supports native union mounts; the
existing prelaunchd addon overlays use them. No new entitlement guessing or
policy bypass was added as part of the fallback repair.

## Future prelaunchd mount plan, not implemented

Reuse the existing `native_gpu_addon_prepare` path after addon extraction and
Data setup, before launchd exec. Its checked `native_gpu_addon_mount` path already
mounts owner content with `MNT_RDONLY|MNT_UNION` and briefly scopes
`native_bindfs_boot_mount` around that one mount call. This bypasses bindfs's
early entitlement requirement for the known boot operation and enables root
ownership mapping; it does not disable general mount policy.

A narrow optional OD addition can use source `/private/var/ngpu/od`, target
`/usr/libexec`, required lower probe `/usr/libexec/xpcproxy`, and replacement
probe `/usr/libexec/opendirectoryd`, with dedicated stage/status evidence. Before
mounting it must validate the extracted candidate and preserve an original
namespace snapshot. After mounting it must establish both the replacement's
backing vnode identity/expected code identity and lower-tool visibility. A failed
check requires rollback and verification; a failed mount leaves the original
namespace and only records inactivity. A missing source skips the operation.

The generic existing helper performs presence probes but does not implement the
full identity/rollback contract; calling it alone must not be represented as a
complete checked OD mount. A follow-up can extend that contract specifically for
OD, keeping the existing successful addon paths unchanged, then compile it with
the next planned kernel build. No kernel change or extra kernel CI was started
for this plan. The current CPU kernel build receives no mid-build input change.
