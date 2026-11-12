# 25F71 CPU compositor candidate

`nabu-cpu-compositor=1` is an explicit development policy, off by default. It
changes three original, hash-pinned shared-cache code pages in memory after
original page validation; it neither rewrites the cache nor changes the root
image. Scope is shared executable pages, **not process-private WindowServer
patching**. No GPU or Metal device is fabricated. SHENG 27.3 supplies an A740
GPU path, not a reusable CPU desktop implementation.

`prepare_nabu_software_compositor.py --cache <original dyld directory> --output
<staging directory>` verifies both original cache UUIDs, all three complete
16 KiB source hashes, edit preimages and resulting hashes. It emits
`nabu_cpu_compositor_pages.h` for the kernel pager policy. No private Apple
source pages are emitted.

The original SkyLight Metal-capability once callback returns with its original
zero-device state. PACIBSP is preserved and paired with AUTIBSP/RET; original
dispatch_once completion remains responsible for both default compositor and
shadow selection. The original default-selection block interprets no device
by selecting `activate_compositor_sw`, and the shadow initializer disables
shadows. This changes capability policy, not the `abort` call or a Metal
success return. Inspection of these callers found no environment/configuration
read before their unconditional shared capability initialization; an environment
variable alone cannot select this complete route on the inspected 25F71 code.

The original QuartzCore IOMFBDisplay framebuffer_pixel_format returns BGRA
before allocation. Separately, SLCADisplay::begin_update chooses texture format
80 (MTLPixelFormatBGRA8Unorm) before intermediate SurfacePool matching and
allocation. Existing IOSurface format queries and unsupported-format checks
remain intact. The latter page is necessary: historical QEMU ten-frame black
background/cursor proof used two edits, then a longer session crashed with RGhA
in the intermediate target (history lines 2097–2107). A two-page candidate would
retain that known subsequent failure. The current cache's source offsets differ
from the old Recovery manifest; every site was relocated from current symbols
and disassembly, never copied by old file offset.

This candidate supplies original software rendering plus CPU framebuffer
presentation; it does not prove a visible login desktop. Required runtime
observations are all three policy pages accepted, no repeated Metal timeout,
valid allocated BGRA IOSurfaces, continuing original swaps and completed GOP
frames, followed by an actual visible system session. Authentication, keychain,
LaunchServices and input readiness remain independent gates. Historical QEMU
continuous frames are reference evidence, not a Nabu hardware qualification.
