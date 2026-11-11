# NABU ESP journal: actual desktop wiring and deployment

Reference: `.work/sheng273-reference/UniXNU/docs/porting/log-capture.md` and the repository's `config/nabulog.json`, `NabuLogFormat.h`, `NabuLogVolume.h`, `NabuLogUfs.h`. Mainline's SHENG retained-log/v5 and its other-device FAT16 installer are different formats; do not copy their partition numbers or boot arguments onto NABU.

## Corrected defects

The delivered desktop's retained console never fed the NABULOG2 ring, and its actual NativeUfs driver did not include or start the NABULOG2 writer. Adding `nabu-log=1` alone therefore could not persist text. The console now feeds the 4 MiB RAM ring and the checked desktop root path starts a token-gated ordinary worker. The old writer's recovery-era OS/SystemOS argument gate was also replaced by checking disjointness of the already validated userdata interval and GPT ESP. Source and compiled-driver evidence record the exact included header hashes.

`read_xnu2log.py` previously rotated already-linearized snapshots a second time, causing CRC rejection once the captured stream exceeded 4 MiB. It now uses the shared strict `nabu_log_volume.decode_container` validator. Both slot integrity and configuration identity are checked, with fallback to a valid older slot after an interrupted later commit.

## Storage contract

LUN0 must report 4096-byte logical blocks. The GPT must uniquely identify an ESP-type partition named `esp`, with consistent GPT/BPB geometry, mirrored FAT32 FATs and 4096-byte clusters. Historical copies have differing 300/384 MiB extents; neither a historical size nor the descriptive `esp_size_mib` in nabulog.json is an authorization to resize or write another range.

The root `XNU2LOG.BIN` is exactly 8,400,896 bytes (2051 consecutive clusters): 4096-byte immutable NABUCFG2 config followed by two 4096+4 MiB slots. `nabu-log=1` and a matching nonzero 32-hex `nabu-log-token=` enable the writer. The token is an allocation identity, not a secret authentication credential.

Before writing, the kernel verifies GPT CRC/layout, ESP geometry, both FATs, exact contiguous file allocation, absence of aliases and the config CRC/token. It can write only the preallocated slot blocks, never GPT/FAT/directory/config or root windows. The ordinary worker writes payload with FUA, synchronizes, commits the new header, then synchronizes again. First commit is immediate after validation; subsequent snapshots are approximately every second when text changes. Scheduling/contention or stopped hardware can delay them. Panic hooks do not issue storage I/O, so the last uncommitted bytes can be lost. This is the latest 4 MiB of console text with explicit loss counters, not an unlimited lossless boot log.

## Install after copying the paired kernel files

Use the newly built logger kernel, not an earlier package. Keep the existing root/Data images. Boot TWRP; no Android boot or automatic reboot is needed. From the extracted package directory, run:

```bash
python3 tools/install_nabu_esp_log.py --output nabu-log-backup --boot-config EFI/UniXNU/unixnu.toml --install
```

Python 3.11 or newer and adb are required (Windows can use `py` in place of `python3`). Add `--serial SERIAL` if more than one device is connected. `--output` must be a new directory. Omit `--install` for backup and offline preparation only.

The installer verifies root TWRP on nabu, the current 4K LUN geometry, both GPT headers/tables and the ESP block node. It backs up both GPT ends, the full ESP and the active TOML. The offline preparer preserves unrelated allocated data, creates or relocates only the journal into an existing free contiguous run, retains existing valid journal bytes/token, and verifies allocation ownership. It refuses insufficient contiguous space or geometry mismatch; it does not repartition.

With `--install`, it rechecks GPT and the full original ESP hash, writes only that same-size ESP partition once, verifies full readback, then backs up and replaces TOML through same-directory renames. Only the default XNU entry's two log arguments change unless a new package TOML is explicitly supplied. Config is read back after remount; ESP is left unmounted. The installer does not reboot. A partial or uncertain write is recorded and never automatically retried.

Acceptance on the panel:

```text
NABULOG2: volume verified ...
NABULOG2: first snapshot committed ...
```

Any gate failure names its reason on the console and keeps storage writes disabled.

## Retrieve and decode

Back in TWRP, mount the existing ESP read-only (the partition must still be the verified `by-name/esp` target):

```bash
adb shell 'mkdir -p /esp; mount -t vfat -o ro /dev/block/by-name/esp /esp'
adb pull /esp/XNU2LOG.BIN XNU2LOG.BIN
python3 tools/read_xnu2log.py XNU2LOG.BIN --out boot.txt --info-json boot-info.json
adb shell umount /esp
```

If already mounted, inspect the mount state before mounting again. The decoder auto-reads a CRC-valid config token; `--token` remains available for an explicit expected identity. A successful decode reports sequence, boot ID, payload CRC and captured/lost byte counters. Bind the boot ID/build evidence to the tested kernel before treating a stored snapshot as fresh.

## Validation limits

Production-header transport tests cover 300/384 MiB synthetic disks, wrapped logs, dual commits, incomplete-header fallback, bad-FAT refusal and byte preservation outside slots. Host tools cover contiguous allocation/relocation, identity/geometry rejection, dual GPT validation and install readback failure boundaries. These checks do not prove device FUA/SYNCHRONIZE CACHE behavior or successful runtime commit; retrieve a new CRC-valid snapshot to qualify actual persistence.
