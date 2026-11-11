# NABU 25F71: launchd EBADARCH after root selection

## Device evidence

Photo `/tmp/codex-clipboard-e4b9cc02-bc81-4ff0-bee3-6053108c5639.png`
and the pasted panic report show:

* Kernel UUID `D380554C-83A2-3C47-BBE7-74A9548C0980`, matching the native
  kernel built by CI run 42893637356. Prelink/probe edits retain LC_UUID.
* Data mounted, 17 firmlinks configured and 2 absent optional targets skipped.
* AppCryptex mounted, stage 5/status 0.
* `load_init_program` attempts `/sbin/launchd`, then panics with errno 86
  (`EBADARCH`) at the final PID1 exec error path.

This confirms progress past BSD root selection, root mount and the owner
Data/App preparation. It does not show launchd's first user instruction.
The ESR/FAR carried by the panic hook are not the architecture rejection
location or evidence of an independent launchd data abort.

## Exact admission mismatch

The original 25F71 OS samples of both launchd and dyld have CPU subtype
`0x80000002` (versioned arm64e ABI0). Their chained-fixup segments use format
12 (`ARM64E_USERLAND24`). See the recorded SHA256 values in
`out/nabu-arm64e-userspace-build/exec-evidence.json`.

`ml_grade_binary` in `bsd/dev/arm/kern_machdep.c` admits subtype0/1 on the
ARM64_V8 host but returns zero for ARM64E. `exec_mach_imgact` converts that
zero grade to EBADARCH. `parse_machfile` independently grades the dyld image.
Therefore the change must cover normal grading for both images.

## Why a grade-only edit is incomplete

The existing ARM64 build excludes ARM64E dyld-pager cases under
`HAS_APPLE_PAC`. Original format12 binaries would be admitted without their
page-in relocation decoder. Shared-cache slide versions3/5 decode auth
rebases but place their writeback inside `__has_feature(ptrauth_calls)`;
the ARM64 build leaves the encoded chain word untouched.

The older crypto/PAC exception source also needs the corrected PAC/AUT/XPAC,
branch and load semantics in source form. An old binary cave injector has
fixed addresses for a different kernel and cannot serve as build integration.

The targeted correction is a NABU ARM64 execution-compatibility path:

1. Accept the supported versioned ARM64E ABI through the native grader.
2. Pass the existing `ptrauth_disabled=1` apple-vector contract to dyld and
   retain no-JOP mode without claiming hardware PAC capability.
3. Compile ARM64E pager decoders, emit plain decoded targets, preserve chain
   validation and propagate decoding errors.
4. Write decoded auth targets for shared-cache slide3/5.
5. Handle supported PAC/AUT/XPAC/RETAA/branch/load instructions before the
   crypto decoder through source-built exception handling.

The original signed launchd, dyld, caches and root policies are retained.
The working 2 GiB request and mainline root-matching dictionary remain the
baseline. These compatibility changes do not implement cryptographic PAC
authentication; code-signature and owner-root authentication retain their
separate existing checks.

Implementation and host validation are in progress. No corrected kernel or
successful EL0/desktop boot is claimed by this document.
