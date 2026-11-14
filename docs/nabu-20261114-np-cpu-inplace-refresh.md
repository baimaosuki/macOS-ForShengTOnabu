# NP: restore native services and copy explicit CPU plane updates

NP original BIN MD5 0b1d39415a48d409a4c1bc12ae9fba38, selected slot seq50533,
904789 committed bytes, contention loss315 bytes, no overwrite. All5022 decoded
logical lines were classified; last timestamp291.000348229 seconds. There are no
NABU-MINIMAL decisions. The run submits90 requests and copies3 real black frames.
Queue audits directly show requests4..16 retired by native dropNoOpSwaps_gated;
audit stops after16, so the specific helper for each later request is unobserved.

The no-op method is original 25F71 code, UUID EA3273B7-AF0F-3E06-B0F5-072DFCDD5395.
It first compares request+0x338 and+0x33c, then calls swapWillChangeDisplayedData.
The latter compares retained surface identity and geometry/metadata, without
examining modified pixel bytes. Hardware can continue scanning that same memory;
a GOP CPU snapshot remains stale unless the adapter copies it again. NP establishes
missed copies for explicit plane updates; it does not establish that the missed
source bytes already contain the login interface.

NABU clean_queue_gated now preserves an explicit update to any supported plane
(mask&0xf) for the real CPU presenter. It reads the live head after obsolete cleanup,
preserves the native aborted/dummy checks and normal completion/ownership, and
keeps native no-op handling for zero-plane/mode-only requests. No base-family code
or exported ABI changes. The generated method plus production presenter is tested
under ASan/UBSan with90 writes to one mapped IOSurface, identical ID/address and
geometry: all90 copies reflect the new pixels before native completion. A mode-only
request still uses native no-op cleanup. The mapping fixture now aliases live
backing bytes, as an actual mapping does, rather than snapshotting them at map time.

swap_wait is unchanged. Original instructions at0xa895418 AND options with0x7fffffff;
0xa8955d0 compares the real requested ID against last_displayed_id; mode0/nonblocking
returns Busy for that retained ID. It is not comparing0x80000000 to the displayed ID.
The existing six original-machine-code tests cover this ownership contract. Forcing
success for swap_id<=last_displayed_id would change native buffer-reuse semantics.

The package removes nabu-minimal-boot from boot arguments. The unfinished alternate
native-launchd-policy source experiment is not included. Only the existing exact
watchdogd bring-up policy remains; no broad xpcproxy holds or new disabled job list
are enabled. SHENG27.3 macOS guide and its pinned BootWatchdogPolicy11080685 preserve
native service infrastructure and use explicit launchd overrides rather than holding
all unknown jobs. Its service list belongs to its target and is not a NABU whitelist.

K20 Pro/SM8150 reference review is documented in
nabu-20261114-sm8150-cpu-render-reference.md: CPU scanout copies real IOSurfaces,
keeps per-layer ownership, and completes after copying. iOS wait/payload layouts
are not substituted for the paired25F71 native family. No desktop success claim
is made; device execution of this display change is still required.
