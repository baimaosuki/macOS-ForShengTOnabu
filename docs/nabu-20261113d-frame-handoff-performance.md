# NABU d: frame handoff and boot overhead

Input: out/logs/xnu-20261113d/XNU2LOG.BIN. Standard CRC-valid decoder selects
slot 1 sequence 6366, payload 192077 bytes, 276 contended bytes lost, no ring
overwrite. Supplied texts contain the previous 191920-byte snapshot. Preserve
all original input files; verified-boot.txt and verified-info.json are derived.

## Display evidence and fix

Full-screen layer 0 reaches source-map-ready and source-copy-done for BGRA,
1600x2560, stride6400, 16384000 bytes. Thus the previous direct-map change
actually advances through all 2560 source rows. There is no subsequent frame
or native completion record. This alone cannot distinguish the former local
mapping destructor, subsequent planes, framebuffer ownership, console handoff
or final scanout. The SHENG synchronous map/copy/release path shows that
release inside a gate is not inherently invalid. No unique deadlock is claimed.

This revision keeps one strong source mapping per plane, reuses unchanged
backing, and retires replaced/stopped maps through an independent bounded
worker. It retains neither borrowed IOSurface pointers nor the display object.
Retirement queue capacity is eight, with four reserved for stop. A full queue
preserves old ownership and refuses replacement instead of leaking or falsely
completing. Actual presenter/worker host tests block unmap and exercise queue
capacity, stop, final release, failure retirement and panic-safe handoff.

Native vcputc ignores the diagnostic framebuffer owner. Disabling it after
first scanout writes allowed it to overwrite that image. First handoff now
drains/disables native console after staging and GUI claim, before the first
GOP store. Failed first copy restores console only if panic has not taken
ownership. Stage logs distinguish source return, planes, claim, console drain,
scanout copy and native completion. These changes require device validation.

## Boot overhead changes

The diagnostic tools cause 42 of 142 visible shared-cache unnest events, 29.6%
of events, not measured CPU time. Three duration2/10ms samplers each hit the
15-second external deadline without outputting stacks. Probe v4 takes a single
round, samples duration0 (single snapshot) without mayDie symbol preload, uses
ps ucomm instead of executable-path resolution and halves poll frequency.
Absent user-session lsd no longer keeps pre-login polling alive; observed
launchservicesd is an optional target. Guard deadlines remain bounded.
The matching universal sample file's x86 text confirms duration0 and mayDie
semantics; its arm64e slice was not recovered, and on-device stacks remain
unverified. No -e flag is used: it opens TextEdit, not raw output.

UFS flash and sparse RAM were both exposed with isssd=0. The actual storage
device publication now sets Device Characteristics / Medium Type to Solid
State before driver matching, preserving existing keys and making no new
TRIM/FUA/flush promises. Tests execute the real helper/start calls and original
IOBlockStorageDriver/IOMediaBSDClient consumers. This corrects medium policy;
it does not promise a quantified reduction in every boot stage.

Dirs_cleaner EPERM is still real but its observed task-start interval is only
0.311 seconds; it is not the minute-scale stall. Raw service starts are not
proof that a specific service blocks for the full inter-start interval.
No device, Data/root image or original log is modified by this build.
