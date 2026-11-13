25F71 software VSync candidate
============================

`nabu_vsync25.h` adds the missing native type-5 refresh notification from the
existing display work-loop timer. It calls the unchanged original native timing
method, then the unchanged original notification sender. It does not complete
swaps or claim that pixels were presented. It is integrated in the following
functional revision, separately from the earlier direct-RAM diagnostic CI.

The paired family UUID is EA3273B7-AF0F-3E06-B0F5-072DFCDD5395.
`scripts/inspect_nabu_vsync25.py` admits only its reviewed instruction hashes,
original exports, virtual slots/PAC, kernel branch targets, and the original
clear-up queue call site's three argument loads. It admits the original paired
BootKC and the exact reviewed delivery PAC postimage as separate profiles. The
postimage must reverse at ten specified PAC instruction sites to the original
body hashes, with only the original inherited vtable PAC metadata removed.
ASan/UBSan host tests exercise the
actual helper and exact assembly symbol linkage; the native functions are
substitutes there, so host success does not verify delivery to WindowServer.

Minimal integration, performed separately from this candidate:

1. Copy/include the header in the generated display source using the existing
   source-input hash and build-record mechanism.
2. In `vblFired`, while holding the family's existing work-loop gate, call
   `nabuVsync25TickGated(display, 16000000u)` once for each timer firing when the
   display/hardware is active. Retain the existing swap processing and 16 ms
   timer rearm. Registration and removal run under this same gate.
3. Do not put this call inside `swap_begin_gated`: submissions and notification
   registration call that function too and would create extra refresh events.
   Do not add a second timer, call it from start/PM transitions, or force power.
4. The helper checks the native type-5 queue head at `this+0x1808` and does
   nothing when no subscriber exists. Registration already calls swap-begin,
   which preserves the existing timer activity. Stop cancels the same timer.

The direct native imports use these exact exported symbols:

| Function | Unslid address | Behavior |
| --- | --- | --- |
| `__ZN25IOMobileFramebufferLegacy12note_vbl_ISREj` | `0xfffffe000a89bc5c` | Original timing updates; argument is nanoseconds |
| `__ZN25IOMobileFramebufferLegacy17swap_notify_gatedEyyy` | `0xfffffe000a894cc4` | Tail-call to original native dispatcher |
| `__ZN25IOMobileFramebufferLegacy26swap_notify_dispatch_gatedEyyy` | `0xfffffe000a89bd34` | Original type-5 list walk and CanDrop send |

Neither direct import is a virtual slot. The exported sender and note method
use the existing selected-family exact-symbol resolver; no new private-vtable
binding exception is needed. Native timing method dependencies are inherited
virtual slot 349 (`get_current_absolute_time`, offset `0xae8`, diversity
`0x9562`) and slot 398 (`ns_to_absolute_time`, offset `0xc70`, diversity
`0x4f41`). Despite its name, the former tail-calls **mach_continuous_time**;
the latter tail-calls nanoseconds_to_absolutetime. The 16,000,000 ns seed matches
the actual existing 16 ms timer, rather than claiming a separate 60 Hz cadence.
Later intervals come from native clock differences. Original reset clears
`+0x108/+0x110`; the helper keeps no timing cache.

The native note method has no command-gate entry, swap-begin call, notification
send, or hardware IRQ acknowledgement. Calling it under the existing gate does
not recursively acquire that gate. It owns all timing/counter updates; the
adapter neither duplicates nor partially emulates them.

Native clear-up queue instructions at `0xfffffe000a8947a4` load arguments from
`+0x100` (count), `+0x108` (native continuous-clock reading), and `+0x110`
(interval). The helper forwards those native results in that exact order. The
original dispatcher retains six-half encoding, async reference ownership and
drop-on-full behavior. Any native error IOLog branch remains native behavior;
there is no new IOLog in the helper.

Reference cross-checks, kept as architectural evidence rather than ABI sources:

- SHENG 27.3 `.work/sheng-reference-20261113-refresh/GopFramebuffer/NativeLegacyDisplay.cpp:437`
  checks the real client list, runs a work-loop timer under the family's gate,
  and calls its unchanged native sender. Its sender arguments describe different
  OS semantics and are deliberately not copied.
- NABU iOS16 `.work/ios16-gpu224-reference/hakios/xnu/files/iokit/Kernel/MiniIOMobileFramebuffer.cpp:1474`
  sends only while its callback/port exists, under the registration lock, with
  CanDrop, and rearms its timer. Its manually encoded `time,time,count` payload
  is deliberately not reused for macOS 25F71.

Validation commands:

```sh
python3 scripts/inspect_nabu_vsync25.py --kernelcache out/macos26-assets/kc.mac13g.macho
python3 scripts/inspect_nabu_vsync25.py --kernelcache out/nabu-bootkc/kernelcache.release.mac13g.nabu.kc
python3 -m unittest discover -s tests -p test_nabu_vsync25.py -v
```

Device qualification must still establish real type-5 registration and received
notifications, and whether those notifications help the current WindowServer
startup. No subscriber means this feature correctly emits nothing; a successful
timer callback alone is not proof of desktop presentation.
