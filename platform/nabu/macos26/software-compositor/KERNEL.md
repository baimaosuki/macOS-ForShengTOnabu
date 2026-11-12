# CPU compositor page policy

The independent `zzzzz-nabu-cpu-compositor.patch` extends the existing development
`vm_page_validate_cs_mapped_slow` hook. Stage these headers before building:

| Source | XNU destination |
| --- | --- |
| `policy.h` | `osfmk/vm/nabu_cpu_compositor_policy.h` |
| `kernel.h` | `osfmk/vm/nabu_cpu_compositor_kernel.h` |
| `nabu_cpu_compositor_pages.h` | `osfmk/vm/nabu_cpu_compositor_pages.h` |

`nabu-cpu-compositor=1` enables exactly three generated page rules. The default
is off. The hook is compiled only for NABU development/debug builds and requires
`CSR_ALLOW_UNAUTHENTICATED_ROOT`. It rejects concurrent `sheng-recovery-ui` use.
It validates the original signature result, busy/clean/non-writable page state,
vnode basename, exact source offset, full 16 KiB SHA-256 and all original edit
bytes. A mismatch panics instead of silently changing a different image. No
code-signature validation/taint flags are changed by this helper.

This is a shared cache **memory** policy. It is not restricted to a WindowServer
PID, and can first run when another process faults a target page. Main/text cache
UUIDs are pinned by the source generator and recorded in the generated artifact;
the fault hook has no cache header in hand and uses the exact authenticated full
page digest plus source name/offset as its runtime image selector. It does not
claim an independent runtime cache-UUID check.

The source page remains busy under its VM object lock while edits are applied
through its physical mapping. The helper synchronizes the physical instruction
cache before returning. It neither uses user `copyout` nor changes a user mapping
to writable. Existing consumers of the shared vnode page can observe the policy.
The original cache files remain unchanged, and converted pages remain clean and
can be discarded. Only successfully converted pages skip validation-bitmap SET;
therefore a reload of original disk bytes must be validated and converted again.
A resident validated page uses the existing VM fast path. If unrelated machinery
clears validation and actually revalidates modified bytes, normal validation can
fail; this policy never repairs taint or fabricates a validation result.

The no-PAuth compatibility code decodes pointer rebases/data. These executable
cache pages are not changed by that decoder, so their preimages are the original
cache bytes. `dyld_pager` and `AppleProtectPager` do not cover all ordinary shared
cache executable pages; this VM validation hook covers that case without copying
the entire cache or introducing a new pager.

Globals for a debugger or future bounded status export:

- `nabu_cpu_compositor_enabled`: requested boot value, default 0.
- `nabu_cpu_compositor_pages_applied`: number of new page applications, including
  possible applications after discard/reload.
- `nabu_cpu_compositor_page_mask`: bits 0–2 for rules seen; complete mask is 7.

Each successful new application emits one existing kernel printf line. This is
an application record, not proof that WindowServer called CompositorSW or created
an IOSurface. Runtime qualification requires original software-compositor calls,
actual BGRA IOSurface allocation, and observed submitted/copied frames.

Host validation on 2026-11-12:

- `tests/test_nabu_cpu_compositor.py`: compiled real policy, digest rejection,
  preimage rejection, atomic rejection, postimage rollback, overlapping/range
  rejection, idempotence, and default/guard/hook checks passed.
- A separate mock-VM harness compiled the actual kernel header with real SHA-256
  and fed all three original cache pages. Each produced its pinned postimage;
  disabled mode left bytes unchanged, dirty/mutated pages were rejected, repeat
  application did not resynchronize, count was 3 and mask was 7.
- `git apply --check` passed against the prepared XNU source after existing 0054.

Compilation and device execution are tracked by the integrating agent. These
host checks do not qualify CPU rendering or successful login on the device.
