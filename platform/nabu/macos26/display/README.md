# Nabu 25F71 CPU scanout

Build with `prepare_macos26_gop.prepare(root, output, board="nabu")` and the
original **25F71** IOMobileGraphicsFamily, IOSurface and IOAVFamily. Enable with
`native-gop-display=1 cpus=8` (also accepts 1, 2, 4). The unchanged default is off. No A740/SM8550
GPU hardware, platform identity or macOS 27 ABI is imported.

The board policy accepts only the existing 1600 x 2560 BGRA GOP at 0x9c000000,
stride 6400, active span 0xfa0000 within the 16 MiB carveout. The scanout stays
portrait; the rotation helper alone does not enable a landscape desktop.
The UUID is `4E414255-2D47-4F50-8000-000000000001`; network identity is separate.
The personality matches IOPlatformExpertDevice; `/arm-io/native-gop` supplies
one `display-subsystem` descriptor to original userland display enumeration.

## Pinned SHENG comparison

The primary reference is UniXNU `268bfa1c587db5ee616e955cdfd97a281500a6fb`
and UniXNU-sheng `81f43ebaaa3317a5917b76093f7bc44c4949bc02`.
`package/LegacyDisplayProbe.cpp`, `scripts/build-gop-legacy-display.py` and
GopFramebuffer `NativeLegacyDisplay.cpp` separate provider/lifecycle, validated
swap, bounded IOSurface mapping, actual pixel transfer and retirement. Its
`OriginalMobileFramebufferABI.h` explicitly targets iOS17 and cannot be copied
into the 473-slot 25F71 family. This adapter therefore retains the independently
reviewed 25F71 queue/virtual dispatch implementation and supplies Nabu board
inputs, rather than relabelling the mainline binary ABI.

The inherited first-frame owner transaction claims GUI only for a source-backed
copy, uses scanline leases, rolls back on failure, and disables the text console
only after a completed pixel transfer. `start`/hotplug/first-swap messages do not
prove desktop presentation. Existing `sheng_*` framebuffer-owner and diagnostic
symbols are shared kernel interfaces, not a claim that this is Sheng hardware.

## CPU plane presentation (25F71)

`nabu_cpu_present.inc` uses the paired native family's layer contract, verified
against UUID `EA3273B7-AF0F-3E06-B0F5-072DFCDD5395`. In its original code:

- `swap_submit` at `0xfffffe000a8975e0` copies the user update mask `+0x14c`
  into request `+0x338`, source rectangles `+0xac` into request `+0x11`,
  destination rectangles `+0x10c` into request `+0x51`, and owns four IOSurfaces
  at request `+0xac0`. Rectangles contain signed x, y, width, height, confirmed
  by `writeLayerDebugInfo` at `0xfffffe000a89d538` and its `src_width`/`dst_width`
  dictionary keys.
- `getCurrentSrcRect_gated` at `0xfffffe000a89fd60`,
  `getCurrentDstRect_gated` at `0xfffffe000a89fdbc`, and
  `isCurrentLayerValid_gated` at `0xfffffe000a89ffa4` select request data only
  for updated layers; otherwise the retained surface at framebuffer `+0x1510`
  and rectangles at `+0x11e9`/`+0x1229` remain active.
- `updateDisplayedDataFromPendingSwap_gated` at `0xfffffe000a89b248` updates
  only mask-selected layers. A selected null surface clears that plane.
  An unset mask bit preserves it. A zero-area rectangle disables drawing.
- `swap_complete_gated(true)` at `0xfffffe000a8947dc` transfers updated
  surfaces to DisplayedData only after the synchronous pixel copy is done.
  Every CPU source read finishes before calling it. A retained mapping keeps
  its own descriptor/backing reference; no borrowed IOSurface pointer outlives
  native family ownership.
- `dropSwap_gated` at `0xfffffe000a89f2dc` unlinks the request itself, invokes
  `swap_complete_gated(false)`, and clears the current pointer. Because our
  dispatcher has already unlinked the current request, its failure helper
  first relinks it to the queue head. This avoids unlinking the next entry.
  The false branch retires resources without updating DisplayedData.

