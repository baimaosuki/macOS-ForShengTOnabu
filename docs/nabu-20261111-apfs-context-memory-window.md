# NABU 25F71 APFS context allocation and managed-memory correction

## Device observation

Photo `/tmp/codex-clipboard-b34df035-5eed-43ad-8673-32ca7ffc1fe1.png`
shows C04–C12 with remaining count `0x240`, then another C02/C03 with the
same remaining count. The last visible marker is C03, typed-view address
`0xfffffe000a873218`. C04 is not visible after that last C03.

The caller requests `0x68` bytes from native `OSObject_typed_operator_new`
at `0xfffffe0007a593c8`. A preceding iteration has succeeded, so the photo
does not establish that the type address or size is always invalid.
Concurrent scheme traces do not carry a unique scheme identity in every C
line. These boundary markers also cannot exclude a stop in the logger's
tail before the original allocation call executes.

## Confirmed configuration defect

The delivered ADT SHA256
`f8f156e736fb720d874dd137115996d59d4c104d54c5e7046f732deb88709398`
contains `/chosen/darwinboot-memory-size = 0x20000000` (512 MiB), while
`dram-size = 0x140000000` (5 GiB).

The actual loader source and EFI hashes match
`out/nabu-desktop-loader-final/build.json`. Its `Boot()` assigns both
`MemorySize` and `MemorySizeActual` from `WindowSize`. XNU's `arm_vm_init`
uses `args->memSize` to establish `gPhysSize` and `mem_size`; the subsequent
maxmem check only shrinks it. The DRAM property populates `gDramSize`, not
the managed-memory size. No NABU override extending the window was found.

The delivered image spans 308,584,448 bytes and retains a 21,110,928-byte
packed seed. They alone occupy approximately 315 MiB, leaving less than
198 MiB before page tables, VM metadata, zones, IOKit allocations and sparse
Data resident pages. The previous assembly check established payload fit,
not an adequate runtime memory budget.

The allocator permits blocking waits: empty zones can wait for an expander,
physical pages or zone-map GC. No runtime free-page or expander snapshot has
yet proved which path is responsible for this device stop. The 512 MiB cap
is confirmed; its causal responsibility for this stop remains a hypothesis.

## Mainline comparison

Reference `.work/sheng273-reference/UniXNU-sheng/board.toml` has a base
owned-memory request `0x130000000`; its current macOS 27.3 override requests
`0x180000000` and supplies excluded-range metadata. These mechanisms must
not be copied to NABU as an assertion of ownership. The NABU loader keeps
its existing requirement for one real contiguous conventional-memory run
and successful EFI `AllocatePages` before handing the window to XNU.

## Implemented change and delivery

`scripts/assemble_nabu_desktop_candidate.py` now defaults to a 2 GiB request
and accepts `--owned-memory-size`. It checks range, alignment and payload
fit, and records that host assembly does not establish runtime ownership.

`out/nabu-memory-window-2g/nabu-25F71-memory-window-2g.zip` is a small ADT
overlay for the already deployed context-probe kernel. Only one byte of
the 8-byte memory-request property changes. All other decoded properties
and all bytes outside that field are identical. The package also includes
the previous ADT for rollback. No kernel or root-image replacement is needed.

Updated ADT SHA256:
`9d0d7ddc010b8a2e405171e29cf913d73f9870b4d0e3c8a93592cc4b981ed1a1`.

Validation: full ADT audit passes; the actual loader's WindowSize header
accepts 2 GiB and rejects unaligned values; 3 desktop-memory tests pass.
The extra existing native-deftree suite has one unrelated fixture failure:
the external Sheng generator rejects its `--os-environment`/`--panic-log`
arguments. The overlay has not run on hardware. No ADB or fastboot device
was visible from this host during preparation.

The next device result must distinguish loader allocation failure (`NO
WINDOW`), the same C03 stop, or progression to S04/S05/later APFS work.
Progress alone would support memory pressure as a cause, not prove that
the complete macOS boot path is working.

## Subsequent device result

Photo `/tmp/codex-clipboard-e8511901-480d-4739-9d84-70d66d853eb9.png`
after delivery of the 2 GiB overlay shows:

* `NativeUfs: OS volume 47719F50-5527-44A3-9F59-8170528D8C14 ready`.
* S07, S08, S09, S10 and an earlier S11 registration-return marker.
* `dev_init` for disk6: 4096-byte blocks, 3,191,296 blocks, matching the OS
  window length.
* `nx_mount`: cache initialization, checkpoint search and stable checkpoint
  indices; `Macintosh HD` and `AppleAPFSVolumeBSDClient` service registration.

This is device evidence of progress beyond the previous context-pool stop
and into real APFS container/volume discovery. Together with the intervening
memory-only update it supports inadequate managed-memory headroom as a cause
of the previous stop, without identifying the exact allocator wait.

The photograph contains no explicit new panic or APFS error. Volume service
registration is not evidence that BSD has completed mounting `/`, that PID1
is executing, or that the desktop is running. Preserve this 2 GiB baseline;
the next boundary to establish is BSD root selection and completed root mount.
