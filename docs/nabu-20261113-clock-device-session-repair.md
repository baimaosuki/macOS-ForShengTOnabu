# Clock device result and session evidence repair

The new raw input is `out/nabu-logs-20261113/XNU2LOG-clock.BIN`, SHA256
`9c31c18d9c4fdf40a90d8ff83b761984541878f8d4fc1f6e783a6deea3b4cf1e`.
The decoder selects sequence 8048, 190172 committed bytes, one lost byte,
zero overwrite. The retained tail reaches 308.306 seconds. Original input and
the user-provided analysis are retained separately; hypotheses in that analysis
are checked against the actual 25F71 implementation before changing behavior.

## What this device run establishes

Frames 1, 2 and 3 again copy all 2560 rows and return from native completion.
They are black. At all four observed swap-info calls, native time, interval and
count are zero, **both type-3 and type-5 lists are empty**, and held=0. The
completion-only timer correction therefore had no subscriber to activate it in
this capture. It must not be described as having resolved this boot's stall.

Swap-info 4 at 287.385 s did not pass through the CPU pixel presenter. At
308.306 s, the software callback count reaches 14999, submitted remains 4,
shown remains 3, and both current request and queue head are null. This is
real new evidence, but not proof that a request remains pending forever.

The exact native `dropSwap_gated` at 0xfffffe000a89f2dc unlinks a queue entry,
sets current, invokes native `swap_complete_gated(bool)` at 0xa89f3b4, and clears
current at 0xa89f3b8. It can reach swap-info without entering the local pixel
presenter. `clean_queue_gated` invokes native obsolete/no-op/aborted/dummy
cleanup before local programming. Local shown counts actual scanout copies;
it must not be incremented for a request that did not copy pixels. Further
native swap-info dispatch call sites also require distinction. Do not manually
write an ID into current, clear pending work, or force shown=4 to match a
validation counter.

The new bounded queue records capture request ID, update mask, flags, reasons
before cleanup and current/queue/last-displayed-ID after each original cleanup
helper. No released request is read after the helper. This distinguishes native
retirement from a local presenter path and missing completion.

## Fixing the observation failures

Probe v5 replaces the 15-second sample guard with 120 seconds and retains full
reports in `/private/var/tmp/nabu-session-probe/`. It does not use `-mayDie`,
which the actual tool documents as preloading symbols, not disabling them.
The first attempt to inspect sample used a truncated fat binary; the complete
175104-byte original and actual ARM64E slice have now been extracted and checked.
No unsupported `-noSymbols` flag is invented.

The same signed boot shim now exposes a read-only raw-stack diagnostic mode.
It discovers real PIDs, takes an initial and a delayed snapshot for OD,
loginwindow and WindowServer, enumerates real threads, and prints registers,
bounded frame-pointer walks, dyld cache UUID/slide and image UUIDs. It requests
task read rights using the read/read.safe entitlements present in the original
sample tool. MAC checks remain. Failed APIs, live-state races and truncation
are reported rather than claiming a complete stack. Raw records are captured
directly into RAM independently of the disabled console; complete reports
remain files, with a bounded explicitly marked mirror into the journal.

The IPC observer now includes WindowServer, resolves destination ports through
the exact native locked receiver helper, and logs PID/name snapshots only after
releasing locks. It keeps bounded call associations across receive continuation.
Per-process and per-error budgets prevent OD from consuming the login/display
budget; late events remain sampled rather than silently stopping before 250 s.
Unknown, busy, kernel-owned and receive-only cases stay explicit. It does not
invent a bootstrap service name from a port number. `0x10004004` is TOO_LARGE,
not timeout; timeout is `0x10004003`.

The display audit records every plane on the first frame, including clear,
absent and zero-area entries, and records nonblack/alpha statistics for later
black frames on a bounded cadence (first eight/every fiftieth). Surface IDs
come from the original exported IOSurface getter at 0xfffffe000aabc890.
Statistics observe BGRA before forced opaque alpha; RGhA statistics describe
the converted 8-bit source. Failure paths also report plane state. Existing
four-plane traversal, cursor alpha, persistent layers and native completion
are preserved. Tests cover root+cursor+plane2, subsequent explicit clear,
disabled planes and first-frame rejection without inventing a shown frame.

## Reference and delivery boundary

SHENG27.3 display guide and native source revisions remain those recorded in
`nabu-20261113-vsync-device-completion-clock.md`; the user iOS16 reference uses
real source copies, native ownership or its own explicit simplified ABI, and
raw stack collection. Its always-success SwapWait cannot replace 25F71's
different original buffer-ownership contract.

This revision repairs known diagnostic failures and preserves the verified
clock correction. It is not a demonstrated fix for the final login-session
blocker. Native compile, signed payload, trustcache and source hashes must all
be checked before delivery, and the device must provide real stacks and peer
records before a blocked service can be named and repaired with confidence.