Each valid swap rebuilds a complete BGRA image in a 16,384,000-byte wired CPU
buffer using all retained scene planes and their clipped source/destination
rectangles. This preserves unchanged planes and restores pixels under cleared
or moved planes. Scene planes use opaque copy even if their alpha bytes are
zero. Plane 1 is identified as the cursor by the paired AppleParavirtDisplay
reference; only a plane-1 surface no larger than 256x256 uses premultiplied
alpha and is drawn last. Large plane-1 scenes remain opaque. Other plane blend
modes are not inferred from the update mask. Nonzero per-plane rotation is
rejected with native error retirement; portrait rotation zero is the supported
board mode.

Source validation or mapping failures leave the scanout untouched and use native error retirement.
After all source rows are staged, the first frame claims the shared framebuffer
owner and disables/drains the native text console before the first scanout store.
Native vcputc does not consult the diagnostic owner, so disabling it only after
the copy allowed it to overwrite GUI pixels. A failed first transfer restores
the console only while the owner remains GUI/BOOT; a panic keeps ownership. A later explicit
clear can present black. `frame=... copied=1` records actual BGRA/RGhA output
pixels, lit RGB count and center pixel; `complete=...` separately records the
subsequent native successful completion. These are content/completion evidence,
not a desktop assertion. The old `vbl` heartbeat counts software callbacks,
not measured hardware refresh.

The c hardware run reached the first real 1600x2560 layer but did not return
from the layer-copy region. Its live source descriptor type and exact wait were
not captured. The CPU path now matches the fixed GopFramebuffer direct read-only
mapping path: IOSurface owns the backing; the display does not call an extra
`prepare()`/`complete()` DMA cycle or fall back to repeated `readBytes()` windows.
Original 25F71 IOSurface allocation uses wired pool descriptors or an allocated
IOBufferMemoryDescriptor. Mapping lifetime covers every source pixel read and
may extend beyond native swap completion through its own backing reference. First four successful/failed frames record
mapping entry, readiness and completed CPU row reads to distinguish a source-map
wait from later scanout or retirement. This removes the extra VM wiring operation;
a specific prepare-lock deadlock and the hardware fix still require verification.

The d hardware run confirms direct source mapping and all 2560 CPU row copies
completed. Its last marker precedes the old map destructor; the unchanged native
console handoff was also before the next existing completed-frame marker. It
does not uniquely locate a deadlock. XNU IOMemoryMap::free synchronously takes
gIOMemoryLock, unmaps with mach_vm_deallocate and removes itself from the source
descriptor's mappings. The fixed SHENG CPU implementation also releases its map
synchronously, so a gate/unmap lock cycle is not established merely by reading
that source.

Nabu now retains one mapping per plane and reuses it for the same descriptor,
offset and span. Replacement transfers the old strong map reference to a
separate kernel worker. That worker has no display pointer and drains VM unmaps
outside the framebuffer gate. Its eight-entry queue permits four normal pending
maps and reserves four entries for stop; at most nine mapping references exist
across current slots, pending entries and the one unmap in flight. A full queue
refuses replacement, preserves the old slot and retires the new swap as an error.
Worker creation failure refuses display startup. Stop closes native activity,
transfers its four slots and returns without waiting for an unmap; the worker
frees its own lock/state after draining. No IOSurface ownership or GPU completion
is fabricated. First-frame stage markers distinguish source return, planes,
owner claim, console disable, scanout copy and native completion. Reaper entry
and return records are limited to four.

Host tests exercise the actual `.inc` with a mocked native family and source
memory descriptors under ASan/UBSan, including retained layers, cursor-only
updates/moves, clearing, mapping reuse, failures and queue retirement. A host
thread executes the actual reaper. Tests hold an unmap indefinitely and verify
bounded queue refusal, nonblocking stop, final map reclamation, worker creation
failure, console restoration on transfer failure and panic ownership preservation.
The arithmetic tests cover crop, scaling, clipping, BGRA zero alpha, cursor
blending and half-float conversion. Full kernel compilation still needs the
macOS builder and paired SDK; real panel appearance and WindowServer acceptance
remain runtime gates.

## Remaining runtime gate

CPU presentation requires valid BGRA/RGhA IOSurfaces from original WindowServer.
It does not publish a Metal device. The three-page software-compositor patch
and display path need to be qualified together on-device: changing real desktop
frames, successful native waits and completion, and a usable login session.
`runtime_qualified` and `compositor_qualified` remain false until then.
GPU fences are not fabricated. Rebuild the display-containing storage module,
relink its original 25F71 families, and rebuild the payload/kernel package.
