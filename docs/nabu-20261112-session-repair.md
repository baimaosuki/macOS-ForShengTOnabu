# 25F71 session startup repair

This implements the five items approved after the 19:31 journal review. The
input remains macOS 26.5 / 25F71 and the existing three verified UFS windows.
SHENG references are pinned in `nabu-20261112-sheng273-repair-plan.md`.

## Changes

1. `zzzzzzz-nabu-session-time.patch` fixes the actual ARM microsecond converter.
   After removing whole seconds it multiplies the remainder by one million
   before dividing by the full frequency. The 19.2 MHz clock no longer yields
   a microsecond field above 999999 or moves backward at a second boundary.
   `mach_absolute_time`, counter frequency and nanosecond conversion keep their
   existing contracts.
2. The same patch sets an invalid/older boot calendar to the recorded UTC build
   epoch in `inittodr`, through `setthetime`. A later valid platform time is
   preserved. `SOURCE_DATE_EPOCH` can supply a reproducible timestamp; otherwise
   the build records its current UTC epoch. This is a calendar fallback, not a
   PMIC RTC or date persistence implementation.
3. `nabu-session-log=1` adds bounded, RAM-only `NABU-SESSION` events for kauth
   register, submit, GETWORK, RESULT and wake. Sequence numbers connect the
   requester and resolver; uid/gid, query flags, queue state, result and error
   distinguish uncollected work from missing completion. loginwindow/OD IPC
   records thread IDs, message IDs, ports, options and receive/send outcomes.
   A receive completion hook also observes continuation-based receive results.
   The existing live exit/exception context observer now includes loginwindow.
   These observations preserve original operation outcomes and do not themselves
   claim to fix an unidentified OD user-space wait.
4. The Ethernet identity worker waits asynchronously for IONetworkStack's actual
   BSD Name, then re-registers the interface once. Original 25F71 TimeSync's
   `gIOMatchedNotification` callback can then consume it after its earlier
   no-name return. No invented BSD name, carrier or clock state is published.
   The GOP adapter reports bounded VBL, power, queue, submission, completed-frame
   and swap-wait counters even if no frames arrive. The original CPU compositor
   and verified swap completion implementation remain in the package.
5. `session-filesystem.patch` adapts SHENG's OS-independent union/firmlink fixes
   to the prepared 25F71 source: consistent root device/fsid across vnode,
   bulk and extended attributes, proc_info and fsid lists; MNT_ROOTFS in the
   visible root statfs; root union hidden from kext mount-attribute checks while
   libc retains MNT_UNION for merged directories; source-parent semantics for
   firmlink `..`; no final-component firmlink traversal for delete/rename;
   lookup of a missing name in a read-only upper layer through the lower layer;
   and rejection of a lower traversal that returns to the same mount. The
   owner root authorization check now reads the real mnt_flag for its union
   invariant. Per-thread bulk context is restored around nested callbacks.

The filesystem work does not import Darwin 26 MAC hook slots, disable all MAC
checks or modify APFS superblock roles. It is a concrete correction for missing
filesystem semantics, not proof that every observed EPERM shares that cause.
The display work closes a naming/rematch opportunity and improves real progress
reporting; AppleDisplayManager protocol compatibility and first frame still need
device evidence. No empty success service or fabricated swap completion is used.

## Validation and delivery

Fresh Linux prepare applied the full existing desktop series plus both new
patches in `out/nabu-session-repair/verify/source`. The source audit passed.
Host tests compile and execute the actual prepared microtime, calendar and
union-identity/visibility functions. They cover 86400002 counter samples,
calendar fallback versus a valid later RTC, and the distinct user-space and
kext root-mount views. Separate tests execute the actual observer and named
interface method, including absence of a name and preserved property ownership.
Compositor, GOP bounds, TimeSync binding, boot shim and existing Data/Hardware/VM
tests remain applicable. Native compilation/prelink and device outcomes are
recorded separately below when available.

`package_nabu_services.py --cpu-compositor --windowserver-log --session-repair`
checks compiled observer/filesystem hashes, enables the session log, and selects
bounded stdout/stderr for the boot filesystem and session processes. It uses the
same Data image, stable platform UUID, root policy and journal token as the
installed baseline. The candidate replaces the four kernel sidecar/payload files
and TOML, preserving the existing ESP journal allocation.

## Device acceptance

The next boot must show a sane calendar, no impossible APFS duration, completed
identity lookup sequences, and the native named-interface rematch. A registered
network identity alone is not a TimeSync-ready result. WindowServer's clients
must establish a connection and the panel must receive a genuine userland frame
with completion before desktop presentation is claimed. Dock/Finder and input
are subsequent session checkpoints.
