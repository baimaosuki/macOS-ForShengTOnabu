# Automatic session stack capture

The 13:17 native kernel's device journal still shows three completed-too-late
kauth requests (198.9, 131.0 and 72.1 seconds), a no-client WindowServer idle
exit and sustained LaunchServices CPU use. Fixing microtime and root identity
did not resolve these symptoms. Generic successful mach_vm_map traffic is not
evidence of a login connection. This change captures the missing process stacks;
it does not claim to repair the unknown user-space wait.

The existing signed ARM64 owner boot tool now accepts a boot policy
`nabu-session-probe=1`. During mount phase 2 it installs a one-shot launchd job
in the RAM Data volume. The job invokes the original signed macOS `sample`,
`ps` and `launchctl` tools. It records opendirectoryd, loginwindow, lsd and
WindowServer stacks, job state and process CPU state to `/dev/console`, the
existing console journal input. Three rounds start after a 90-second delay,
with 180-second gaps between rounds. Each sampling command has a 15-second
guard and each report contributes at most 256 KiB. The job then exits.

The probe is disabled without its exact argument. Installation failure is
reported without failing a successful mount phase. It does not restart jobs,
change OD membership results, create fake WindowServer clients or alter normal
service exit handling. No new executable architecture, root image or kernel
build is required. The boot addon and its explicit trust module must be rebuilt
for the changed owner tool; the native kernel can stay at CI 43224981411.

Host checks execute the actual opt-in installer through filesystem/sysctl
adapters and parse the resulting launchd plist. Both original mount phases
retain their existing checks. Eight tests and 17 subtests pass.

ADB reported no attached devices during preparation. Device execution and
console-to-journal export of these new reports remain unverified until tested.
If `sample` is unavailable or cannot acquire the process task, its error is
recorded rather than converted into a successful stack capture.
