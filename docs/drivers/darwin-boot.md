# DarwinBoot

Start with the [adaptation procedure](/porting/platform) for board inputs, source changes, build integration and verification order.

UEFI bootloader. Installs the loader protocol and hands the kernel, ADT, trust cache and ramdisk to XNU.

| Repository | Maintainer | Scope |
| --- | --- | --- |
| [DarwinBoot](https://github.com/UniXNU/DarwinBoot) | [UniXNU](https://github.com/UniXNU) | UEFI bootloader. Installs the loader protocol and hands the kernel, ADT, trust cache and ramdisk to XNU. |

## State

Not a driver. Used by every XNU boot path.

The loader accepts arm64 and arm64e `MH_EXECUTE` kernels and validates
`MH_FILESET` membership, native entry, mappings and protections. The NABU board
adapter also accepts a plain ARM64 native member inside an ARM64e collection.
XNU initializes pointer authentication and applies its chained fixups.

The NABU loader can be installed as `EFI/UniXNU/Drivers/DarwinBoot.efi` on ESP.
Configure it before the XNU entry:

```toml
[[driver]]
path = "Drivers/DarwinBoot.efi"
args = "replace-existing"
required = true
connect = false
```

With this explicit option, one revision 1 or 2 firmware loader interface can
be replaced. Its driver remains resident; unloading the ESP driver restores
the original interface. Multiple providers and unknown revisions are refused.
The supplied UniXNU application honors `required`: load or start failure keeps
the menu available and prevents an OS entry from using the older loader.

## Build

Include the DarwinBoot INF in the target firmware DSC/FDF, then run `./unixnu build firmware` from UniXNU. Follow [firmware integration](/porting/uefi-firmware#required-integration) and verify the loader protocol before selecting an XNU entry.

The source storage runtime also accepts `kernel.macho.vroot`, a separate signed
policy bundle passed through `/chosen/memory-map/RootPolicies`. Firmware checks
the envelope; the kernel verifies each signature and hash table against its
compiled owner anchor. The new sidecar path remains untested.
