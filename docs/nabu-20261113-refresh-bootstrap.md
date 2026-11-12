# NABU: display-on without a first refresh callback

Input: `out/nabu-logs-20261113/XNU2LOG.BIN`, SHA-256
`364fcc6e7176d0f63d86cb401153ca1975f87d8b71a335ee30f6279e4bb24346`.
The standard CRC decoder selects slot 1, sequence 6624, boot ID
`a69cb04f00000000df18000000000000`, 184686 payload bytes. The ring has not
wrapped; seven bytes were lost on contention. Preserve the supplied raw and
text files. `verified-boot.txt` and `verified-info.json` are derived outputs.
The supplied 184935-byte text includes binary header bytes and is not the
decoder-selected journal payload.

The log's build ID matches CI 43309271191's integrated desktop patch digest
`31c53b0f84833c2f5bafcc7df6c542f9cb4a4ffa5b93c247002a4418f0f21017`;
the kernel banner is dated 2026-11-12 21:34:23 UTC. Arguments include cpus=8,
native-gop-display=1, nabu-cpu-compositor=1 and nabu-session-log=1. Probe v4
and actual isssd=1 on UFS/RAM are visible, consistent with this delivery.

## What this capture proves

- WindowServer PID 165 executes its real image at 170.667 seconds;
  loginwindow PID 167 starts with console at 169.716 seconds.
- CPU compositor page 2 applies, NativeGOPDisplay reports display on, and
  page 1 applies. Page 0 is not recorded in this snapshot.
- There is no first swap, source mapping/copy, console-disable, scanout-copy,
  native-complete or NABU-DISPLAY heartbeat record. This capture does not reach
  the previously observed d-run source-copy boundary.
- Other kernel/session activity continues through 421.642 seconds. There is
  no recorded WindowServer exit-syscall, SIGABRT or kernel panic. Absence of
  selected exit records does not prove every service survived.
- 27 complete kauth results succeed, median 5.362ms, maximum 9.159 seconds;
  no requester timeout is recorded. Of 118 OD timedwait returns, one is
  ETIMEDOUT after 30.001 seconds. This is not the old 60-second kauth storm.
- The three main sample commands still reach their 15-second limit. The
  launchservicesd sample succeeds; launchctl print returns 141, Reentrancy
  avoided. No thread stacks for the main three targets were captured.
- endpointsecurityd again exits 35 after 65.697 seconds, while its --init
  invocation exits zero. digest-service twice exits 1. These remain real
  separate startup failures; they do not uniquely explain black scanout.

## Deterministic refresh initialization defect

The paired generated NativeGOPDisplay creates and enables fVBL during
start_hardware(), but sets no initial deadline. Its only setTimeoutMS(16)
was at the end of swap_begin_gated(), which may first be invoked by a
userspace submission. The new IOTimerEventSource starts with abstime zero.
In the actual prepared XNU source, enable() calls wakeAtTime(abstime), and
wakeAtTime schedules a callout only for a nonzero deadline and attached
work loop (`iokit/Kernel/IOTimerEventSource.cpp`, enable/wakeAtTime).
Consequently the timer did not independently start the advertised refresh
loop. This defect exists regardless of whether it is the only black-screen
cause on the device.

The NABU adaptation now sets the first 16ms deadline only after the native
family start returns successfully. Failure stops the initialized service and
returns false. Native callbacks retain their existing self-rearm operation;
power changes do not restart or defer the cadence. A first-timer-fired
record precedes queue processing, unlike the old vbl=0 heartbeat which was
after queue processing. Power notification begin/end records distinguish
the display-on message from successful delivery of its notification.

This software timer is not a measured DPU VBlank, and this patch does not
invent a swap, a presentation, or a native display notification. The exact
25F71 notification ABI must be qualified separately if refresh starts but
userspace still submits no frames.

## SHENG comparison and black-screen limits

The reviewed latest docs at `.work/sheng-latest-20261113/UniXNU/docs/porting/display.md`
require matching the selected OS ABI, observing actual CPU presentation,
retaining IOSurfaces through real swap completion, and separating requested
refresh from measured timing. The GopFramebuffer guide also preserves the
previous frame until a pending transfer completes. This change keeps the
paired 25F71 ABI and existing staging/completion path; the iOS17 adapter is
not copied over it.

The screenshot's transition to black does not identify who wrote black
pixels or changed display hardware. The presenter has not logged reaching
its handoff path in this snapshot. In the original native console,
kPEDisableScreen pauses output rather than clearing all pixels, and the
boot checkpoint is disabled by the installed arguments. Neither supports
calling this black transition a proven successful GUI takeover.

## Validation and next device check

`scripts/qualify_nabu_display_refresh.py` compiles the actual generated start
and callback functions with a timer model that requires completed family
initialization and a nonzero deadline. It executes successful initial arm,
callback without a userspace submission, one entry diagnostic, base-start
failure, failed arm cleanup and missing timer cleanup under ASan/UBSan.
Existing pixel/lifetime tests and full native CI remain required.

Device acceptance is refresh-bootstrap result=0 followed by
first-timer-fired and vbl counters, then a real first swap, scanout copy,
native completion and visible GUI. Those outcomes have not yet been tested.
No device, Data image, root image or original log is modified by this repair.
