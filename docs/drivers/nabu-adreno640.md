# NABU Adreno 640, macOS 27.3

The source target is macOS 27.3, 26C5035r, native arm64 XNU 12377.121.6 with
Zeta ordinary-pointer PAC conversion. Do not replace the A640 hardware path
with A740 register, HFI, firmware, voltage or bandwidth tables.

The hardware backend is in `devices/nabu/package/gpu`. Preserved A6xx sources,
the A640 board overlay and macOS adaptations are recorded separately in their
PROVENANCE files and `sources.lock.json`. The production ADT exports an absolute
`/nabu-adreno` node: GPU 0x02c00000, GMU 0x02c6a000 and GPU SMMU 0x02ca0000.
This GPU SMMU is distinct from the 0x15000000 SMMU used by UFS and USB. GPU
streams 0/1/0x400/0x401 use CB0; GMU kernel streams 5/0x405 use CB1.
The vendor GMU user stream 4/0x404 is not substituted for the kernel stream.

A build links the backend and v2 publication but does not start them unless `nabu-gpu=1` is present.
After IOBSD and a bounded delay, it validates private A640 firmware, owns the
GPU/GMU contexts, performs bounded power/HFI/ZAP startup and submits a scratch
write and timestamp fence. Only the observed scratch and CP-written fence make
`proof=cp-scratch-fence` successful. Failed or uncertain DMA retains resources.
Normal boot continues to use NABU27-CPU; this source step does not claim a Metal
desktop or measured GPU throughput.

Set `NABU_GPU_FIRMWARE_DIR` to the owner's local A640 firmware directory when
making an ADT. Required inputs are a640_gmu.bin, a630_sqe.fw, a640_zap.mdt and
a640_zap.b02. They are checked against the recorded board inputs and never
committed or uploaded to CI.

Portable SMMU transactions preserve the A640 fixed arena and stream contract.
An exclusive map uses the VM's physical alias checks and publishes invalid leaves
without TLB invalidation. Unmap uses TLBIVAL for up to 512 pages and ASID
invalidation above that. Submission never uses a map as proof of completion.
`log-gpu=1` emits limited `[vm map]`, `[vm unmap]` and `[bo memory]` counters.

Host evidence: real SDK compile/link of 40 native GPU objects; 42 objects in the local
closure check including the actual presenter and production logger accessor; execution of the actual ARM64 ring against a software register/fence
peer; ASan/UBSan tests of page invalidation and timeout failures; stock DT versus
production ADT checks. These are not hardware qualification.

The current M2V source pin is 46e7cc96, with AdrenoMetal patches 0063 and 0064.
The required Mesa c823a83 now has a complete recorded Darwin backport of the
24 SHENG patches (original patch base 85fda3b). The patch guard reconstructs
c823 exactly. Local SDK 26.5 cross builds have linked Turnip, the M2V static
runtime and the macOS plugin for ordinary arm64. The final plugin and Turnip
text scan rejects authenticated branches and XPACI/XPACD. Clang explicitly
uses `-mcpu=generic` with ARMv8.2 crypto: Darwin's implicit Apple CPU otherwise
emits register XPACI for return-address diagnostics. The generic target uses
backwards-compatible HINT #7 instead. These binaries have
not been loaded on macOS or tested on the board.

`prepare-nabu-m2v27.py` stages the pinned upstream with arm64 Rust/Clang,
A640 render defaults, no Rust PAC wrappers, and arm64 Shader Cache tools.
`build-nabu-m2v27.py` runs the matching build and 4 KiB signing on macOS.
The signed userspace artifact is reused by `integrate-gpu-esp27.py`.
The A640 overlay now uses wire v2, 128 fixed-submit IBs and selectors 17/18 for
priority/note. Owned user BOs use no-task pageable WC backing; cached imports
use explicit cache maintenance and NC GPU mappings. No OwnedCoherent bit is set.
The native IOAccelerator publication and connection-owned GOP handoff are
compiled with NABU_GPU_METAL=1. Only the independent A640 Metal menu entry starts
them; CPU boot remains available. Full native CI 44630035429 passed at fd0845d;
the hardware rendering path is still untested.

