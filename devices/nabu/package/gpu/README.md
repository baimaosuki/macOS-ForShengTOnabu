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

Host evidence: real SDK compile/link of 31 GPU objects plus the production logger
accessor; execution of the actual ARM64 ring against a software register/fence
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
them; CPU boot remains available. Host compilation/tests and pending full native
CI are distinct from the still untested hardware rendering path.

Userspace CI 44614135059 (19f8c3b) compiled and 4 KiB-signed the plugin,
Turnip, probe and three Shader Cache programs with Xcode 26.6 / SDK 26.5.
Strict signature verification and the 6,873,990-instruction text scan passed.
The separate addon has no publish marker and is not installed or added to an
ESP. Frontend Vulkan runtime tests, wire integration and board tests remain open.

The userspace CI retains both Cargo objects and the complete Rust bridge
archive. Complete-archive reuse requires the same bridge/translator inputs,
ordinary-pointer ABI, CPU features, LTO settings and compiler versions, followed
by the upstream source-identity/hash check. Missing, partial or corrupt caches
fall back to Cargo. The host cache check exercises reuse and incompatible-input
rejection; this cache changes build time, not the hardware qualification state.

The optional GPU recorder allocates its 4096-sample/job arrays on the first
profiling request, not in static BSS. This keeps the native DATA segment below
the first retained 26C5035r kext (Libm at DATA + 0x284000). Allocation failures
and concurrent requests are exercised against the production recorder with
ASan/UBSan; no request or partial allocation can enable profiling.


P026 hardware follow-up (2026-11-20): the installed publish marker is present,
and the production source identity matches P030 despite the unchanged logger
build ID. Scratch/fence 1 succeeds, but the first framed IB submission fails
with CP_OPCODE_ERROR (opcode register 0, RPTR=WPTR=51, submitted/completed=2/1).
WindowServer subsequently aborts in WSSystemCanCompositeWithMetal; no rendered
GPU desktop is established. This log has no ring/IB snapshot and cannot identify
the actual offending instruction from opcode 0 alone.

Legacy BO view reuse now preserves the same WC/WB map options as the first
mapping. The previous cached-view branch returned default cache flags, allowing
a second mapping to disagree with its allocation on this NC GPU path. Host
regression executes the production method and reproduces the prior failure;
this does not prove it caused the P026 first-submission fault. The production
resource-submit path now retains a bounded first-IB head, and a CP failure prints
only the latest ring/IB snapshot once, including read status and captured length.
No arbitrary physical alias or per-frame pixel scan is added.


P027 hardware evidence confirms the P031 USB correction: the apps SMMU uses
USB slot4/CB55, verifies UFS slot2/CB1 unchanged, and native AppleUSBXHCI starts
and creates ports. External keyboard/mouse enumeration is not yet demonstrated.
GPU source64496146 still faults at fence2/1. The complete 26-word ring is
well-formed; the 28 captured IB words match Turnip's A6xx initialization prefix.
Only 28 of its 182 words were captured, so the failing instruction is unknown.
The local A630 SQE disassembly confirms handlers for the captured PM4 opcodes;
this is static firmware evidence, not execution or memory-visibility proof.

Legacy owned-BO CPU views now retain the prepared IOBufferMemoryDescriptor
itself, preserving the VM-object/cache/lifetime relationship used by the V2
mapping path. Physical-range descriptors remain only for borrowed/imported
backing without an owned descriptor. Host tests cover original-object identity,
WC/WB mapping reuse, borrowed fallback and failed-publication reference cleanup;
the previous production function fails the object-identity regression. Whether
this resolves the P027 CP failure remains a hardware question. Fault records now
include the A6xx IB1/IB2 base/remaining registers and up to 256 IB words. Remaining
counts exclude prefetched words and are not labeled as an exact faulting PC.
