# Login and OD: single-core b versus eight-core c

The eight-core capture has materially passed the earlier OD delay: all 28
fully correlated resolver requests return a result, with no captured 60-second
timeout. This does not establish an authenticated desktop session. WindowServer
submits its first full-screen surface, so its earlier no-client idle-exit
failure is not reproduced in this retained capture.

Inputs and computed correlation are recorded in
`out/nabu-mount-loop/od-b-c-comparison.json`:

| Measurement | b, `cpus=1` | c, `cpus=8` |
| --- | ---: | ---: |
| Completed correlated kauth requests | 24 | 28 |
| GETWORK to RESULT median | 4620.391 ms | 5.115 ms |
| GETWORK to RESULT maximum | 255981.773 ms | 16456.324 ms |
| Requester 60-second timeout events | 6 | 0 |
| Captured OD timed-wait returns | 94 | 118 |
| OD timed-wait median | 6148.171 ms | 45.330 ms |
| OD timed-wait maximum | 33377.207 ms | 5514.990 ms |

Every captured c OD timed-wait return is success. Both captures publish
numer/denom 625/12. b's actual approximately 30-second expired waits return 49
after approximately 30 seconds; this is not evidence of a 19.2 MHz conversion
error. Neither capture reproduces the old PlistFile first-connection assertion.

In b, requests 31337–31340 are collected promptly at 386–438 seconds, then
return together at 600.970–602.302 seconds, after their requesters have timed
out. This supports a shared initialization/queue delay, rather than missed
collection. It does not identify the delayed function. c removes that observed
failure shape without changing OD's binary, timeout or assertion.

## Real loginwindow ordering

The original 25F71 loginwindow ARM64E binary was read directly; static artifacts
are `out/nabu-mount-loop/loginwindow-startup.disasm` and
`loginwindow-methods.json`. Main at `0x1000061b0` calls
`sharedLoginPortJuggler` at `0x100006624`. Its `-[LoginPortJuggler init]`
at `0x1000078b8` executes the following calls in order:

1. `SessionCreate(0, 0x30)` at `0x1000079ec`.
2. `_load_launchd_jobs_at_loginwindow_prompt(0)` at `0x100007ac0`.
3. `SessionGetInfo(-1, ...)` at `0x100007b34`, then session object queries.
4. `_CGSMainConnectionID` at `0x100007e44`.
5. `_CGSSetLoginwindowConnection` at `0x100007ec0`.

These precede main's SetupDone/LanguageChooser checks and
`NSApplication sharedApplication/run` at `0x10000700c/0x1000070c0`.
Consequently an account-query prerequisite must not be assumed to precede
the very first CGS connection. SetupDone is not established as this failure's
cause.

Original Security framework code independently resolves SessionCreate at
`0x183fa6e4c`. It creates a 48-byte auditinfo_addr, calls `setaudit_addr` at
`0x183fa6ee4`, reads the audit info back and sets `SecuritySessionId` in the
environment. It **does not send a securityd Mach RPC or call AKS**. SessionGetInfo
uses audit-info syscalls too. Static artifacts are
`out/nabu-mount-loop/security-session.disasm` and `security-symbols.json`.
An AKS keybag-stub failure is therefore not a demonstrated explanation for
SessionCreate stalling. The actual following launchd job/session/CGS calls
remain possible boundaries until a live stack or completed-call evidence
identifies one.

## Specific misleading messages excluded

`Failed to handle the new encryption key. Error 0xe00002d8` originates in
`bsd/kern/sys_generic.c:sysctl_coredump_encryption_key_update`, after
`IOProvideCoreFileAccess(kdp_core_handle_new_encryption_key, ...)` fails. It
concerns the core-dump encryption public key, not AKS wrapping, account keybags
or SessionCreate. It also occurs in c, which proceeds to its first swap.

In c at 253.094 seconds, loginwindow's msgid 2802 is IOKit's
`io_iterator_next`, as established by subsystem 2800 and the third routine in
`osfmk/device/device.defs`. The destination is zero and the return is
`MACH_SEND_INVALID_DEST` (0x10000003). Following 0x10004006 returns mean
`MACH_RCV_PORT_CHANGED`, not `MACH_RCV_PORT_DIED`. The two earlier
0x10004004 returns mean `MACH_RCV_TOO_LARGE`, not a timeout. These observations
alone do not establish failed CGS creation.

The existing general IPC entry budget is shared by OD and loginwindow.
c retains 52 OD IPC entries and zero loginwindow IPC entries: early OD consumes
the 128-record success/entry channel. Successful loginwindow RPCs being absent
from this log therefore does not show they never ran. The error channel still
captures later errors; only errors cannot reconstruct successful progression.

## What c establishes

c lines 1956–1957 record `first swap submitted` and a BGRA full-screen surface
with source/destination `(0,0,1600,2560)` and `updated=1`. The retained log has
no WindowServer exit or original OD assertion. Account authentication, Finder,
Dock and a visible completed scanout are not established by these two lines.
The remaining display copy/completion investigation should use c's actual
first-surface progress, and should not continue treating b's kauth timeouts as
an unresolved fact in c.

No speculative AKS selector, global timeout, audit bypass or binary assertion
change was made during this audit.
