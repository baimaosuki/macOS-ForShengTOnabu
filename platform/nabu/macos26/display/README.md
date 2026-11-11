# Nabu 25F71 CPU scanout

Build with `prepare_macos26_gop.prepare(root, output, board="nabu")` and the
original **25F71** IOMobileGraphicsFamily, IOSurface and IOAVFamily. Enable with
`native-gop-display=1 cpus=1`. The unchanged default is off. No A740/SM8550
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

## Remaining runtime gate

CPU scanout consumes already-composited BGRA/RGhA IOSurfaces; it does not supply
a Metal device or a general compositor. The mainline OfflineGOPPresentation
instrument explicitly creates a real Metal device and waits for completed GPU
work. Its separate synchronous software-surface experiment does not establish
that stock 25F71 WindowServer can create its compositor without a GPU. Until
original WindowServer emits valid root swaps, completes waits, and produces
observable changing desktop frames on Nabu, software desktop rendering remains
unqualified. `runtime_qualified` stays false. GPU fences are not fabricated.
