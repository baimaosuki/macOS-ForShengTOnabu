# 25F71 new eight-core log: reference-qualified diagnostic repair

## Inputs and what changed on the device

Input: `out/nabu-logs-20261113/XNU2LOG-8core.BIN`. Standard CRC decoder
selects slot 1 sequence 7578, 182650 bytes, zero overwrite and two contended
bytes lost. Banner 2026-11-12 22:19:49 UTC matches refresh CI 43317441852.
Derived inputs: `verified-8core.txt`, `verified-8core-info.json`,
`8core-analysis.json`. Original files were not changed.

The initial refresh deadline repair works on the device: bootstrap result=0,
first-timer-fired and vbl counters 0 through 7499 are retained. Those are
software callbacks, not hardware DPU interrupts. The last periodic callback
is an early sample, not the final display state: the old diagnostic stops
at tick 7500, while a later CPU compositor page 2 and display-on follow near
280 seconds. No first swap, copy or completion is retained.

Immediately after display-on the normal console stops in the middle of the
next `NABU` prefix. RAM-only IPC/wait records continue through 440.864 seconds.
No complete power-notify-begin/done remains. Therefore no inference that
powerstate_notify itself blocked, or that later swaps never ran, is justified.
The native notification uses CanDrop with send timeout zero and does not
wait for a user acknowledgement; its getName and rare failure log paths
can still take native locks. No specific lock cycle is established.

OD and WS sample stderr both reach `Sampling completed, processing symbols...`,
but their guards still terminate sample at 15 seconds with status 143. There
is no saved call graph in this capture. Sampling success must not be reported
merely from those two progress messages. 28 kauth results succeed, zero
requester timeouts; all 126 observed OD timedwait returns are zero.

## Fresh SHENG and user-provided iOS16 reference review

Queried remote heads on 2026-11-13 before submitting this repair:

- UniXNU/UniXNU: `dff276535db3ac394f628cec58a718cd46c20ac2`.
- UniXNU/GopFramebuffer: `b0d812d578efa2acb005b66f73168891719af09a`.
- UniXNU/UniXNU-sheng: `1108068537d5396edb2c63b2f8c6f5b97b0a4bbc`.

Fetched source/docs and exact revisions are saved under
`.work/sheng-reference-20261113-refresh/references.json`.

| Reference | Actual behavior | Application to this repair |
| --- | --- | --- |
| [SHENG display guide](https://github.com/UniXNU/UniXNU/blob/dff276535db3ac394f628cec58a718cd46c20ac2/docs/porting/display.md) | Qualify the selected OS ABI; verify actual pixels and native retirement; distinguish requested refresh from measured timing | Preserve the exact 25F71 473-slot family; count actual submitted/shown/completed separately |
| [SHENG frame diagnostics](https://github.com/UniXNU/UniXNU/blob/dff276535db3ac394f628cec58a718cd46c20ac2/docs/porting/gpu-diagnostics.md) | Separate retained submission, release, completion and display timing evidence | Retain bounded stage events directly in the existing RAM journal, independent of panel logging |
| [Current NativeLegacyDisplay](https://github.com/UniXNU/GopFramebuffer/blob/b0d812d578efa2acb005b66f73168891719af09a/NativeLegacyDisplay.cpp) | A timer explicitly sends the original family's VSync notification only while clients subscribe; board observers retain stage evidence | Distinguish software callback from type5 subscription and transfer; add read-only subscriber presence, not an unqualified sender |
| [SHENG macOS guide](https://github.com/UniXNU/UniXNU/blob/dff276535db3ac394f628cec58a718cd46c20ac2/docs/porting/macos.md) | WindowServer waits for a native TimeSync clock derived from an Ethernet/802.11 address | Preserve real BSD naming and native matching; no fake clock-ready resource or carrier |
| User iOS16 kit, package revision `a7148554052f42159207245e2070b96492a046da`, HakiOS `96ffbc532e970f3540885755d457e401e04d4707` | MiniIOMobileFramebuffer uses firmware GOP, CPU source/destination copies, a client VSync timer and first-surface console takeover | Keep CPU-first path and real surface lifetime; do not assume visible console disappearance proves a frame |
| iOS16 `DISPLAY-CLOCK-BRINGUP.md` | CA_FORCE_TIMER was checked against exact 20F66 QuartzCore and backboardd; a bounded stack probe had explicit observation limits | No unqualified CA_FORCE_TIMER change to macOS; do not claim sample stacks that were not exported |
| iOS16 `NabuLogCapture.h` and separate writer | Producer capture is RAM-only; storage checkpoints are separate; no producer-side device operation or printf recursion | Use existing nonblocking `nabu_capture_append`, not IOLog or the panel-rendering sheng_log_putc |

The actual iOS16 files reread for this review are
`.work/ios16-gpu224-reference/hakios/xnu/files/iokit/Kernel/MiniIOMobileFramebuffer.cpp`
(first surface at 1257, client VSync at 1471),
`source/docs/DISPLAY-CLOCK-BRINGUP.md`, and
`dynamic/reference/dynamic-storage/source/patches/nabu-storage/NabuLogCapture.h`.
That source describes software VSync, not proof of a DPU interrupt. Current
GPU224 candidates and their hardware qualification are distinct from earlier
project records of a successful software desktop.

## Exact bounded change

Only NABU display diagnostics change: generated NativeGOPDisplay,
CPU-present stages and mapping-worker events use a single bounded direct-RAM
append with sequence, kernel time, console_disabled and framebuffer owner.
No console drain, rendering, allocator or storage I/O is called by this helper.
The existing journal try-lock may still drop a contended whole event; journal
loss counters and event sequence numbers remain explicit.

The old heartbeat stopped before display-on; it now continues sparsely after
the first 7500 callbacks, once per 3750 callbacks until tick 75000. It reads
the paired 25F71 VSync subscriber list head at +0x1808 without traversal.
Power-notify-begin reads the exact original type1 head at +0x17c8. Both heads
were checked against 25F71 add_notification_gated and native dispatch; the
iOS17 SHENG queue offsets and iOS16 payload are not copied.

The diagnostic change removes a known output dependency while the display gate
is held. It does not establish that console waiting caused the black screen.
No VSync sender, default-surface factory, power state, renderer selection,
TimeSync response or account data is altered in this iteration.

## Host validation and device gate

The actual presenter/reaper host test now uses the real direct-RAM formatter.
It disables console during first takeover and verifies scanout-copy and native
completion remain captured, including time and disabled flag. Truncation stays
bounded and newline terminated. Pixel/lifecycle sanitizer tests and actual
generated start/callback qualification pass. Native ARM64 build on the user's
requested Intel runner and package verification remain required.

Device acceptance: distinguish notify begin/return, console disabled state,
VSync subscription, first submitted surface, full pixel transfer and native
completion. None of these diagnostic changes alone proves desktop success.
No device, Data/root, original log or root Git index is changed.
