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

A build links the backend but does not start it unless `nabu-gpu=1` is present.
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
The requested Mesa c823a83 is retained. Mainline's separate compiler manifest
still selects 85fda3b and its patch set does not apply unchanged to c823a83
(ir3_disk_cache.c has moved). That patch port and the wire ABI adapter remain
required: the A640 core uses ABI v1, while the current mainline client uses v2.
Do not publish a v1 service under a v2 runtime or silently reinterpret its flags.
No plugin/addon is enabled by this checkpoint. BO no-task/pageable migration,
macOS accelerator base qualification, shader prewarm and GPU scanout still need
independent implementation and hardware evidence; see gpu-port27.json.

The optional GPU profiler lazily allocates its unchanged 4096-record sample
and job arrays. Removing their 1,163,264 bytes of static BSS keeps the native
DATA placement within the 26C5035r retained-kext boundary. Host ASan/UBSan tests
exercise both allocation failures, concurrent requests and the sample/job lifecycle.
