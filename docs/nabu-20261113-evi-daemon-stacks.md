# evi capture: OD/loginwindow stacks and diagnostic interference

Input: `out/nabu-logs-20261113/verified-evi.txt`, SHA-256
`121691d5c1db9b00e0caa6ebf96237cc4750d7569f5b062dc3bf69717d58d6a0`.
No device, Data seed, kernel, display or CI input was changed while extracting
these observations. The separately authorized v6 probe changes are described
at the end.

## Recovered original sample reports

The relay transport recovered these complete files:

| File | Bytes | Result |
| --- | ---: | --- |
| `nabu-sample-opendirectoryd.txt` | 3341 | original sample exit 0 |
| `nabu-sample-loginwindow.txt` | 2069 | original sample exit 0 |
| `nabu-sample-launchservicesd.txt` | 2435 | original sample exit 0 |
| `nabu-sample-WindowServer.stderr` | 68 | sample killed by 120-second guard, exit 143 |

Files, their stderr, source-line offsets, chunk ranges and hashes are in
`out/nabu-evi-daemon-audit/relay-manifest.json`. The extraction script checks
continuous offsets, exact recovered byte counts and all end records with
`truncated=0`. Both direct retained records and console copies exist. Some
console copies are interrupted mid-hex, and two loginwindow chunks contain
different console bytes. These conflicts are explicitly recorded; the
complete direct retained copies are selected instead of silently fabricating
a merge. No WindowServer text report is present in the relay capture.

## What OD was doing

The original OD sample records a single snapshot, not a duration profile.
Main thread 504 is in:

```text
main → daemon_startup → odnodeconfig_scan_base_configurations
 → _scan_config_folder → NSDictionary dealloc / cow_cleanup
 → _CFRelease → __CFAllocatorSystemDeallocate
```

The recursive dictionary destruction is a real stack observation during
configuration scanning. It does not show a semaphore, deadlocked serial
queue, account keybag operation, or 60-second kauth wait. Repetition of
`cow_cleanup` within one stack is recursion depth, not measured CPU residency.
The three other sampled worker threads are waiting in `__workq_kernreturn`.

At the later raw round, OD has 12 enumerated threads. Resolved examples are
the Mach message server, `__identitysvc`, workqueue idle, property-list
construction/parsing, UUID generation and CoreFoundation object creation.
This is active/serving startup work across several queues, rather than all
threads remaining in the original configuration-scan stack. The UUID path is
`libcorecrypto generate → ccdrbg_generate → ccrng_crypto_generate →
uuid_generate_random`; this snapshot cannot establish a random-generator
stall or entropy failure.

The capture also contains measurable observer interference: OD main thread
has 11 matched IPC calls whose recorded peer is original sample PID 162,
totaling **10.710165732 seconds**. Their sum is a measured dependency cost,
not the complete startup delay or proof of OD's primary root cause.

## loginwindow progressed between two different waits

The original sample main-thread stack is:

```text
LoginPortJuggler initialization
 → SLSessionOwner sessionOwnerForNewSessionWithAuditSessionID:launchData:
 → SLSessionOwnerCreateNewSessionWithAuditSessionID
 → CGSLookupServerRootPort
 → bootstrap_look_up2 / bootstrap_look_up3
 → _xpc_pipe_mach_msg → mach_msg2_trap
```

This directly identifies a WindowServer service bootstrap lookup during
session creation. It is not an OD, AKS or account authentication wait.

The second raw sample is **later in the same high-level operation**, not the
same bootstrap lookup. Main thread 620 has
`mach_msg2_trap → mach_msg →
___SLSessionOwnerCreateNewSessionWithAuditSessionID_block_invoke+0xe4 →
SLSessionOwnerCreateNewSessionWithAuditSessionID+0x10c`.
The paired original SkyLight disassembly shows `CGSLookupServerRootPort`
must already have returned a nonzero port to reach this block. The block
constructs and sends the session-create Mach RPC and waits for its reply.
Therefore the original loginwindow did make progress through lookup, but its
captured main thread was still awaiting session establishment. The two
snapshots cannot alone identify which WindowServer initialization step
delayed the reply or establish a permanently stuck request.

Exact paired-cache symbols and disassembly are in
`od-login-round2-symbols.json` and `login-session-flow.disasm` in the audit
directory. The captured cache UUID matches
`46e0097ff38536c884a9a40d315a32d1`, with slide `0x7ddc000` for the later
snapshots. Initial OD's zero cache UUID occurs during early loader startup
and is not treated as a matching cache identity.

