# NABU 25F71: align BSD root selection with sheng mainline

The repeated device photograph
`/tmp/codex-clipboard-7b322b35-ddf9-4b5f-a189-6b8851a0f36d.png`
still ends after OS-volume discovery and AppleAPFSVolumeBSDClient registration.
No BSD root selection or new panic is visible.

## Confirmed contract mismatch

The delivered 2 GiB ADT lacks `/chosen/root-matching`. The entry supplies
`rd=uuid boot-uuid=47719F50-5527-44A3-9F59-8170528D8C14`. In this branch,
`IOFindBSDRoot` publishes a `boot-uuid` string and waits for an IOResources
service matching **both** `IOBSD` and `boot-uuid-media`. The real APFS volume's
UUID/BSD properties alone do not satisfy that resource dictionary.

The actual staged NativeUfs readiness gate requires a unique registered,
active AppleAPFSVolume with matching existing UUID, Whole=false, and BSD
name/major/minor properties. It does not stamp identities and does not
publish `boot-uuid-media`. Therefore `OS volume ... ready` is not a false
positive, but does not complete the selected resource-based root protocol.

The original BootKC includes `com.apple.driver.AppleFileSystemDriver`, whose
TEXT contains `boot-uuid-media`. It is not in this candidate's 13 Apple
component selection. No claim is made that it is the only possible publisher;
its absence is an additional mismatch with relying on that protocol.

Mainline sheng's macOS 27.3 profile instead specifies
`payload.adt.root_match_volume_uuid`. Its documented contract is:

```xml
<dict><key>IOProviderClass</key><string>IOMedia</string>
<key>IOPropertyMatch</key><dict><key>UUID</key>
<string>47719F50-5527-44A3-9F59-8170528D8C14</string>
</dict></dict>
```

The UUID here is NABU's verified 25F71 **OS volume** identity, not sheng's
27.3 identity and not NABU's SystemOS identity.

References: `.work/sheng273-reference/UniXNU/profiles/os/macos-27.3-26C5035r/profile.toml`
(`[payload.adt]`), `UniXNU/docs/concepts/userdata.md`, and
`UniXNU/scripts/qemu-entry/make-dt.py` lines 91–95.

## Implementation and actual consumer verification

`configure_native_text_state()` now supports `root_match_volume_uuid` and
adds the mainline XML+NUL plus matching chosen boot/preboot UUID properties.
Conflicts and invalid UUIDs fail without changing the source. Desktop
assembly derives the UUID from the validated OS policy.

The actual delivered native IOFindBSDRoot at `0xfffffe0007b56a78` reads
chosen root-matching, casts its property to OSData and calls
OSUnserializeXML at `0xfffffe0007b56cf8`. On a dictionary it takes the branch
at `0xfffffe0007b56d1c` before the boot-uuid branch. `mediaProperty` remains
null, so the service returned by the matcher is the real IOMedia itself.
This preserves subsequent BSD-device selection and root authentication.

## Delivery

`out/nabu-root-matching-2g/nabu-25F71-root-matching-2g.zip` contains the new
ADT, its previous 2 GiB version for rollback, instructions and a report.
It preserves every old property, including the 2 GiB memory request, and
adds only the three chosen properties. No kernel rebuild, root reflash or
TOML change is required.

ADT SHA256: `8a731c12d0cf68e4649c9cac8fdeb8fef4295137e9f2003e1c41c0fac1bc4bb4`.
Host validation: full ADT audit, 2 root-matching tests and 3 desktop-memory
tests pass. Actual serialized XML and idempotence were independently checked
on the delivered ADT. Not yet tested on the device.

Expected evidence: the wait dictionary now names IOMedia+UUID, followed by
`Got boot device` and `BSD root`. These markers establish root selection,
not completed root mount, PID1 execution or desktop success. A later failure
must be classified from its own evidence.
