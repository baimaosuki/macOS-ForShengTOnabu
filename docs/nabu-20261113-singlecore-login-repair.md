# NABU single-core login/presentation repair, 2026-11-13

Input: `out/logs/xnu-20261113b/area1.txt`. Its line 850 explicitly contains
`cpus=1`; do not use it as validation of either eight-core package. The visible
arrow in the supplied photograph confirms some displayed content, not which
producer or current swap path wrote it. It does not prove an interactive session.

## Verified failures and corrected attribution

- First sampler round runs before Early boot complete (1104), OD exec (1154),
  WS exec (1221) and loginwindow exec (1327). Four sample diagnostics explicitly
  say no existing matching process (1025/1030/1035/1040); exit 255 is not evidence
  of denied task access. launchctl prints all fail with reentrancy avoided.
- Round 2 prints a process listing, but no later sampler marker. Unbounded ps
  and launchctl calls can prevent all later stack attempts. Probe v3 discovers
  real PIDs, samples before launchctl, and bounds both supporting commands.
- WS exit at 724886556614 ns is real exit(0). Captured first frame
  0x18e26adec minus 0x749c000 slide equals 0x186dcedec exactly: the previously
  inspected idle_timeout exit return. This is the same unslid call site, not
  a new +0x11000 path. No counted active session exists at that decision.
- There are 97 OD wait enters, 94 returns and 19 timeout returns in this
  snapshot. Two selected waits cannot be assumed to cause a kauth timeout.
  kauth sequence association is saved separately in
  `out/logs/xnu-20261113b/verified-session-analysis.json`.
- Multiple OD identity results are delayed until ~600 seconds; their kernel
  callers already returned error 35 after about 60 seconds. OD is alive, but
  that does not establish successful cold-start identity service.

## Implementation underway

Probe v3 is a diagnostic repair, not the desktop functional fix. Its actual
shell host test delays process creation, requires numeric PID sampling, and
makes launchctl ignore TERM; the hard guard still permits round completion.
The installer executes the real v3 start marker and tests spawn failure.
Both tests passed. Functional display/default-surface and logging-hot-path
repairs are being reviewed independently before a single-core build. No
user device, Data image or system image is modified during this work.

## Eight-core c capture changes the active display failure

New input `out/logs/xnu-20261113c/XNU2LOG.BIN`, SHA256
`b97dafb3a4b774678a763fe35768c5d8d13c09144696f7eaf2bafce95deef164`.
The standard decoder selects CRC-valid slot 1, sequence 6122, 180597 bytes.
The supplied 180338-byte texts correspond to slot 0, sequence 6121. Original
files are preserved; `verified-boot.txt`/`verified-info.json` retain the newer
snapshot. Ring overwrite is zero, contention loss is 3; not a complete future
boot trace. There are no new third-region bytes to invent.

Boot arguments contain cpus=8 and sheng-smp=1; this run reaches userspace and
records a real first swap at lines 1956-1957. There are no per-core startup
markers in this captured console; CPU node/configuration count alone is not
individual timer/IPI qualification. The displayed first swap is not evidence
of its completion. The current frame logger stops after full-screen layer 0
geometry, before any copy success/failure or completion message.

The correlated OD comparison at `out/nabu-mount-loop/od-b-c-comparison.json`
shows all 28 c requests returned and no observed requester timeout. Compared
with b, this run passes the major cold-start identity delays. No WS exit
record is present in c through the committed snapshot. The active work now
follows layer 0 source mapping/copy, while retaining native completion and
real failure retirement. The next package preserves this eight-core setting.
