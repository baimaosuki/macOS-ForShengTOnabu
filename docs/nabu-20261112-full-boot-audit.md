# Full journal review: 2026-11-12 19:31 archive

Input `/home/baimao/nabu-boot-log-20261112-193111/boot.txt`: 137513 bytes,
1551 LF lines, SHA-256
`9f5afe108d0a43fea8821ae7381184aa0b2fb34469a24e8c15c6126118b29e32`.
Line numbers below count LF, retaining console CR bytes. This is an audit;
no kernel, configuration or device state was changed.

Follow-up source/binary comparison is recorded in
`nabu-20261112-sheng273-repair-plan.md`. It establishes a 19.2 MHz integer
microsecond-conversion defect and reproduces the negative/unsigned-wrap
duration pattern; identifies the encryption-key message as coredump key setup;
and verifies that the actually compiled AKS companion already includes SHENG's
software wrap/unwrap implementation. Those findings supersede the corresponding
open investigations in the table below. Device validation of the time correction
is still pending.

## Additional findings

| Priority | Observation and exact lines | Established meaning and next investigation |
| --- | --- | --- |
| High | 1196: APFS directory iterator reports `18446744073709` seconds and stops iterating. 1300: transaction flush took `-9739 us`. 1403: flush `2251799813686247us`. 1499: flush `2147484330us`. | These durations cannot be real boot elapsed times. Inspect the matching APFS call sites, timestamp types, supplied time functions and subtraction/conversion results. Distinguish bad instrumentation/uninitialized fields from faulty clocks. The directory path explicitly says it stops iteration, making this more relevant than a cosmetic number. No global clock rollback or specific frequency fix is proven. |
| High | 1237: external kauth resolver times out after 60 seconds; 1224: OD exceeds the 32-thread watermark. | A request failed to complete. Prior exact SystemCache analysis shows the 60-second setting follows resolver registration; this is not evidence that registration never happened. No thread-capacity/deadlock cause is identified. Trace the pending lookup and worker, especially the loginwindow/session request path. |
| High/medium | 1268: AppleKeyStoreUserClient fails MACF for contextstored PID 66. | Actual user-client access rejection, despite successful AKS registration and other selectors returning zero. Inspect this client's credentials/entitlements and the rejecting MAC policy. Do not remove all sandbox/MAC checks or infer that the entire keystore works from selector success. |
| Medium | 1222: `Failed to handle the new encryption key. Error 0xe00002d8`. | IOReturn.h identifies this as kIOReturnNotReady. The interleaved line does not identify its originating service. Do not assert it is caused by AKS MACF rejection or that it proves data corruption. Identify the original binary string and call path before a functional change. |
| Medium | 1259, 1389, 1444, 1473, 1510: digest-service exits with status 1. | Five observed failures, at elapsed 503.815, 732.042, 1024.175, 1248.414 and 1538.307 seconds. This is a continuing restart sequence; the earlier claim that it was fully resolved does not apply to this boot. Authentication, identity and date dependencies need inspection; no exit message identifies one yet. |
| Medium | 963–964: Data `/private/var/run` RENAME_SWAP and directory purge fail with EPERM. | Ordinary Data/VM create-close-unlink probes pass at 872–877 and 902–907, so this is an operation/path-specific denial rather than Data globally read-only. Audit vnode flags, mount/firmlink boundaries and MAC authorization. It cannot be dismissed as inherent to RAM storage. |
| Medium | 841 onward dates are near Epoch 0; fsck reports future superblock timestamps at 859–863; tzinit falls back at 1020. | Calendar time is uninitialized relative to the image. This is distinct from monotonic duration anomalies above. It can affect certificate/authentication paths, but no certificate validation failure is logged here. A board RTC or explicit boot-time source is preferable to a fabricated frequency or arbitrary future date. |
| Medium | 1432–1436: kernelmanager_he PID 169 has recent 50% CPU; 1481–1485: lsd PID 152 has recent 71% CPU. | These are the two CPU warnings in this run; trustd's prior warning belongs to p4. Both show substantial system CPU as well as user CPU. Under cpus=1 this can delay GUI clients. Counters do not identify an infinite loop; duration/accounting anomalies need checking before quantitative conclusions. |
| Medium/low | 1116: Data lowdisk notification, free 778752 blocks; Data capacity at 795 is 786432 blocks of 4096 bytes. | The temporary Data volume is only 3 GiB total and about 2.97 GiB free at this checkpoint. macOS can signal low space despite a high free percentage on a small volume. No ENOSPC is observed. Check absolute free-space policies before enlarging a RAM-backed volume, which would compete with system memory. |
| Unresolved display | 1368: WindowServer stdout `Didn't find ADM service`. 340: original IOMobileFramebufferLegacy rejects its provider type. 736–739: NativeGOPDisplay starts. | Keep these as separate interface/dependency findings; the generic legacy rejection does not prove the native adapter failed. ADM's identity and significance require its call site. Neither message alone proves why clients fail to connect. No frame/swap is recorded. |
| Layout limitation | 769: root lacks a volume group. 806: Data role 0x0. 762–765: named root snapshot/authenticator lookup fails, then mount completes at 770. | Current owner layout mounts successfully but differs from normal macOS volume-group/role/sealed-snapshot relationships. Do not write APFS superblock fields directly or declare the warnings fatal. Compare the Data seed and owner layout contract against SHENG, then trace a consumer actually requiring these properties. Cryptex role 0x0 messages at 790/825 must not be interpreted as Data-role bugs. |

