# GopFramebuffer

NABU CPU bring-up logs request/active masks, four surface slots, full descriptor
mappings and bounded 16x16 source samples for BGRA and RGhA. Raw half-float
nonzero samples are separate from converted 8-bit counts. Copied-frame RGB/alpha
counts describe the composed scene. The first ten copies, each fiftieth copy,
request/plane state changes and first nonblack copy are logged. Rejections have
numbered causes and the same bounded repeat policy. The boot framebuffer is write-combined.
Bounded eight-row red/yellow/cyan probes mark mapping/power/black-frame stages;
the first nonblack CPU frame permanently stops probe writes for that boot.
Probe visibility and desktop output still require device validation.

UEFI GOP framebuffer and MobileFramebuffer integration.

CPU and GPU scanout are supported. With `vsync=<Hz>`, a work-loop timer sends the MobileFramebuffer vsync message to the compositor.

Swaps carry one full-screen surface: 8-bit BGRA, plain or in the one-plane compressed spelling, or half-float RGBA. With `dpu-scanout=2` the display pipes fetch an 8-bit surface themselves. A half-float surface cannot be fetched: the GPU client converts it into the scanout memory (`fb-gpu=1`), or the CPU does when no client takes it. `fb-half=2` reads half-float values as already encoded. A render server that takes the device for a virtual machine presents half float. `native_legacy_formats` counts the swaps of each format, the swaps refused for their format and the frames on each path.

Direct display keeps the first successful GPU fence snapshot for each swap. Busy or unavailable queries retry after 200 microseconds. Reaching 250 milliseconds records a slow wait; the current frame stays on screen until the target completes or the display family cancels the swap. Cancellation clears the pending fence timer and snapshot.

Run the fence-wait regression check with `python3 Test/check-fence-wait.py`. It compiles the display's fence-wait and cancellation methods against a simulated GPU with AddressSanitizer and UndefinedBehaviorSanitizer.

Build and interface details: [documentation](https://unixnu.com/drivers/gop-framebuffer). Component sources and dependencies: [catalog](https://unixnu.com/reference/components).

## Frame diagnostics

`gpu-trace=1` records display arrival, fence and latch in the kernel GPU ring. Repeated pending display queries are coalesced. See [GPU frame diagnostics](https://unixnu.com/porting/gpu-diagnostics) for fields and decoding.

Display traces obtain physical addresses from prepared direct-display slots. Validation records use address zero until a slot is mapped; the flip record supplies the mapped address. Observers do not prepare memory or query unwired descriptors.

The macOS display attributes, the timing and color elements and the hot plug state follow the display driver of [macOS-DroidVM](https://github.com/Droid-VM/macOS-DroidVM), the DroidVM port booted with UniXNU.

The NABU macOS 26C5035r EDID/container child uses the native concrete
`AppleDisplay` class. `IODisplay` is abstract and its metaclass allocator returns
null. The child is attached without starting or registering another display
driver. Original allocator replay verifies the class selection. Host replay covers
dynamic surface selection, disabled layers, BGRA/FP16 sampling and log throttling;
device presentation remains unverified.

NABU macOS 26C5035r: native `isSwapLayerEnabled_gated` reads the bit at
request +0x478. +0x47c is accumulated by
`update_swap_completed_state_gated`; it is not a disable mask. Updated nonnull
IOSurface slots enter CPU composition; updated null slots clear the plane.
The CPU source log names the latter word `completed`, including independent
sampling of mapped incoming sources that are not selected for scanout.

For this firmware-owned primary panel, the owned override of native slot 345
(`setAPIPowerState`) rejects API power-off. The PM callback acknowledges while
keeping scanout. An all-null swap following rejected sleep keeps an existing
nonblack scene; ordinary explicit clears remain supported. This hold does not
claim support for physical panel power cycling. The v36 observer no longer
starts repeatedly failing caffeinate helpers. These source/replay checks still
require hardware validation of visible desktop content.

NABU P019: the 500us interrupt-masked debug budget formerly clamped ticket-lock
waits to 12,000 ticks at the virtual 24MHz timebase. The board now defaults that
diagnostic to TRACE; real ticket-lock protection remains 3,000,000 ticks
(125ms), and explicit debug overrides remain available. This does not establish
the owner CPU's complete critical-section stack.

The first successful CPU scanout takes the physical console from text/progress
drawing while retaining console-input RAM/ESP capture; panic can take it back.
An ordinary-RAM shadow skips unchanged scanout rows. No separate full-frame
RGB/alpha statistics pass runs: diagnostics use at most 256 samples for the
first ten frames and every 300th frame, with short geometry/sample lines.
The phase-2 owner tool installs a one-time Aqua static PNG configuration through
NSWorkspace, with URL readback; visible wallpaper still requires device testing.
