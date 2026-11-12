# 25F71 OpenDirectory: current evidence and bounded timed-wait capture

This is a **diagnostic change**, not a demonstrated functional correction.
The original 25F71 OD binary, connection setup timeout, connection worker,
connected/status fields and PlistFile assertion remain unchanged. The withdrawn
unbounded queue fence and refused `/usr/libexec` overlay must not be packaged.

## Latest device evidence

Input: `out/logs/xnu-20261113a/verified-boot.txt`, SHA-256
`4f89368e961f9d5d328995b5b40a44c77d67cb4d341e000bd1bf5f721eaf0756`.
The original PlistFile `connection != NULL` assertion is at lines 1275–1278, followed by PID 68's
SIGABRT at 238880955208 ns. Replacement PID 106 registers the resolver with a
60-second timeout at 263269140572 ns (line 1348).

Request 31348 is a separate, fully correlated failure:

| Event | Monotonic time (ns) | Location |
| --- | ---: | --- |
| submit, PID 111, UID 282 / GID 20 | 635500324947 | line 1611 |
| GETWORK, OD PID 106 / TID 484 | 635579075416 | line 1612 |
| requester timeout, queue 2 / error 35 | 695501101770 | line 1632 |
| RESULT, OD PID 106 / TID 968, status 0 | 869713405937 | line 1742 |

The work was collected after 78.750469 ms, then returned successfully
234.134330521 seconds after collection. Its requester had already timed out.
Neither absent registration nor failure to collect describes this request.
The exact blocked/running location during these 234 seconds remains unknown.
The 32-thread high-watermark notice at line 1456 is one observation, not proof
of an unbounded creation loop or of all workers being runnable.

The matching published `bsd/pthread/pthread_workqueue.c` bounds total worker
creation with `wq_max_threads`, reuses idle workers and admits new requests
through the creator selection path (`workq_schedule_creator`, around line 4520).
`workq_open` derives QoS allowances from the scheduler and CPU count, rather
than allocating one always-running worker per outstanding request. The original
SystemCache listener is concurrent overcommit with 16 capacity permits and
signals capacity after ordinary result success or failure. No specific excess
creation defect is established by the current source or log. A sample must
separate runnable CPU contention from blocked workers and an admission limit.

## Counter and deadline checks

The latest SHENG macOS guide and `UniXNU-sheng/porting.toml` were read from
`.work/sheng-latest-20261113/`. Its macOS 27.3 timebase representation deliberately
scales 19.2 MHz to 24 MHz. Its optional direct userspace scaled-read optimization
is recorded as not yet hardware tested. This is not evidence of a missing
25F71 OD worker patch.

The current NABU DT generator publishes 19200000 for both `/cpus` and each CPU.
The inspected kernel's `mach_absolute_time()` reads `ml_get_timebase()`, which
returns the hardware counter plus the CPU base offset. The semaphore trap turns
relative sec/nsec into absolute ticks with `nanotime_to_absolutetime`, then adds
the current absolute time using `clock_absolutetime_interval_to_deadline`.
The existing original 25F71 libdispatch audit establishes its `625/12` fallback;
OD creates the deadline with `dispatch_walltime`, whose relative remainder is
ultimately passed to this Mach trap. No specific counter/deadline scaling
mismatch has been demonstrated. The existing microtime fix addresses a distinct
fractional-microsecond conversion and does not establish the live OD wait inputs.

## Implementation and build wiring

- `platform/nabu/macos26/log/od_wait_trace.h`: 64 fixed slots, at most 256
  admitted OD timed waits / 512 journal records, no allocation or console writes.
- `platform/nabu/macos26/patches/zzzzzzzz-nabu-od-wait-trace.patch`: wraps only
  `semaphore_timedwait_trap`, preserving arguments and immediate result. Blocking
  paths resume through a wrapper that records the original result and calls the
  original `thread_syscall_return`. A post-call log alone would miss that path.
- `tests/test_nabu_od_wait_trace.py`: applies the patch with fuzz disabled to
  the published source, extracts its actual trap function and executes it with
  the actual header under ASan/UBSan. Checks selection, real arguments, original
  result propagation, non-returning continuation, exact five-second conversion
  at 19.2 MHz, table saturation, event budget and reserved return capture.

Build integration must stage the header as
`source/osfmk/kern/nabu_od_wait_trace.h`, include the independent patch after the
existing NABU patch series, track the staged header in the source diff, and add
`nabu-od-wait-log=1` to the diagnostic package's boot arguments. The parent build
owns these shared-script edits.

Each entry records the semaphore port, requested sec/nsec, raw absolute ticks,
converted uptime ns, wall calendar and actual numer/denom. Its return records
the Mach result, continuation/immediate path and elapsed monotonic ns. An
admitted wait retains its return slot after the entry budget is exhausted.
Full slots or budgets fall back to the unchanged original continuation. A
thread killed before its wait continuation runs can leave a slot occupied for
the rest of that boot; this bounds logging and does not block the wait path.

## Validation and remaining limits

`python3 -m pytest -q tests/test_nabu_od_wait_trace.py`: **2 passed**, including
the compiler's ASan/UBSan execution of the trap observer. The second test extracts the real
`nanotime_to_absolutetime` and `clock_absolutetime_interval_to_deadline` bodies,
checks 19.2/24 MHz fractional conversions, unchanged absolute counter domain
and overflow saturation under UBSan.
Patch dry-run against the inspected XNU
source succeeds with `--fuzz=0`. This host execution does not establish a
compiled kernel, hardware scheduling, successful account lookup or login.

Device acceptance requires identifying the failing connection's relative wait
input, actual elapsed duration and return, then matching the OD worker sample
from the session probe. The separately requested SMP work may improve available
CPU time; neither more CPUs nor a missing abort alone demonstrates functioning
OD or successful desktop login. Keep successful account queries and actual
session progress as functional acceptance criteria.

For an independently prepared source tree, run the tests with
`NABU_OD_WAIT_SOURCE=/absolute/path/to/source`; the patch-application test expects
that tree's `sync_sema.c` to precede this new diagnostic patch.
