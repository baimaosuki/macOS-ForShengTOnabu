# NABU ESP — UniXNU 启动树（纳管副本）

本目录是设备 ESP 上**与 UniXNU 启动链相关文件**的权威副本，逐字节一致。

- 设备分区：`/dev/block/sda31`，vfat，GPT 分区 31，扇区 `2654272–2731071`（76800 × 4096B = 300 MiB）
- 挂载点（TWRP）：`/esp`
- 不含第三方 rEFInd 主题与文件系统驱动（体积噪声，可从 rEFInd 发行包重新获取）
- 参考清单：`NABU-ESP-MANIFEST.json`（含每个文件的 sha256 与 purpose）

## 启动链

```
小米 ABL
  → ESP \EFI\Boot\bootaa64.efi            (rEFInd)
  → \EFI\UniXNU\UniXNU.efi                读 \EFI\UniXNU\unixnu.toml
      按 [[driver]] LoadImage → \EFI\UniXNU\Drivers\DarwinBoot.efi
  → DarwinBoot (DXE)
      读 Payloads\kernel.macho + Payloads\kernel.macho.adt
      分配 owned window（ADT 的 darwinboot-memory-size，1 GiB）
      按 ADT 铺 4 个容器段，回填 /chosen/memory-map
  → DarwinEnter（EL2 → EL1，XNU 入口）
```

## 文件清单与用途

| 文件 | 字节 | MD5 | 用途 | 来源 | 可否手工编辑 |
|---|---|---|---|---|---|
| `EFI/Boot/bootaa64.efi` | 256960 | `4d737362…` | rEFInd 主引导器，位于 UEFI 默认路径 `\EFI\BOOT\BOOTAA64.EFI`，负责枚举卷并拉起 UniXNU | rEFInd 发行二进制 + 本项目 `refind.conf` | ✗ 二进制 |
| `EFI/Boot/bootmgfw-orig.efi` | 256960 | `4d737362…` | Windows Boot Manager 原始副本，仅作回滚留存 | 与上一行同源 | ✗ 二进制 |
| `EFI/Boot/refind.conf` | 3263 | `e8dba7b3…` | rEFInd 配置：菜单项、超时、驱动加载、指向 `\EFI\UniXNU\UniXNU.efi` 的入口 | 本项目 | ✓ 文本 |
| `EFI/Boot/refind.conf.bak` | 3233 | `e95934e0…` | 上一版 `refind.conf` 备份 | 本项目 | ✓ 文本 |
| `EFI/Boot/vars/PreviousBoot` | 198 | `50592315…` | rEFInd 记录的上次启动项（运行时状态，可删） | rEFInd 自动生成 | ✓ 可删 |
| `EFI/UniXNU/UniXNU.efi` | 90112 | `c3ab70c7…` | UEFI 应用：解析 `unixnu.toml`、注入 `[[driver]]`、准备 ADT、调用 loader protocol 的 `Boot()`；菜单与倒计时也在这里 | 构建产物（UniXNU 包） | ✗ 二进制 |
| `EFI/UniXNU/Drivers/DarwinBoot.efi` | 45056 | `3802c4bd…` | DXE 驱动，提供 `UNIXNU_LOADER_PROTOCOL`：读 `<kernel>.adt`、校验 `/chosen/memory-map`（`RAMDisk`/`TrustCache`/`GPUAddon` 均为 16 字节槽）、分配窗口、铺 Mach-O 段、填 `DarwinBootArgs`、跳内核 | 构建产物（DarwinBoot） | ✗ 二进制 |
| `EFI/UniXNU/Drivers/UniXNUDxe.efi` | 16384 | `e74b2488…` | UniXNU 的 DXE 支撑（协议注册与注入辅助） | 构建产物 | ✗ 二进制 |
| `EFI/UniXNU/HelloPayload.efi` | 20480 | `01aab0a5…` | 冒烟载荷：只验证 `LoadImage`/`StartImage` 通路可用，不参与启动 | 构建产物 | ✗ 二进制 |
| `EFI/UniXNU/Payloads/kernel.macho` | 112390144 | `6c8fa3b3…` | **当前启动内核**：fileset KC，由 Uniform Slide 发射器（uniform15）产出；DarwinBoot 按 `vmaddr - min_va` 铺到 owned window | 构建产物（CI） | ✗ 二进制 |
| `EFI/UniXNU/Payloads/kernel.macho.adt` | 11824 | `0ac51a29…` | 与上一行**配对**的 ADT：`/chosen/memory-map`、`darwinboot-memory-size=1GiB`、panic-log 节点、原生文本控制台状态 | 打包器同款两步配方生成 | ✗ 二进制 |
| `EFI/UniXNU/Payloads/kernel.prev.macho` | 29949952 | `485cedc8…` | 上一版内核回滚副本，不参与启动 | 历史构建 | ✗ 二进制 |
| `EFI/UniXNU/Payloads/kernel.macho.adt.bak64` | 11640 | `70e41cf8…` | 历史 ADT 变体，非启动链使用 | 历史生成 | ✗ 二进制 |
| `EFI/UniXNU/Payloads/kernel.macho.adt.new11424` | 11424 | `6e3c6e7f…` | 历史 ADT 变体，非启动链使用 | 历史生成 | ✗ 二进制 |
| `EFI/UniXNU/unixnu.toml` | 524 | `1f3e874e…` | **权威配置**：`[[driver]] path = "Drivers/DarwinBoot.efi"`；entry `args` 共 12 项（见下） | 本项目 | ✓ 文本，但属契约 |
| `EFI/UniXNU/unixnu.toml.bak56` | 502 | `9bc16644…` | 前代配置，缺 `nabu-source-text=1` | 本项目 | ✓ 文本 |

