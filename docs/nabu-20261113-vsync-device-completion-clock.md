# Native VSync device result and completion clock repair

## New device evidence

Input `out/nabu-logs-20261113/XNU2LOG-vsync.BIN` SHA256
`2bafd4f5f04d051c3aaf43371155c898aeaabc2eafd3bb602b2f1515c3f8610d`.
The CRC decoder selects slot 1, sequence 7808, boot ID
`3c9d0c3b000000009b1d000000000000`: 177594 committed bytes, no ring overwrite,
three lost bytes. This is a committed snapshot ending around 285 seconds,
not proof of a complete boot capture. Banner 2026-11-13 06:20:18 UTC identifies
the delivered native-VSync build 43377105726.

At 241.538 s display-on and both power notification markers return. At
282.955 s a first real swap is submitted. Frames 1, 2 and 3 each copy 2560 rows
of a 1600x2560 BGRA surface, copy all rows into scanout, and return from the
original native completion. Shown counters reach 1, 2 and 3. All three composed
frames have 4096000 pixels, **zero nonblack RGB pixels**, centre `0xff000000`.
The first frame disables the native text console before scanout stores.
This explains why visible text can disappear into black after handoff; it
does not establish which user-space initialization prevented visible content.
Plane 0's BGRA fast copy preserves source RGB and forces alpha opaque.

The old map is released successfully during frame 3. There is no evidence of
a stuck map reaper, failed console disable, or missing completion in this run.
The trailing submitted=4 counter includes validation calls; it alone does not
prove an outstanding fourth queued frame. No captured panic, WindowServer
exit or abort establishes its later state. The missing page-0 console message
also cannot establish failure to patch that page after console disable.

No native-VSync send is captured. Visible type-5 head samples are empty; there
is no basis to attribute these three frames to delivered type-5 notifications.
The software timer counter is not a hardware VBL interrupt counter.
OD has 22 completed kauth requests without requester timeout; 103 recorded
timed-wait returns are zero. Those observations supersede older single-core
OD-timeout explanations for this snapshot.

## Busy is native behavior, not a missing completion

For the exact paired family UUID EA3273B7-AF0F-3E06-B0F5-072DFCDD5395,
`swap_wait_gated` at `0xfffffe000a8953d4` masks options into mode 0 for
`0x80000000`. With no matching current/queued request, it compares the ID with
the last displayed ID at +0x190c. A different ID returns success; the same ID,
zero timeout and bit 31 take `0xa895a7c..0xa895a88`, returning
`0xe00002d5` (Busy). This path reads no VBL timing or retirement timestamp.
Native completion stores successful IDs 1/2/3 into +0x190c and clears current,
exactly as captured. Do not replace Busy with success or rewrite native state.
The iOS16 reference's simplified always-success SwapWait is not this contract.

## Confirmed source defect and bounded correction

The adapter's `notify_swap_done_gated` copies +0x108 into SwapInfo word 0;
word 1 is the elapsed time from the previous report. Native completion dispatch
at `0xa89bf70` loads the **type-3** list from +0x17e8. The delivered helper
instead calls original `note_vbl_ISR` only if the distinct **type-5** list at
+0x1808 is nonempty. Thus a completion-only subscriber receives no timer-driven
clock updates. Native completion does not supply a replacement clock there.
This is a source-proven missing case, although neither the actual type-3 head
nor a zero completion timestamp was captured in this device snapshot. It is
not a proven unique cause of black frames.

The correction calls original note on the existing actual 16 ms callback when
display-on and either real list is nonempty; only type-5 authorizes the original
VSync sender. Both lists empty means neither note nor send. Note runs before
queue processing, preserves original reset and native continuous-clock units,
and adds no timer, IRQ, synthetic client, power transition or forced completion.
A synchronous submission before the first active timer can still complete
with zero time; no invented note is added at submission/completion to hide it.

First four completion reports now record actual time, interval, count,
type-3/type-5 presence and held state. First eight actual type-3 sends record
mask, argument count and original sender result through direct RAM logging.
Native return values and message encoding remain intact.

## References and validation

SHENG27.3 GopFramebuffer `b0d812d578efa2acb005b66f73168891719af09a`,
NativeLegacyDisplay lines 836-880, corroborates collection of the native family
completion timestamp and real per-request async reporting. Its `vsyncTick`
gates messages on actual subscriptions. Latest reviewed UniXNU docs revision
is `dff276535db3ac394f628cec58a718cd46c20ac2`.
User NABU iOS16 package `a7148554052f42159207245e2070b96492a046da`, HakiOS
`96ffbc532e970f3540885755d457e401e04d4707`, MiniIOMobileFramebuffer lines
1455-1507, uses a real timer and registered callback/port with CanDrop.
These establish architecture, not reusable 25F71 offsets or payload words.

Exact original/delivery-PAC ABI admission passes, including the type-3 list
load. ASan/UBSan tests cover type-3-only after native reset, type-5 delivery,
both lists removed, full-width timing and measured late intervals. The actual
generated callback is tested with the real helper to ensure display-off skips
timing and an active completion-only callback updates time before queue work.
Existing pixel, cursor, persistent-map and native-completion lifecycle tests
pass. Independent review found no blocking issue. Intel ARM64 CI and actual
device acceptance remain separate; a usable desktop is not yet verified.