## Positive evidence and limits

- OS/SystemOS/App become ready at 734/632/683; the journal does not show UFS
  read failure or a return to the earlier root-manifest rejection.
- Data and VM probes pass; both mount phases reach ready. Hardware mapping,
  App cryptex and owner shim reach status zero. The optional OD replacement
  is absent at 878/908, so the current system runs the original OD.
- FIPS POST passes all tests at 954 and returns true at 955. This proves
  the exercised crypto self-tests, not a working keybag/security service.
- Early boot completes at 1054. cfprefsd starts and no captured cfprefsd exit
  occurs. endpointsecurityd --init exits zero, which is not a crash.
- No NABU-ABORT, captured WS exception or fork-parent event is present.
  The proven WS exit is its no-client idle callback, twice; see the separate
  windowserver-idle-exit report. Its timer condition alone does not prove
  loginwindow was the only missing client or that OD was the sole cause.
- Missing iBoot SFR/nonce/boot-performance metadata, absent prior-boot
  assets/PRNG seed and AMFI messages explicitly marked non-enforcing or
  non-fatal are not evidence of new fatal failures. Missing PRNG persistence
  does not prove that the live kernel has no entropy.
- Journal CRCs validate and loss counters are zero through the snapshot.
  `complete_boot_log=false` accurately notes that post-commit bytes and
  output outside these producers are not guaranteed. It is not corruption.
- Direct RAM observer records can interleave inside queued console lines;
  the placement of `CPU compositor page` after an exit-entry record does not
  establish execution order between different output producers.

## Proposed repair order

First inspect the impossible APFS durations and the directory early-stop
branch alongside the registered OD lookup timeout. These can affect service
startup and complicate timer/CPU interpretation. Then identify loginwindow's
actual missing connection and the keystore access/not-ready failures. Fix the
specific `/var/run` denial and calendar source, and reassess digest-service.
Prove a known frame through the native framebuffer separately from client
connection success. Broader Data role/group, capacity and persistence work
needs the corresponding consumer or resource evidence.

This plan does not call for blindly changing the 19.2 MHz board timebase to
SHENG's 24 MHz, disabling all authorization, suppressing the idle exit, or
treating an alive WindowServer as a rendered desktop.

Exact grouped excerpts are saved in
`out/nabu-service-startup/ws-193111/full-audit-evidence.json`.
