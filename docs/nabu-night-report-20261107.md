# nabu / macOS 26.5 夜间进度报告（2026-11-07 06:00-06:20）

一句话：**内核已编译并验证、ADT 已按严格契约生成、可 `fastboot boot` 的镜像已打包完毕**；
kext 侧存在一个跨构建的硬边界（下方 §4），首次上机预期只到平台专家阶段，但能拿到真实 EL1 日志。

---

## 1. 现在就能做的一步（不写任何分区）

```bash
adb devices && adb reboot bootloader
fastboot devices
fastboot getvar slot-unbootable:b        # 期望：no
fastboot getvar slot-retry-count:b       # 期望：7
fastboot boot out/nabu/image/nabu-macos26-kernel.img    # 只从 RAM 启动，绝不 fastboot flash
```

镜像：`out/nabu/image/nabu-macos26-kernel.img`，24,813,568 B，sha256 `2c61f09419d5ebf9…`。
内含 kernel 24,787,072 B（sha256 `83ec42f2…`）、ADT 11,424 B（严格校验 14/14 通过）、`unixnu.toml`。
启动参数：`-v debug=0x14e keepsyms=1 cpus=1 armcpu-self-ipi=1`，DarwinBoot 窗口 1 GiB。

回滚核对（`fastboot boot` 本身不改分区，这里只是确认）：

```bash
fastboot getvar slot-unbootable:b   # 仍应为 no
fastboot reboot                     # 正常回到 Ubuntu（boot_b 未被触碰）
```

## 2. 产物与校验

| 文件 | 字节 | sha256 前 16 位 | 说明 |
|---|---|---|---|
| `out/nabu/kernel/kernel.development.nabu` | 24,787,072 | `83ec42f2bd5d0b67` | 0008 版内核，MH_EXECUTE/arm64，含 dSYM+map |
| `out/nabu/image/nabu-macos26-kernel.img` | 24,813,568 | `2c61f09419d5ebf9` | Android boot v0，`fastboot boot` 用 |
| `…/Payloads/kernel.macho.adt` | 11,424 | `0d262564d898aa6e` | `make_nabu_adt` 生成，14/14 校验 |
| `EFI/UniXNU/UniXNU.efi` | 90,112 | `19e1b4fff9a7b3ce` | EDK2 Mu_Basecore 构建 |
| `EFI/UniXNU/Drivers/DarwinBoot.efi` | 45,056 | `4ceb18422e446db5` | EDK2 DarwinBootPkg |
| `out/macos26-assets/userspace/dyld.arm64` | 1,309,040 | 见 json | PAC 降级后（arm64） |
| `out/macos26-assets/userspace/launchd.arm64` | 617,024 | 见 json | PAC 降级后（arm64） |
| IPSW `UniversalMac_26.5_25F71_Restore.ipsw` | 19,769,944,180 | `fbaa50395763009b` | 已校验，与 25F71 记号一致 |

内核侧关键事实：

```
符号表 _kalloc_sparse_prelink_index_version = 0xfffffe0007007f48，值 1
  （emit_prelink 的准入 ABI 已满足；同批还编入 sheng_security/native_boot 两个标记）
__TEXT 0xfffffe0007004000/0x1ac000  __TEXT_EXEC …/0x93c000  __LINKEDIT …/0x97c000
嵌入 xiaomi,nabu、ArmDTPlatform、ArmCpu；corecrypto 封签 HMAC 写入 __fips_hmacs
```

## 3. 昨夜推进的五件事

1. **CI 打通**：run `42299746771` 成功，产物下载并逐项核验（见上表）。
2. **内核 ABI 补齐**：`0008-nabu-sparse-prelink-index.patch` 把 XNU 12377 内置的
   `__macho_list` 稀疏 prelink 解析从 `#if SHENG` 放开给 NABU，并导出 ABI 标记。
