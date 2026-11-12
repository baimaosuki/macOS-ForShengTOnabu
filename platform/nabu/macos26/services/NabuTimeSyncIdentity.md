# 25F71 TimeSync identity provider

This supplies the original network family's Ethernet identity path, with no
network connectivity. It never registers a replacement TimeSync class. The MAC
is derived from the persistent, device-specific 16-byte `/options/platform-uuid`
ADT property, shared with the board's other identity consumers. Missing, all-zero
or incorrectly sized UUIDs fail startup. Packaging must preserve this UUID and
must not use the display UUID or a common per-model UUID.

Required native prelink roots (from the original 25F71 BootKC):

- `com.apple.iokit.IONetworkingFamily`
- `com.apple.iokit.IOTimeSyncFamily`
- `com.apple.plugin.IOgPTPPlugin`

Compile the CPP as its own translation unit using the Mac kernel SDK network
headers with private SPI enabled. Preserve ordinary OSKext metaclass registration:
load/register the original IONetworkingFamily before this translation unit's
constructors, then call `nabu_timesync_identity_start()` after IOResources exists.
Do not run controller start synchronously inside an IOResources match callback.
The entrypoint is idempotent and starts a worker. Its state is 2 while pending,
1 when published, or negative on failure. A published controller deliberately
keeps one reference for its boot lifetime.

`static_assert(sizeof(IOEthernetController) == 0x118)` checks the original
metaclass allocation size. Before admission the build must additionally compare
the compiled subclass's inherited vtable slots and their authenticated pointer
contracts against the original collection, as done for other private classes.
`scripts/inspect_nabu_timesync_identity.py` extracts those original slots and
records the source KC hash; it does not claim to validate a compiled object.
No headers from 26C5035r or 26A428 may supply an unchecked ABI for 25F71.

The session repair keeps its startup worker alive until IONetworkStack assigns
the real BSD Name (up to 900 one-second waits), then calls registerService once
on that named interface. Original 25F71 ClockManager installs an
`gIOMatchedNotification`, verified through the BootKC's GOT slot
`0xfffffe0008754510`. Re-registration invokes this notification again; it is
not a first-match-only notification. The original interfaceMatched callback
requires BSD Name before scheduling clock creation. The worker neither assigns
an en0 name nor reports a clock as ready. Startup publication and root mounting
do not wait for the worker.

Original 25F71 `IOTimeSyncClockManager::interfaceMatched` is exported at
`0xfffffe000accfa4c`. The IONF branch checks `BSD Name`, rejects the name prefix
`AppleThunderboltIPPort`, and emits `matched first IONF interface`. Thus registry
publication alone is insufficient: the original IONetworkStack and configd must
name the interface before native TimeSync can consume it. This provider permits
the native naming path and does not reserve en0, overwrite configuration, or
pretend a BSD name was assigned. TimeSync and WindowServer progress still require
runtime confirmation; neither has been verified on hardware here.

All administrative enable/disable states keep link valid but inactive, speed 0.
All outgoing mbufs are consumed and counted as dropped. The medium is Ethernet
none, the interface is not classified as Wi-Fi, and both controller and interface
publish `NetworkConnectivityImplemented=false`.

## Native 25F71 clock lookup policy (2026-11-13)

The original family has a supported presence flag, `-timesync_bringup`. This
is unrelated to the iOS 20F66 QuartzCore `CA_FORCE_TIMER` behavior. The paired
25F71 kernel collection implements it as follows:

| Original function / address | Verified behavior |
| --- | --- |
| `IOTimeSyncClockManager::init`, `0xfffffe000accd704`–`0xfffffe000accd720` | `PE_parse_boot_argn("-timesync_bringup", NULL, 0)` stores the presence boolean at manager `+0x128` |
| `delayedClockIDLookup`, `0xfffffe000accdd48`–`0xfffffe000accdd64` | Flag present selects `IOSleep(5000)`; flag absent selects `getPlatform()->waitQuiet(UINT64_MAX)` |
| `delayedClockIDLookup`, `0xfffffe000accdd98`–`0xfffffe000accdda4` | Both branches join the original `findTimeSyncTimeClockID()` call and test its success byte at manager `+0xbb` |
| `delayedClockIDLookup`, `0xfffffe000accddd0`–`0xfffffe000accdde4` | Only after successful lookup, update the original translation service and publish the `IOTimeSyncClockManager` resource |

`scripts/inspect_nabu_timesync_identity.py` now qualifies the option literal,
parse call, flag storage, both wait branches, lookup call, success guard,
translation update and resource publication against the actual original
instructions. It rejects changes to this contract. Run:

```sh
python3 scripts/inspect_nabu_timesync_identity.py \
  --kernelcache out/nabu-bootkc/kernelcache.release.mac13g.nabu.kc \
  --output out/nabu-timesync-25F71-policy.json
```

The report records the complete input hash and the delayed lookup code hash.
This inspector does not patch the collection. The boot policy can select this
existing native branch to remove the dependency on *global* platform quiescence
while retaining real interface and MAC discovery. The 5-second delay starts
after an actual named-interface match schedules the original delayed lookup;
it is not a five-second deadline from power-on and not a readiness assertion.
Because the option is tested for presence, use its exact standalone spelling.

The candidate's inactive carrier and `IOBuiltin=false` do not require faking a
built-in interface. `findTimeSyncTimeClockID` first prefers built-in interfaces,
then has an Ethernet fallback at `0xfffffe000accec20`. That fallback excludes
Thunderbolt IP and the reviewed USB case, collects eligible original Ethernet
interfaces, and reads `IOMACAddress` from the interface or controller beginning
at `0xfffffe000accefd4`. Native code derives and publishes its own clock ID;
this provider must not publish that resource itself or invent a clock ID.

The `xnu-20261113a/verified-boot.txt` journal contains native `en0` naming and
the rematch, and `IOTimeSyncTranslationMach` publication. It contains no
`ZZ rs IOTimeSyncClockManager` resource-owner publication. This is consistent
with the original delayed worker remaining in its unbounded `waitQuiet`, but
does not prove that wait's runtime stack or establish it as the sole black
screen cause. Hardware acceptance requires observing the actual native clock
resource plus its clock ID and translation, followed by completed user frames
and visible session progress. No networking functionality is added.