Userspace CI 44614135059 at 19f8c3b passed compilation, 4 KiB signing and strict
signature verification with SDK 26.5. Its final text scan covered 6,873,990
instructions without unsupported PAuth. The signed addon was merged into the ESP with an independent GPU boot entry;
installation on a device, frontend Vulkan runtime and board execution remain untested.

Userspace CI caches the complete Rust bridge in addition to Cargo objects.
Reuse binds the bridge and translator inputs, ABI, CPU features, LTO settings
and compiler versions; the upstream builder also verifies its source identity
and archive hash. Incomplete or corrupt caches use Cargo. The host cache test
does not replace the frontend Vulkan or board tests.

The optional GPU profiler lazily allocates its unchanged 4096-record sample
and job arrays. Removing their 1,163,264 bytes of static BSS keeps the native
DATA placement within the 26C5035r retained-kext boundary. Host ASan/UBSan tests
exercise both allocation failures, concurrent requests and the sample/job lifecycle.

Audit follow-up: the signed 19f8c3b Shader Cache tools and LaunchAgent use
`/Applications/Shader Cache.app`. The nabu installer now routes that exact addon
prefix to the Data-backed `/Applications` with the kernel context; signed bytes
and CodeResources stay unchanged. Other addon destinations are preserved.
WindowServer and backboardd share render-server export/diagnostics policy.
Submit caps report KernelCacheMaintenance rather than hardware coherence, and
legacy, V2 and async submissions apply the same foreground/background priority.
Host replays cover the routing and all three submission paths. Full native CI
44640851880 at 49d151a, final prelink/replays and FAT/ZIP verification passed.
Device validation is still pending.

GPU launch now belongs to the board BSD late hook, after genuine IOBSD
publication; it has no static STARTUP registration. The worker waits up to
120 seconds for this boot's writer state 2 and a completed fixed-snapshot
commit before allocating A640 state or touching MMIO. Later checkpoints retain
a 5-second bound and tolerate transient writer contention. This corrects an
initialization race, not a proven explanation of the photographed pre-MMU bars.
The former startup record actually used EARLY_BOOT=18, after task/thread init;
arm_init's barrier 4 is KPRINTF. Source and binary checks keep that distinction.

P028 full native CI 44694522097 at 8b122cc and final-kernel startup/PAC/
signed-text/display/log replays passed. FAT readback and ZIP CRC passed.
The native __DATA layout remains below the retained Libm boundary. Device
startup and visible A640 rendering remain untested.

P025 hardware logs subsequently confirmed GMU/HFI startup, PAS13 authentication,
CP scratch readback `6401cafe` and fence completion. WindowServer nevertheless
aborted in `WSSystemCanCompositeWithMetal`: the delivered addon omitted
`GPUBundles/NativeAdrenoMetal.publish`, so the plugin's initializer selected its
diagnostic path and returned nil instead of a Metal device. Production ESP
integration now adds this readable sibling marker outside the signed bundle.
The host regression executes the actual plugin mode gate and preserves all
signed bytes and trust modules. A subsequent `CP_OPCODE_ERROR` in the same device
log is a separate unresolved command fault; neither this correction nor the
successful bootstrap proves a working Metal desktop.

The GPU entry also enables `nabu-gpu-scanout=1`: the signed presenter imports
the complete firmware GOP allocation through a no-copy Metal buffer. Without
this gate, memory outside managed RAM fails the ordinary import cache query.
The gate still requires the exact current GOP physical extent, 32-bit geometry,
contiguous pages and writable access; arbitrary device memory is not admitted.

Client teardown cancels a scheduler range only after setScope or an actual
submission establishes ownership. Previously a failed unscoped open could close
owner 0 and cancel WindowServer's pending jobs. The regression reproduces that
failure using the old production functions and passes with the corrected ones.
The ARM64 ring replay now covers 400 framed IB submissions, packet parity and
lengths, new-context entry and one ring wrap, in addition to the scratch path.
It does not execute firmware or shaders. A640 fault capture preserves the first
CP error and reads the stock SQE opcode selector before CP shutdown, with bounded
MMIO and explicit read-valid bits; this adds missing evidence for the unresolved
P025 opcode error without claiming that an offending packet is already known.