3. **ADT 契约对齐**：`native_devtree.make_nabu_adt` 重写为 14/14 通过（单核 cpu@0、
   GIC 子偏移 0xa00000/0xa60000 → 0x17a00000/0x17a60000、ranges 起点 0、
   8 KiB CHRP NVRAM（三段头 + Adler-32）、64 字节 random-seed、defaults/options、
   首启无 ufs/gpu 节点）。
4. **用户态 PAC 降级打通**：IPSW 的 `RestoreRamDisk` 是**明文 APFS DMG**（7z 可读），
   从中取到真实 `usr/lib/dyld`（1,309,040 B）与 `sbin/launchd`（617,024 B），
   `nabu_pac_strip.py --downgrade-subtype` 结果：
   ```
   dyld     arm64e→arm64  5,287 条指令改写（PAC→NOP 2971、RETAA→RET 1497、BLRAA→BLR 571、…）
   launchd  arm64e→arm64  2,373 条指令改写（RETAA→RET 1063、PAC→NOP 574、BRAA→BR 560、…）
   链清单：两者都是 pointer_format 12（ARM64E_USERLAND24），auth_rebase 0 个
   ```
5. **可上机镜像打包**：`package_nabu_fastboot` 产出 Android boot v0，NRAM 里内嵌内核+ADT+菜单。

## 4. 硬边界（上机前必须知道）

1. **kext 无法跨构建沿用**：对 macOS 26.5 内核缓存的 982,911 个链式指针做归属统计，
   731,678 个（74.5%）指向 Apple 版 `com.apple.kernel` 镜像。我们是从源码重建的 XNU
   （同基址、不同内部布局），链槽只有 30 位偏移、无符号名（`imports_count = 0`），
   因此这些引用不可能靠重写搬运。要装上 Apple 的 kext，必须按未定义符号名重链，
   或用源码构建自有驱动。→ **首次上机是不含 kext 的裸内核**，预期停在 IOPlatformExpert /
   根设备阶段，但能拿到 `NABULOG2` 日志与屏幕输出，用来验证 EL1 交接、GOP 显示、定时器、GIC。
2. **BaseSystem 取不出来**：`BuildManifest` 里 `BaseSystem` = `022-21678-092.dmg.aea`
   （1,140,850,688 B，`Personalize: true`），是 Apple 个性化加密的 AEA 归档，
   离线 Linux 无密钥来源（TSS 个性化）。`OS`/`Cryptex1,SystemOS` 同样是 `.aea`。
   明文的只有 `RestoreRamDisk` ×2、`Cryptex1,AppOS`，所以只能做用户态少量二进制，
   拿不到完整 dyld shared cache。
3. 首启单核、无 UFS/GPU 节点（严格 ADT 契约要求），所以启动后不要期待存储或画面自检通过。

## 5. 日志采集（启动后立刻做）

```bash
# 屏幕：panic 信息拍照即可（debug=0x14e 会打印详细栈）
# 若启用 RAM 日志卷（unixnu.toml 里 nabu-log=1 时）：
adb shell ls -l /sdcard/            # 或 fastboot 侧读取 XNU2LOG.BIN（见 config/nabu-baseline.json log_magic=NABULOG2）
python3 scripts/nabu_esp_log.py <日志文件>    # 解析 NABULOG2/NABUCFG2 结构
```

请把这些带回来：panic 截图、`fastboot getvar all` 里 `slot-*` 段、`dmesg`/日志文件。

## 6. 下一步（按优先级）

1. 上机验证裸内核（本轮镜像），确认 EL1/显示/GIC/定时器链路。
2. 若停在根设备：把 UFS 驱动按 arm64 路径打开（当前 `ARCH == 'arm64e'` 才编 `native-ufs`），
   或以只读模式读 `os_lba 2739968` 做验证。
3. kext 方案二选一：按未定义符号名重链 Apple kext，或源码构建最小驱动集（APFS/UFS/GPU）。
4. 用户态：RestoreRamDisk 明文可用，可先做 dyld/launchd 的小闭环验证。
