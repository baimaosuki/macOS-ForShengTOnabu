# GopFramebuffer

Start with the [adaptation procedure](/porting/display) for board inputs, source changes, build integration and verification order.

Framebuffer presentation using the firmware GOP handoff.

[Source repository](https://github.com/UniXNU/GopFramebuffer).

The implementation reads the firmware framebuffer through PE_current_console and integrates with the selected MobileFramebuffer ABI. Board hooks supply display policy and GPU scanout integration.

Direct display fixes each swap's target at its first successful GPU fence query. Busy or unavailable queries retry after 200 microseconds. A 250-millisecond wait records a slow frame and continues waiting. The previous frame remains displayed until completion or swap cancellation; cancellation clears the retry timer and target.

## Integration

The catalog records implementation scope, hardware and OS requirements, board dependencies, revision, build stage, outputs, and runtime identifiers. The selected profile orders build commands by dependency.

```sh
./unixnu build kernel -D /path/to/device
```

[Component catalog](/reference/components)

Frame diagnostics retain allocation, command, completion and GPU/display timing. Pixel capture is disabled. See [GPU frame diagnostics](/porting/gpu-diagnostics).

Frame diagnostics read physical addresses from prepared direct-display slots. Validation reports zero until mapping, and the flip records the mapped address. Diagnostic observers do not prepare descriptors or query their physical segments.

The NABU macOS 26C5035r EDID/container child uses the native concrete
`AppleDisplay` class. `IODisplay` is abstract and its metaclass allocator returns
null. The child is attached without starting or registering another display
driver. Original allocator replay verifies the class selection; compilation and
device presentation of this change remain unverified.

NABU CPU records the first ten frames, every fiftieth frame and state changes.
Request masks and slots are separate from retained active layers. Source samples
cover BGRA and RGhA; raw half-float nonzero counts distinguish quantization from
an already black source. Unsupported formats, including unimplemented 10-bit
layouts, retain validation failure and now report its cause.

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
