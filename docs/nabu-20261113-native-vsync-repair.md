# NABU 25F71 native client refresh repair

The initial timer-deadline fix is device-confirmed in
`out/nabu-logs-20261113/verified-8core.txt`: bootstrap result zero, first timer
and software callbacks through 7499. That callback still invoked only swap
processing. It never updated original VBL state or sent a native type-5
notification. This revision adds that missing functionality, alongside the
previously prepared direct-RAM diagnostic repair.

## Reference and exact native contract

SHENG GopFramebuffer `b0d812d578efa2acb005b66f73168891719af09a`,
NativeLegacyDisplay::vsyncWanted/vsyncTick, sends through its unchanged family
only while clients subscribe under the registration work-loop gate.
User-provided NABU iOS16, package `a7148554052f42159207245e2070b96492a046da`,
HakiOS `96ffbc532e970f3540885755d457e401e04d4707`,
MiniIOMobileFramebuffer::vsync, has the same timer/subscription/CanDrop design.
Its time/time/count payload and SHENG's iOS17 offsets are not reused.
See `docs/nabu-20261113-8core-reference-review.md` for exact source locations.

`scripts/inspect_nabu_vsync25.py` qualifies actual 25F71 family UUID
EA3273B7-AF0F-3E06-B0F5-072DFCDD5395, exact original exports, function hashes,
inherited virtual slots and the original call site's parameter loads. It
separately qualifies the original ARM64E input and ten explicitly reviewed PAC
conversions in the actual ordinary-ARM64 delivery, not arbitrary changed code.

The helper calls exact exported note_vbl_ISR at 0xfffffe000a89bc5c, reads its
native count +0x100, clock +0x108 and interval +0x110, then invokes exact native
swap_notify_gated at 0xfffffe000a894cc4. The original sender retains encoding,
list traversal and reference ownership. Existing kext export resolution binds
both functions without a new private-vtable exemption. The original clock
slot 349 tail-calls mach_continuous_time despite its method's name; slot 398
converts nanoseconds. No userland message or clock value is fabricated.

## Integration and limits

Only the existing actual timer callback invokes the helper, with display-on
true and a real type-5 list at +0x1808. Native note receives 16000000ns to match
the existing 16ms timer; following intervals come from native clock readings.
It then retains existing swap processing/rearm. Submissions and registration
also call swap_begin_gated, so this helper is deliberately outside that path.

Registration/removal and callback share the family work-loop gate. Original
note has no gate reentry or hardware IRQ acknowledgement. Existing cancellation
generation checks protect stop. Re-subscription or waking may produce a large
first measured interval if native reset has not cleared its previous timestamp;
that is not a continuous hardware 60Hz claim.

No subscriber means no notification. Native-vsync diagnostics count attempted
native sends, not user acknowledgements or shown frames. Power, TimeSync,
renderer, accounts and default-surface lifecycle are not changed. A successful
send is not proof of the black-screen root cause or an interactive desktop.
The default-surface path remains under investigation: no guessed allocation
flags, unprepared descriptors or fake surface is included.

## Validation

ASan/UBSan tests execute the actual helper with explicit native-call substitutes:
empty subscription, full-width outputs/order, late intervals, removal and reset.
The actual generated callback test verifies display-off skips it, display-on
calls once per timer and unsubscribed results do not count as sends. Existing
presenter/reaper tests verify actual direct RAM copy/completion records survive
console disable. Both original and delivery KC qualification passed. An
independent review found no blocking issue in the clock, gate, reference or
stop contract. Native ARM64 compilation uses the requested Intel runner.

Device acceptance requires actual subscription, native notification attempts,
real surface submission, scanout copy/native completion and a visible usable
session. No device, Data/root or original log is changed by this build.
