# 25F71 real thread stacks and sample report preservation

The v5 probe gives original `/usr/bin/sample` **120 seconds** to finish its
single snapshot and symbol processing. It does not use `-mayDie`, and keeps
the full report and diagnostic file in the root-owned 0700 directory
`/private/var/tmp/nabu-session-probe`. It does not print a truncated report
and then delete the original. The console mirrors diagnostics; a separate
bounded relay copies at most 16 KiB of a report to retained RAM and marks
any truncation. The actual report file remains complete, subject to what
the original tool wrote before its 120-second guard.

The existing signed `apfs_boot_util` now also implements
`--nabu-raw-stacks PID`, `--nabu-raw-stacks --watch` and
`--nabu-probe-relay BASENAME`. Mount-phase behavior remains in the same
executable. `main` dispatches these commands before mount phase validation.
No additional executable needs distribution or a new trust entry.

The raw watcher starts concurrently before `sample`, discovers real PIDs
through `KERN_PROC_ALL` in the same process, and records each of
opendirectoryd/loginwindow/WindowServer twice: when first observed, then at
least 150 monotonic seconds later. Each record carries target PID, round
and monotonic nanoseconds. A restarted target is rediscovered for the second
round. The watcher has a 480-second discovery bound and an outer 515-second
alarm. Each target runs in one child with a 10-second alarm; a slow/blocked
Mach operation cannot consume the later targets' 120-second symbol budget.
Child death and unfinished enumeration are explicit partial diagnostics,
not a claim that every thread was successfully sampled.

`task_read_for_pid` obtains a read port; the helper requests no task control
port, never suspends or modifies the target, and does no online symbolication.
It enumerates all returned threads, reports the exact result of each
`thread_info`/`thread_get_state`, and emits PC/LR/FP/SP plus their raw register
words. It walks at most 32 ascending, 16-byte-aligned frame records per
thread, within eight MiB above the captured SP. Each memory read must return
the exact requested byte count; failed/short reads never become frames.
Raw authenticated return words are preserved without guessing a PAC mask.
Invalid links, read failure, nonascending links and frame limits explicitly
report `truncated=1`. Registers and frames are live unsuspended observations,
so a thread may move between the register snapshot and a memory read.

After raw thread output the helper reads only bounded remote dyld metadata
and the load commands of up to 16 actual images. It reports the real cache
UUID/base/slide, image UUID and preferred text address when those reads
succeed, and whether dyld's image pointer/count changed. Failed reads leave
real failure codes. Image enumeration is bounded independently of thread
enumeration. These addresses can be resolved later against the exact binary
and dyld cache; no local filesystem symbol lookup occurs on the device.

Each `NABU-RAWSTACK` record is formatted into one fd2 `write` of at most
512 bytes. The kernel's dedicated path in `process_output.h` requires
`nabu-session-probe=1`, root credentials, exact `p_comm=apfs_boot_util`,
fd2, a complete printable prefixed single line and a successful bounded
copyin. It writes directly to `nabu_capture_append`, independently of
`/dev/console`, `printf`, and the old eight-line/five-second stderr limiter.
It has a separate one-MiB global budget and emits one explicit drop marker
on exhaustion. Retained-ring contention/overwriting remain possible and
are tracked by the existing RAM log's loss accounting. The normal write
still runs with its original checks, side effects and return value.

The report relay accepts only a restricted basename, opens the fixed private
directory and file with `O_NOFOLLOW`, requires a root-owned regular file and
rejects unexpected permissions/types. It encodes chunks of at most 160 bytes
as hex in complete prefixed lines. It cannot relay arbitrary paths or an
unbounded shell/cat stream. The raw report and original sample files are kept.

## Original sample evidence

The initial local `out/nabu-session-probe-audit/sample` was a **truncated
65,536-byte file**. Its universal header places ARM64E at offset 81,920 for
93,184 bytes, so its strings could not validate ARM64E behavior. The complete
175,104-byte original was re-extracted with existing 7zz 26.06 from the
25F71 OS image. Full SHA-256:
`569d1e8f29e32e0557fdfffb7620e20126fab5df90f9e1bce4c6930ccded7363`.
ARM64E SHA-256:
`71fad4024ada165148f9db34bd7bac94215dcf197a1f5e4d6f1d6f91d7bb531f`.

ARM64E registration and callbacks prove that `nodsyms` is a real option,
but sets sampler option 0x80 and still enters the final report processing.
`stdoutOnly` only selects `outputString`; it does not bypass processing.
The code calls `sampleForDuration:interval:` at `0x100002534`,
`stopSampling` at `0x10000253c`, prints `processing symbols` at
`0x100002564`, then calls `outputString` or `writeOutput:append:`.
There is no established `-noSymbols` switch. The original man page does
not document such a switch. The original `-mayDie` path explicitly invokes
`preloadSymbols` before sampling and is deliberately omitted.
Static call flow cannot identify which library call consumed the observed
remaining time after that message.

The iOS16 `lockdown-stack-sample` reference is an actual native tool, not
a shell script. Its disassembly confirms task acquisition, `task_threads`,
`thread_info`, ARM64 `thread_get_state`, bounded `mach_vm_read_overwrite`
frame reads and immediate printed addresses. It informed this implementation;
its target PID/name, addresses and iOS binary are not reused on macOS.
Disassembly and structured evidence are retained under
`out/nabu-session-probe-audit/`.

## Read permissions and build

The original ARM64E sample signature contains
`com.apple.system-task-ports.read` and
`com.apple.system-task-ports.read.safe`. The owner shim adds exactly these
two read permissions to its existing bindfs entitlement. It does not request
task control, disable target MAC policy, change target entitlements, or
assume reads must succeed. XNU's `task_read_for_pid` still applies its POSIX
check, then `mac_proc_check_get_task(..., TASK_FLAVOR_READ)`; failures remain
visible. `task_threads` accepts task-inspect/read and returns corresponding
thread rights; `mach_vm_read_overwrite` accepts a read map.

Build remains the existing command:

```sh
python3 scripts/build_nabu_boot_shim.py --output out/nabu-boot-tools/boot-shim
```

It uses macOS SDK clang, ordinary `arm64-apple-macos26.5`, C11, `-Os -Wall
-Wextra -Werror`, then the existing ad-hoc signing, DER entitlement generation,
strict signature verification and CodeDirectory page checks. The builder
verifies embedded entitlements equal the source plist and records the new
raw header hash. This Linux host has no macOS SDK; host boundary compilation
does not establish the final Darwin build, signature or device behavior.

Validation: `tests/test_nabu_raw_stacks.py`,
`tests/test_nabu_process_output.py`, `tests/test_nabu_session_probe.py` and
`tests/test_nabu_boot_shim.py` passed **11 tests plus 17 subtests**. These
execute the real headers with injected Mach/kernel boundaries, including
denied task access, thread-state failure/short count, short/failed VM reads,
misaligned/backwards/out-of-range frame pointers, a 32-frame limit, 130
threads without a thread cap, actual dyld UUID metadata, extreme-length relay
records, root/process/fd/prefix restrictions and exact budget exhaustion.
The same host adapter compiles and executes the real watcher with changing
sysctl PID lists and a simulated monotonic clock: it verifies all six target
dispatches, round separation and newly discovered second-round PIDs. The
relay's full platform code is compiled and rejects path traversal.
The real shell fixture simulates symbol processing lasting 25 logical seconds
and confirms the new 120-second guard allows completion and retains reports.
