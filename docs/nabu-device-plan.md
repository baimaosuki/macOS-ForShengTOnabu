# nabu 设备侧计划（等设备连上后执行）

约束：**只做临时 RAM 启动**，不刷任何分区；`boot_b` 的 Ubuntu 必须保持可启动。

## 0. 前置（软件侧）

DarwinBoot 的载荷契约（`UniXNU/drivers/DarwinBoot/DarwinBootPkg/Driver/DarwinBoot.c`）：

```
\EFI\UniXNU\Payloads\kernel.macho              ← prelink 产物（当前缺）
\EFI\UniXNU\Payloads\kernel.macho.adt          ← make-*-adt.py 生成，DRAM 描述
\EFI\UniXNU\Payloads\kernel.macho.trustcache   ← 与 kernel.macho 同源
\EFI\UniXNU\Payloads\kernel.macho.gpuaddon     ← make-gpu-addon.py（可选）
```

已就位的 EFI 程序（`out/esp-payload/`，均为 PE32+ AArch64）：

```
EFI/UniXNU/UniXNU.efi                  90,112 B  sha256 19e1b4ff…4bf4b
EFI/UniXNU/Drivers/DarwinBoot.efi      45,056 B  sha256 4ceb1842…05932d
EFI/UniXNU/Drivers/UniXNUDxe.efi       16,384 B
EFI/UniXNU/HelloPayload.efi            20,480 B
```

## 1. 备份（先做，只读）

```bash
adb shell getprop ro.boot.slot_suffix                 # 期望 _b（Ubuntu 所在）
adb shell su -c 'dd if=/dev/block/by-name/esp of=/sdcard/esp-backup.img bs=4M'
adb pull /sdcard/esp-backup.img
sha256sum esp-backup.img                              # 记录，回滚用
adb shell su -c 'ls -l /dev/block/by-name/ | grep -E "esp|boot|super|userdata"'  # 记录分区表
```

## 2. ESP 载荷落盘（写 ESP 本身是必须的，分区表不动）

```bash
adb shell su -c 'mkdir -p /mnt/esp && mount -t vfat /dev/block/by-name/esp /mnt/esp'
adb push out/esp-payload/EFI/UniXNU /mnt/esp/EFI/
# Payloads 里的 kernel.macho / .adt / .trustcache 待 prelink 完成后一并推入
adb shell su -c 'sync && umount /mnt/esp'
```

## 3. 临时引导（关键：`fastboot boot` 不写分区）

```bash
adb reboot bootloader
fastboot devices
fastboot boot out/nabu/boot.img      # 临时 RAM 载入；绝不使用 fastboot flash
fastboot getvar slot-retry-count:b   # 记录（期望 7）
fastboot getvar slot-unbootable:b    # 记录（期望 no）
```

## 4. 现场日志

- UniXNU 侧通过 `scripts/nabu_esp_log.py` 读 `NABULOG2` 记录；
- XNU 侧日志落 `XNU2LOG.BIN`（见 `config/nabu-baseline.json` 的 `log_magic`）；
- 若卡住：长按电源强制重启即回 Ubuntu（RAM 启动不改变任何持久状态）。

## 5. 硬性禁止

- `fastboot flash` 任何分区（boot/system/vendor/super/esp）；
- 改 `boot_b` 的 slot 状态；
- 在没有 ESP 备份完成的情况下写 ESP。
