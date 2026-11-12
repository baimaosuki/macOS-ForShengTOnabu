# NABU CPU desktop and eight-core repair candidate

2026-11-13. User requested SOL implementation, compilation/packaging and eight-core enablement. Reference review: nabu-20261113-ios16-gpu224-display-review.md. No device write or reboot is part of this build.

## Functional changes

- Match original 25F71 framebuffer persistent-plane semantics: retain unchanged planes through the native DisplayedData, apply source/destination rectangles, preserve cursor updates, stage actual CPU pixels before scanout and complete through the original family. A failed copy retires with the original error path. BGRA and half-float presentation report actual pixel content and completed copies.
- Enable original 25F71 `-timesync_bringup`: replace the clock worker's infinite platform quiet wait with its original five-second bringup wait. Real MAC-derived clock lookup, failure checks and publication remain unchanged. Inspect script qualifies the exact original code before packaging.
- Enable eight ADT CPUs with SM8150 MPIDR affinities 0x000..0x700, PSCI SMC, virtual timer interrupt 19, SGI IPI and sheng-smp=1. Preserve Affinity1 in the secondary reset lookup. Call original ml_cpu_init_completed only after every configured registration succeeded; publish the normal all-CPU resource afterward. Retain sleep-disabled until suspend/resume is qualified.
- Fix the silent session probe installation: spawn its shell directly with explicit console descriptors and a new session, emit an immediate marker and begin bounded sampling after 15 seconds.

## OD limitation

The initial OD connection assertion and registered-resolver delays are still device-observed problems. No unsupported timeout increase, queue fence or fabricated response is shipped. The independent OD timedwait observer preserves native behavior and records actual relative timeout arguments, timebase and immediate/continuation result. Eight-core execution may change the delay; that is not a proven OD root-cause fix.

## Validation and delivery

Host: 25 tests plus 17 subtests passed across pixel/lifecycle sanitizer fixtures, actual patched registration barrier, ADT, native SMP, TimeSync original-code qualification, actual OD timedwait wrappers and boot shim direct child execution. Full source preparation with all desktop patches succeeded. One old SHENG recovery-generator test remains incompatible with that pinned generator's CLI, unrelated to the NABU path; no false all-suite pass is claimed.

CI must compile/link the native ARM64 kernel, board components, CPU presenter and signed owner tool from the same commit. Package via scripts/package_nabu_services.py with --cpu-compositor --windowserver-log --session-repair --cpu-desktop-repair --cpus 8. Preserve the installed logger allocation/token and existing Data/root image. Root index and dirty worktree are preserved through a temporary Git index.

Device acceptance still required: every secondary registered/running with timer and SGI, original TimeSyncClockManager published, actual source pixels copied/completed, and a visible interactive login/desktop. Build success is not device success.