## Original sample has real cost, but did not freeze WS for all 120 seconds

Between WindowServer sample start (physical LF line 2635) and its status 143
(line 3925), the capture contains **433 WindowServer IPC records** spanning
kernel absolute 251.575026197–379.009704895 seconds, including successful
calls and growing call IDs. This rules out an uninterrupted whole-process
freeze lasting the entire sample command. It does not rule out individual
pauses or synchronous observer communication.

The matching original Symbolication implementation confirms those individual
pauses. `VMUSampler::recordSampleTo:timestamp:thread:clearMemoryCache:` calls
`task_suspend2` at unslid `0x1cf085a20` before collecting thread data, and
the successful path calls `task_resume2` at `0x1cf085e80` before returning.
The single-thread sampling path similarly calls `thread_suspend` and
`thread_resume`. Original sample returns from `sampleForDuration:interval:`,
calls `stopSampling`, and only then prints `processing symbols` and generates
output. Thus the report-generation timeout is not itself evidence that a
task suspension was held for the entire timeout. Exact pause durations cannot
be recovered from those static call sites.

More directly, WindowServer main thread 646 has **five matched IPC calls to
sample PID 223**, message ID 16384, totaling **48.472610989 seconds**:

| Call | Kernel start (s) | Kernel finish (s) | Wait (s) |
| --- | ---: | ---: | ---: |
| 1986 | 277.059014531 | 288.318233489 | 11.259218958 |
| 2011 | 288.318299843 | 293.690970833 | 5.372670990 |
| 2044 | 298.465103281 | 323.701949895 | 25.236846614 |
| 2141 | 345.120574583 | 349.584094583 | 4.463520000 |
| 2155 | 355.573456093 | 357.713810520 | 2.140354427 |

All five receive results report success. The identity comes from the kernel
peer snapshot, not a guess based on port numbers. This is strong evidence
that online symbol diagnostics materially lengthened startup. It does not
make all evi delays diagnostic overhead. In particular, original
`launchservicesd` PID 128's successful sample is not a sample of the separate
`lsd` PID 216; the observed high-CPU lsd remains a separate performance target.

Evidence: `sample-peer-waits.json`, `ws-during-sample.json`, and
`sampler-control-flow.disasm`. The pairing script preserves call IDs,
thread IDs, start/end timestamps, peer PID and source lines.

## The timestamp domains must not be mixed

v5 raw records use `clock_gettime(CLOCK_MONOTONIC)`, not
`CLOCK_MONOTONIC_RAW`. The matching Libc implementation obtains calendar time
minus boot time for CLOCK_MONOTONIC. Kernel `NABU-SESSION` records convert
`mach_absolute_time` to nanoseconds. The domains have a substantial offset
in this boot: WS raw round 1 `100.848901` is adjacent to kernel
`182.77990`, while raw round 2 `296.093464` is adjacent to kernel
`378.16097`. Raw `296` must not be placed at kernel absolute second 296.
Adjacency is not an exact clock calibration; logging/scheduling can add delay.
The original source path and all neighboring measurements are recorded in
`time-domain-adjacency.json`.

## Authorized v6 diagnostic performance repair

The revised bootshim makes **raw-only the default**. Online original sample
is enabled separately by the exact `nabu-session-symbols=1` boot argument;
the installer passes a dedicated environment flag only after matching that
argument. Without it, the shell reports a clear skip and does not run
ps/sample/launchctl. Enabled sample retains the requested 120-second guard
and full files.

Raw records now use `CLOCK_UPTIME_RAW` and label `clock=mach-uptime`.
The matching Libc implementation derives this clock from
`mach_absolute_time`, aligning with kernel trace time. Each dynamically found
required target is observed at discovery, +150 and +300 seconds. Optional
`lsd` receives the same rounds if observed; its absence never delays completion
of the three required targets. All discovery/target/outer bounds remain
480/10/515 seconds, and the helper never suspends its targets. All three
rounds can still be partial if time, read permissions, frame validation or
retained-log budgets are exhausted; outputs mark those limits.

Builder metadata now records exact source/self hashes and an explicit v6
`probe_policy`, checked against implementation markers. Behavior tests
execute the actual default skip, opt-in 120-second sample workflow, precise
boot-argument matching, nine required dispatches and twelve dispatches when
lsd is present, dynamic later PIDs and absence without an extra tail. The
final related suites passed 11 tests plus 17 bootshim subtests; host tests
do not substitute for the macOS tool build or device verification.
