# OpenDirectory cold-start assertion on 25F71

Approved item 1 remains an investigation. No functional OpenDirectory fix has been established or implemented. The assertion, service launch policy and original five-second connection timeout are unchanged. This work adds no kernel instrumentation, timeout increase, assertion bypass, pre-emptive service restart or startup delay.

## Latest device evidence

Input: `/home/baimao/nabu-boot-log-20261112-060440/boot.txt`, SHA-256 `daad0a0195d8c840bb22bf9b231e6f0eae40d9a58c907a9a1d656a79c89941b4`. Kernel timestamps below are the log's `NABU-PROC`/`NABU-ABORT` nanoseconds.

| Event | PID | Timestamp (ns) |
| --- | --- | ---: |
| First xpcproxy activation for `com.apple.opendirectoryd` | 68 | 159704668489 |
| First `/usr/libexec/opendirectoryd` activation | 68 | 207623910520 |
| PlistFile assertion / SIGABRT | 68 | 247718120885 |
| Recorded process exit | 68 | 250314783437 |
| Replacement xpcproxy activation | 106 | 252532555364 |
| Replacement OpenDirectory activation | 106 | 253431536145 |

The first proxy-to-daemon interval is **47.919242031 seconds**; the replacement interval is **0.898980781 seconds**. The first daemon activation to assertion interval is **40.094210365 seconds**, which includes initialization and cannot be interpreted as the duration of its final semaphore wait. The replacement has no recorded exit in this capture; that does not establish successful directory queries or a working login session.

Lines 1040–1043 report `Assertion failed: (connection != NULL), function odm_create_connection_with_options, file PlistFile.m, line 11578`. The signal is SIGABRT, namespace 2 / code 6. Quarantine is already registered at lines 246–247, before userland and either OpenDirectory activation. This capture does not support attributing the initial assertion to Quarantine still being unregistered. The large difference between first and replacement activation times supports investigating cold-start delay, but it does not identify its cause.

## Original binary path

The unchanged 25F71 inputs are:

- `out/nabu-dit-search-fix/opendirectoryd.arm64-slice`, the ARM64E slice of `os/usr/libexec/opendirectoryd`, SHA-256 `5e1abdd7d2fd30ee95c8bb07fee1259d5bc7f7cc2b047c1aa415dc0b539a1452`.
- `out/nabu-dit-search-fix/review/PlistFile.arm64e`, from the original PlistFile bundle, SHA-256 `62cbe25f7e713ac7ae865fbeb900c74ede55964de247e91b337f52a4f9efa754`.
- `out/nabu-pid1-cache-fault/cache/System/Library/dyld/dyld_shared_cache_arm64e` and its subcaches.

In PlistFile, `0x1ae80` calls OpenDirectory's `_odconnection_create_ext`. Its argument structure contains the module context and three zero callback slots (`0x1ae44`–`0x1ae60`). The NULL return branch at `0x1ae84` reaches `0x1b788`, then assertion routine `0x41324`; `0x4134c` calls `__assert_rtn` with the condition and line number above. This is an internal OD connection, not a direct Mach/XPC service connection. The latest runtime return addresses `0x100995350` and `0x10096f78c` match a PlistFile load base of `0x100954000`. OD return addresses `0x10081e228` and `0x100815028` match slide `0x7d8000` and the internal module creation/enumeration callbacks.

`_odconnection_create_ext` calls `_odconnection_connect` when its connect callback is NULL (`0x1000215d0` onward). A nonzero connection status causes the object to be released and NULL returned at `0x1000215e4`–`0x1000215f0`. The worker's no-callback branch explicitly writes success status zero at `0x100023890`. Completing this branch requires no external hardware or service handshake.

