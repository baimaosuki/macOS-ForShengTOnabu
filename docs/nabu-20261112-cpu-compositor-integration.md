# NABU 25F71 CPU compositor integration

The user requested CPU rendering first, then hardware GPU work. The 11:35 log
contains six WindowServer SIGABRTs in the original SkyLight Metal-device wait;
its exact abort branch is recorded in `nabu-20261112-windowserver-metal-abort.md`.
The `display type 0` message is not evidence of the fatal branch.

The candidate uses the actual 25F71 CompositorSW route. Three exact original
shared-cache pages are adapted only in memory after validation:

1. Complete the Metal-capability once callback with the original no-device
   state, allowing both the original compositor selector and shadow initializer
   to take their software/no-shadow paths.
2. Choose BGRA before the original QuartzCore framebuffer IOSurface allocation.
3. Choose BGRA8Unorm (80) before the original intermediate SurfacePool lookup
   and allocation, removing the known later RGhA failure.

The kernel has its independent `nabu-cpu-compositor=1` switch. It preserves the
current root/Data images, original OD and existing service integration. The
source policy is shared among users of those code pages; it is not a private
WindowServer mapping. Code-page hashes and instructions must match before
changing memory. Changed pages remain clean, and skipped bitmap updates make
reloaded original bytes pass validation and conversion again.

The fixed SHENG reference supplies an A740/Metal path, not an existing CPU
macOS desktop. Its display guidance separates render/presentation and actual
pixel verification. This candidate reuses the paired 25F71 original software
path and the existing CPU GOP copy driver. Historical QEMU continuous frames
were only black background/cursor evidence, not a NABU desktop test.

A fresh prepare succeeded and includes all three headers, the VM hook and
source hashes. Seven compositor tests passed, including actual original
machine-code replay of software selection, shadow state, framebuffer return
format and intermediate pool format. PAC and dispatch_once completion are
explicitly modeled, not hardware validated.

First CPU CI 43151782832 failed only because
`nabu_cpu_page_transformed` is unused when CHECK_CS_VALIDATION_BITMAP is off.
The declaration is now explicitly unused in that configuration; the original
warnings remain errors. Retry run and delivered package are recorded in
`out/nabu-service-startup/cpu-compositor/ci-run.json` and the final delivery.

The earlier OD overlay package is withdrawn. Its userland mount was refused
EPERM and the old optional shim made mount phase 1 fail. The fallback repair
logs inactivity and continues only when the original namespace is preserved.
Lightweight shim CI 43152484767 succeeded. The CPU package does not carry an OD
replacement, so it cannot activate that optional mount path. The rollback
sidecar package restores the previously booted services baseline and is not a
CPU-render package.

Device acceptance requires successful CPU page application, no repeated Metal
availability abort, continuing real GOP frames and an observable login/session.
Build/test success alone does not establish a visible desktop or useful speed.


## Successful build and package

Retry CI [43153060721](https://github.com/baimaosuki/macOS-ForShengTOnabu/actions/runs/43153060721)
succeeded at commit `9e163ddef672895cbdbd8487cafcf6976e6cd5eb` using stable Xcode
26.5 and the paired KDK. Actual native symbols include all three CPU policy
globals; the actual build record pins the three-page contract and headers.
Eight compositor tests now pass, including signature-bitmap-on/off compile and
a negative control reproducing the first build failure. No warning was disabled.

Final prelink has 35 original components and zero unresolved references. The
independent verifier passed 1000 pointer, 56031 branch and 1000 kernel-binding
samples. The final kernel is 314085376 bytes; UUID
`20A72158-0612-380B-B787-676D8FE7E664`, SHA-256
`777a259555beea514e44d7b7fbf67c6747d8874c9811dd56003f599430b59359`.

Package `out/nabu-service-startup/nabu-25F71-cpu-compositor-43153060721.zip`:
172171760 bytes, SHA-256
`1bd06e65a5e96a6b90a3bfb04fe80afc973631ba8b11bc7575724d4077710ad8`.
ZIP CRC and every packaged payload hash passed. The addon contains only the
signed apfs_boot_util, no OD replacement. This actual compiled tool includes
the corrected optional-overlay fallback. All addon CodeDirectories are in the
paired trust bundle. Original three Apple trust modules remain intact.

The actual packaged TOML differs from the installed 11:35 TOML by exactly one
argument: `nabu-cpu-compositor=1`. Existing logger token and other arguments are
preserved. Install all five ESP files, including TOML; an old four-payload-only
script would leave the new policy disabled. Root, Data, loader, partitions and
XNU2LOG.BIN do not need replacement. The device remained in TWRP and this work
performed no device writes. Desktop/frame output remains unverified.
