# WindowServer exit and exception capture

p4 (`/home/baimao/p4.txt`, SHA-256
`93dadc348c100fd134d2b92576f0fe6110fe32d81535fc4ba9b4a98c6a0cd9dd`)
records all three CPU compositor pages applied, followed by PID 100 exit
with wait status zero. Line 1405 is the path belonging to the exit at 1402,
not another exec. No exception record or GOP swap is present. High CPU
warnings for trustd and lsd do not establish an infinite loop.

`nabu-ws-log=1` adds the missing early context directly to the RAM ring used
by the existing ESP journal, independently of console/framebuffer ownership:

- `NABU-WS event=exit-syscall`: raw exit argument and live caller context
  before exit1 and address-space teardown.
- `event=exit-with-reason`: wait status, reason code and namespace before
  locks/teardown, only when the target is the current process.
- `event=exception`: exception type in code, code[0] in sub and code[1] in
  detail, before the optional SHENG stop and normal exception delivery.
- `event=task-terminate`: target/caller PID, whether self-termination and
  raw kernel call site. No reads from another task's user map.
- `event=fork-parent`: successful fork child PID in code and the parent's
  user stack, after child construction locks are dropped. This distinguishes
  daemon parent exit from termination without a child; fork does not exec.

Context includes PC/LR/SP/FP, ESR/FAR, fault-safe instruction fetch, shared
cache slide (all ones means unavailable), x0–x28, and up to twelve frame
records with raw and candidate stripped return addresses. A read error is
reported and stops the frame walk. These are sampled frames, not a guaranteed
complete unwind. Exit-syscall ESR/PC describe the syscall context, not a fault.
The observer changes no exit value, exception result or security decision.
Each compiled observer copy admits at most 32 events per boot; reads and
output stop after that. Records have PID and per-copy sequence tags.

The package option `--windowserver-log` verifies the compiled header hash
and event string, enables the observer and WindowServer stdout/stderr, and
sets `sheng-windowserver-stop=0` so a user exception can return to ordinary
handling and allow the ESP writer to commit. This does not guarantee the
last RAM bytes survive a later fatal kernel panic.

Unified user-space logging normally uses the firehose/logd path and is not
equivalent to write/writev on fd1/fd2. This change captures exit/exception
execution context; it does not claim to decode that separate binary protocol.

Reference reviewed: `.work/sheng273-reference/revisions.json` pins UniXNU
`268bfa1c587db5ee616e955cdfd97a281500a6fb` and UniXNU-sheng
`81f43ebaaa3317a5917b76093f7bc44c4949bc02`. Their macOS guide documents
exec/exit tracing; display guidance requires actual frame/swap completion.
The macOS board enables A740/Metal and CPU presentation, which is distinct
from a proven CPU-only desktop renderer.

Host validation executes the actual header with ASan/UBSan, checking opt-in
and process selection, instruction/frame copy failures, PAC candidate
addresses, missing saved state/region, cyclic or misaligned frames and budget
exhaustion. A fresh desktop prepare applies all hooks and records the header
hash. Native CI and device evidence are separate acceptance steps.

First native CI 43163428641 exposed a header integration error: importing
BSD sys/proc.h into the libsa exception translation unit redefined dev_t and
daddr_t. The observer now declares only the two proc interfaces it uses,
matching exception.c's existing pattern, without changing either ABI typedef.
Retry 43164426419 additionally required the exact libkern vsnprintf prototype
in that same translation unit; importing the broader BSD header is avoided.
Run 43165344743 then exposed BSD's separate header search scope. The build now
stages the same header beside both BSD and Mach callers and uses local includes,
instead of assuming a new internal header was in the exported kern headers.
Run 43166489806 reached the BSD caller and identified the Mach-private shared
region struct boundary. `nabu_ws_trace_slide()` now owns that lookup in the
Mach exception translation unit; BSD callers use only the scalar-returning
interface, preserving the reference/deallocation pair without exposing layout.

After decoding a run, use its recorded slide rather than an earlier boot's:

```
python3 scripts/symbolicate_nabu_ws_log.py /home/baimao/p5.txt \
  --cache-map out/nabu-pid1-cache-fault/cache/System/Library/dyld/dyld_shared_cache_arm64e.map \
  --native <exact-CI-artifact>/kernel.development.nabu --output /home/baimao/p5-ws.json
```

This maps shared-cache PCs and frame callers to module text offsets, and task
termination call sites to the nearest native kernel symbol. Executable PCs
outside the cache remain explicitly unresolved and need that executable's
load address. The tool is included in the diagnostic ZIP with native_macho.py.

## Successful build, package and installation

CI 43167776272 succeeded at source commit
`c58b15761bb71221755911076680246866b3cb4e` on the stable macos-26 runner and
paired KDK. Native symbols include nabu_ws_trace_slide, nabu_capture_append
and the existing CPU compositor globals. The compiled header hash matches
the packaged observer source.

The final image retains 35 components. Independent prelink verification passed
1000 pointers, 56031 branches and 1000 kernel bindings. Kernel UUID:
`1A1D61BE-3AAF-355F-9EC7-D69EE5F1D3ED`; SHA-256:
`605762f8864113cb0e9900186d23348130e8cf2909aa6198af37f8212f99c60c`.

Package: `out/nabu-service-startup/nabu-25F71-ws-log-43167776272.zip`,
172174594 bytes, SHA-256
`f6f02ad8ca41e0e63f4478048f08a95ffeab3e2a679a55776a7439c970e8252b`.
ZIP CRC, all payload hashes and the configuration flags were checked.

All five ESP files were installed on ae6c9094 and their readback SHA-256
values match the package. The old files and original journal are preserved
under `out/nabu-service-startup/windowserver-log/device-backup/`.
`XNU2LOG.BIN` remains byte-identical. The device was left in TWRP without a
reboot. Runtime capture and desktop rendering remain unverified.
