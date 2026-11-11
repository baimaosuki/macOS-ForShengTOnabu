# NABU 25F71 launchd/dyld AMFI trust-cache handoff

Device evidence: `/tmp/codex-clipboard-1e84d928-0bfb-4598-9e0a-aeb049f2d821.png`.
The arm64e admission error has progressed to an explicit AMFI rejection of both
/sbin/launchd and /usr/lib/dyld: platform identifier is not in the trust cache.
The final namespace 9/code 8 SIGKILL follows those signature-check failures.
It does not establish that either process executed its first user instruction.

The previous desktop assembly omitted the original trust-cache sidecar and its
ADT memory-map slot. This was a packaging defect. Existing DarwinBoot already
loads `<KernelPath>.trustcache`, validates the offset-table/version-2 format,
copies it into the owned allocation, and fills the paired 16-byte TrustCache
slot. The current DEVELOPMENT kernel loads the first two modules as static
caches (CONFIG_SECOND_STATIC_TRUST_CACHE); its internal-code configuration
allows additional engineering modules. No kernel or loader rebuild is needed.

Mainline references: UniXNU/scripts/build-original-system-trust.py preserves
original static/system payloads; UniXNU-sheng/scripts/build-sheng-payload.py
copies the sidecar and requests the TrustCache slot. The current target stays
macOS 26.5/25F71, not the reference 27.3 userspace.

Reproduce the verified original bundle:

```sh
python3 scripts/prepare_nabu_trustcache.py \
  --ipsw private/apple/UniversalMac_26.5_25F71_Restore.ipsw \
  --output out/25F71-original-trust \
  --verify-macho out/nabu-desktop-data/os-samples/sbin/launchd.arm64-slice \
  --verify-macho out/nabu-desktop-data/os-samples/usr/lib/dyld.arm64-slice
```

Pass the resulting kernel.macho.trustcache to the desktop assembler with
`--trust-cache`. It validates before creating output, accounts for its memory,
copies the original bytes, and emits the required zero slot. It does not
change code signatures or disable AMFI.

Three original modules, ordered Static/System/App: 15159/177/83 entries.
All three IM4P SHA384 values match the selected j313ap erase BuildIdentity.
Both launchd and dyld match original Static module entries (12312 and 14220).
Bundle: 370144 bytes; SHA256
`2d740fa4b041b95b74b45315f95045715c5ee707a564adbeb780f4fd524431e8`.
The original modules are unchanged, merely placed after the XNU offset table.

The host compiled and ran the actual delivered DarwinBoot ValidTrustCaches
function: original bundle accepted, truncated/version/hash-type/duplicate-hash
negatives rejected. Four assembly/memory tests and two root-matching tests
passed. The real generated ADT matches the previously working 2 GiB/root-match
ADT property-for-property after removing only the new TrustCache slot.

Delivery: out/nabu-25F71-trustcache/nabu-25F71-original-trustcache-fix.zip.
Contains only kernel.macho.trustcache and kernel.macho.adt plus reports.
Requires the existing kernel UUID 76324BCE-F410-369A-BEBB-BD4C46C87F59.
Device trust-cache loading, successful exec, and desktop startup remain
unverified until the next device run.