## boot-args 契约

`unixnu.toml` 中 entry 的 `args`（缺一项就可能改变内核行为，改动必须单变量验证）：

```
-v serial=3 cpus=8 nabu-log=1 nabu-source-text=1 nabu-gpu=1
native-ufs-root=1 native-ufs-os-lba=2739968 native-ufs-os-sectors=11718723
native-ufs-systemos-lba=4204809 native-ufs-systemos-sectors=6451200
armcpu-self-ipi=1 t733-managed-rt-normal=1 sheng-boot-progress=1
```

| 参数 | 作用 |
|---|---|
| `serial=3` | 串口日志级别 |
| `cpus=8` | 逻辑 CPU 数，**必须与 ADT 的 CPU 节点数一致** |
| `nabu-log=1` | 启用 NABULOG 环形日志（retained log） |
| `nabu-source-text=1` | 原生文本控制台；与 ADT 的 native text state 配对 |
| `nabu-gpu=1` | GPU 初始化开关 |
| `native-ufs-root=1` + 4 个 `native-ufs-*` | UFS 根卷窗口（LBAs 与扇区数） |
| `armcpu-self-ipi=1` | 单核自 IPI 容忍（A76 在缺 SMP 驱动时的绕行） |
| `t733-managed-rt-normal=1` | 时钟/电源域正常模式 |
| `sheng-boot-progress=1` | 打开进度与检查点（`sheng_boot_checkpoint`），屏上 `STAGES` 阶梯由它驱动 |

## 生成规则（复现用，禁止凭记忆重写）

```python
# ADT：必须是两步
make_nabu_adt(path,
              dram_base=0xC0000000, dram_size=0x1400000000,
              owned_memory_size=0x40000000,
              os_environment='recoveryos',
              panic_log=True,
              native_gop_display=True)
configure_native_text_state(path, development_root=True, native_gop_display=True)
```

- 只调 `make_nabu_adt` 会少约 140 字节状态（文本控制台节点），内核会在 `trust_cache_init` 之后挂。
- `kernel.macho` 由 `scripts/prelink_fileset_kernel.py` 经 CI 产出，与 ADT 必须成对更新。

## 大二进制（不进 Git，按哈希钉死）

`Payloads/kernel.macho`（107 MiB）与 `Payloads/kernel.prev.macho`（28 MiB）超过 GitHub 单文件
100 MiB 上限；本项目远端是 public fork，GitHub 同样拒绝对 fork 上传 LFS 对象。因此这两个文件
**不在仓库内**，但被清单以 sha256 钉死，可用 `fetch_payloads.py` 复原并校验：

| 文件 | 字节 | MD5 | SHA256 |
|---|---|---|---|
| `kernel.macho` | 112390144 | `6c8fa3b3…` | `16ced8e2796cc5b217aefb25483bcdcaf51ef873460147e8f876831191dff872` |
| `kernel.prev.macho` | 29949952 | `485cedc8…` | `85a978c2cdc2f9a72a837135c429cdc9c1bd2f54f7e466e2610d52f55b3775d2` |

复原（三种来源，全部校验 sha256，不一致即失败）：

```bash
python3 fetch_payloads.py --from <CI 输出或 staging 树>     # 从构建产物复制
python3 fetch_payloads.py --adb                              # 从设备 /esp 拉取
python3 fetch_payloads.py --out-dir ../../../../out          # 从本地 out/ 复制
python3 fetch_payloads.py --check                            # 只核对现况
```

写 ESP 前必须让 `--check` 与 `NABU-ESP-MANIFEST.json` 全部一致。

## 改动流程（硬性纪律）

1. 先对账：`diff -r <设备>/EFI/UniXNU platform/nabu/macos26/esp/EFI/UniXNU`
2. 一次只改一个文件/一个变量，其余保持逐字节不变
3. 本地 MD5 → 推设备 → 设备侧 MD5，两侧一致才算写入成功
4. 同步更新本目录与 `NABU-ESP-MANIFEST.json`，再提交

## 事故记录

### 2026-11-09 ESP 扩容事故
- ESP 由 300MB 扩到 2GB；扩后累计写入约 317MB（EFI 140MB + ramdisk 175MB），越过原 `sda32` 起点（扇区 2731072）约 17.8MB，覆盖 Ubuntu ext4 超级块区。
- 分区表已按原扇区复原（`sda31` 76800 扇区、`sda32` 2731072–30660602），文件系统需用备份超级块修复。
- 同期我用手写 343 字节 toml 覆盖了原始 524 字节配置，丢掉 `[[driver]]` 段与 9 个 boot-args，导致 UniXNU 立即返回 `EFI_ERROR`，内核未被执行。
- 本目录为复原后的权威副本；`unixnu.toml` = `1f3e874e…`、`kernel.macho.adt` = `0ac51a29…`、`kernel.macho` = `6c8fa3b3…`。
