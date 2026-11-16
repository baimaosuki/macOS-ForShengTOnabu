# ShengUfs

NABU starts source storage after `IOKitBSDInit` publishes the genuine `IOBSD`
resource. Native media BSD clients require that resource during matching; the
source entry point rejects startup before publication. The worker loads the
original APFS module before root window publication. Missing-volume snapshots
include resource presence and rejected/entered/returned read-completion counts.
The first 16 requests per window log their LBA, size, result and callback return.
This lifecycle correction still requires device verification of root mount.

Start with the [adaptation procedure](/porting/ufs) for board inputs, source changes, build integration and verification order.

ShengUfs provides the SM8550 UFS storage path. It owns the host
controller, SMMU mappings, command queues, and root reads. Its board integration
includes clock resources and the persistent Data and log regions. On request it
also gives the display bank (`dpu-scanout`) and the WiFi endpoint
(`wifi=2` or more) stage 1 windows; both are off by default.

| Field | Value |
| --- | --- |
| Repository | [UniXNU/ShengUfs](https://github.com/UniXNU/ShengUfs) |
| Hardware | SM8550 UFS host, PHY, clocks and SMMU resources |
| Board dependencies | Board power policy and storage layout |
| Verified device | [Reference native UFS root](/devices/sheng#storage) |
| Runtime class | `NativeUfs` |

The component catalog supplies its kernel archive recipe. The selected device
profile enables `CONFIG_XNU_NATIVE_UFS` and supplies the board inputs.

Verified userdata windows publish internal virtual-storage characteristics.
Disk Arbitration can retain their boot-time mounts before a console user logs in,
including the App cryptex at `/System/Volumes/Preboot/Cryptexes/App`.

```sh
./unixnu build kernel -D /path/to/device
```

## Porting and records

The device repository supplies ShengUfs as a submodule. Follow the
[UFS porting guide](/porting/ufs) and maintain controller, clock, PHY, SMMU,
DMA, partition and completion details in the [storage record](/devices/sheng#storage).

For the arm64e public-source build path and its current linking/runtime limits, see [public-source driver builds](/porting/migration#source-drivers). Historical iOS 17 device results do not qualify this path.

## Command path

The board-local NABU adapter uses SID `0x300`. It discovers an existing exact
firmware mapping independently of the top-eight bank allocation window. Reuse
requires a unique stream and bank owner, stage 1 disabled, stage 2 bypass and
VA64; it preserves all context registers and revalidates before configuration.
Both transfer and task-management lists are stopped before SMMU access. These
paths have host fault tests; the NABU 27.3 root handoff still needs device testing.

For NABU source storage, the original APFS module is loaded before root-window
publication. A missing signed volume reports transport counters and the service
subtree's provider, content, geometry and whole/removable/ejectable properties,
including traversal completeness and validity. These observations do not bypass
the signed volume gate or qualify a root mount.

Up to 32 commands run at once, one per command slot. The request list sits on a
write-combined page of its own. Completion uses the controller interrupt with
hybrid polling: a wait polls for 200 microseconds, then sleeps. Page-aligned
buffers are transferred by zero-copy DMA of up to 1 MiB per command. On the
reference board every read test matches or exceeds the mainline Linux driver;
see [UFS performance](/porting/ufs#performance).

## Wi-Fi DMA window

The WCN7850 DMA window maps 128 MiB at IOVA 0x10000000 using 64 two-MiB
blocks in its own SMMU context bank. Device checks reach Wi-Fi interface
readiness and traffic with zero DMA allocation failures. The board fixture
`scripts/check-wifi-dma.py` checks capacity and allocation boundaries.
