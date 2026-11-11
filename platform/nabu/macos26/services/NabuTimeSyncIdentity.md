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