`_odconnection_connect` reads **`connection setup timeout`**, default **5 seconds** (`0x1000219a0`). The CFString is at `0x10008a908`, with text at `0x10006e73c`. None of the inspected original OpenDirectory configuration plists overrides it. It queues asynchronous connection work, constructs `dispatch_walltime(NULL, timeout * 1000000000)` at `0x100021a20`, then calls `dispatch_semaphore_wait` at `0x100021a2c`. The queued path initializes status to `0x834`; if completion has not updated it when the wait returns, that nonzero status propagates to the NULL connection and assertion.

This establishes failed asynchronous connection completion. The saved abort stack does not establish the semaphore's actual input, elapsed wait or return code, nor the connection worker's state at that instant.

## SHENG 27.3 comparison and excluded changes

References are fixed to UniXNU `268bfa1c587db5ee616e955cdfd97a281500a6fb` and UniXNU-sheng `81f43ebaaa3317a5917b76093f7bc44c4949bc02`.

- **pthread registration:** SHENG's `build-libpthread-kernel.py` compiles three source files and an early bootstrap which calls `pthread_start`. NABU uses the original 25F71 `com.apple.kec.pthread` prelink root. XNU's `pthread_init` would panic before userland if its function table were absent, and `pthread_kext_register` explicitly rejects duplicate registration. Adding SHENG's bootstrap alongside the original provider is not a justified fix. The reference `explicit-kernel-address.patch` contains compilation type corrections, not a worker-start correction.
- **Time conversion:** the actual 25F71 libdispatch code at `__dispatch_time_init` (`0x1803695e0`) calls `mach_timebase_info`. It has a fast path for `125/3`, and `0x18037270c` computes the supplied ratio and installs conversion functions for other values. NABU's 19.2 MHz ratio, `625/12`, therefore has a real binary fallback. `mach_absolute_time` at `0x1804ae0a0` selects its counter path from the commpage and adds the published offset. No evidence supports forcing 24 MHz as the OD fix. This does not prove every runtime clock value is correct.
- **CPU admission:** this device boot uses `cpus=1`. Published XNU workqueue initialization derives QoS parallelism from the scheduler, and the non-AMP clutch implementation uses `HOST_BASIC_INFO.logical_cpu`; machine CPU-up accounting increments that count. No missing initialization or zero runtime allowance has been proved. Enabling more CPUs is not established as a correction for this assertion.
- **kqworkloop limits:** SHENG's `macos-kqworkloop-limits.patch` addresses macOS 27 launchd limits interpreted by an older published XNU. The observed 25F71 failure is the PlistFile SIGABRT assertion, not a demonstrated workloop-limit kill. It is not a basis for importing the Darwin 26 compatibility change.
- **Dyld pager source release:** SHENG's `vmwait-dyld-pager-source-release.patch` prevents a busy-source self-wait while assembling a 16 KiB fixup from four 4 KiB kernel pages. The inspected NABU build reports 16 KiB kernel pages, follows the original single-page dyld path, and has no `dyld_pager_peek` symbol. This particular 4 KiB assembly repair is not applicable to the current build.

The original pthread first-use path does fault the new worker's stack and pthread structure pages before its first return to userland (`kern_support.c:970`–`986`). That is a concrete possible delay location, not proof that it caused this failure. The cold/hot timing alone cannot distinguish page-in cost, CPU scheduling/admission, worker creation failure, queue contention or an incorrect wait deadline.

## Minimal missing device evidence

1. At the failing OD connection wait, obtain the actual relative `semaphore_timedwait` arguments, monotonic elapsed time and return code. This separates a genuine approximately five-second timeout from an immediate/error return or time conversion error.
2. At that same failure, establish whether the connection worker was created/admitted and its pending or blocked location. The relevant workqueue pending count, thread/idle counts and QoS allowance distinguish admission failure from an already-running worker blocked elsewhere.

No diagnostic ring or general thread introspection implementation was added. Item 1 must remain unresolved until evidence supports a functional correction. Existing call-chain artifacts are `out/nabu-mount-loop/opendirectoryd/boot052546-connection-assertion.json` and `connection-assertion.disasm`; they establish the same original binary branch, not a verified fix.
