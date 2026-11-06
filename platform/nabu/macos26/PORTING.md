# nabu (SM8150) — macOS 26.5 移植状态

目标：在 Xiaomi Pad 5（nabu, SM8150）上启动 **macOS 26.5 / 25F71**（非 iPadOS）。

## 来源与分工

| 来源 | 贡献 | 不能直接用的部分 |
| --- | --- | --- |
| `CrossDarwin/macOS-ForSheng`（sheng, SM8550） | macOS 26.5 的 XNU 构建流水线、66 个通用内核补丁、平台层头文件、Recovery/日志/存储机制 | 一切 SM8550 地址与 sheng 板级假设 |
| `UniXNU-nabu`（nabu, SM8150） | nabu SoC 层：IPI/GICv3/UFS/GOP/输入、7 个板级 XNU 补丁、ADT 内存契约 | iPadOS 用户态，不是 macOS |

组合产物：sheng 的 macOS 层 + nabu 的 SM8150 层。

## 当前可核对的机械状态

```
python3 scripts/build_macos26_xnu.py --print-series --board nabu
```

| 项 | 数值 |
| --- | --- |
| 基座补丁（sheng 通用序列） | 66 个，全部存在 |
| nabu 板级补丁 | 12 个（7 个来自 UniXNU-nabu + 5 个机器层） |
| UniXNU 组件补丁 | 6 个，待 `scripts/sources.py fetch --group all` 落盘 |
| nabu 平台文件 | 49 个（含从 sheng 迁移的 30 个通用头文件） |

## 内存契约（来自移植记录，必须一致）

```
DRAM banks: [0x80000000, 0x3cc00000] [0xc0000000, 0x80000000] [0x140000000, 0xc0000000]
TrustZone 洞: 0xb9900000..0xc0000000
XNU 窗口: dram-base 0xc0000000, dram-size 0x140000000 (5 GiB)
DarwinBoot 申请: 1 GiB 连续 conventional memory
帧缓冲: 1600x2560 @ 0x9c000000
```

## 剩余任务

1. **UniXNU 子模块落盘**：`scripts/sources.py fetch --group all`
2. **SM8150 适配**：CPU/GICv3 基址、UFS SMMU、GOP 扫描输出、`dts/sm8150.dtsi` 对照机器层补丁复核
3. **命名**：26 个平台文件仍带 `sheng` 字样（继承命名）；改名属于纯机械操作，等构建通了再做
4. **构建目标**：`bringup.yml` 增加 `--board nabu` 与 `macos26-nabu-kernel` 产物
5. **构建环境**：XNU 必须在 macOS + 配套 KDK 上编译（`xcode-27` runner 或本地 Mac）；Linux 侧只负责输入处理、重定位、封装与 QEMU
6. **设备侧**：64 GiB `macosroot` + 64 MiB `macoslog`；ESP 放 `DarwinBoot.efi` 与 `Payloads/`；日志经 `NABULOG2` 回读

## 不做什么

- 不刷 boot/Recovery/userdata 分区内容
- 不改变现有 Ubuntu 的 A/B 槽状态

## 进度记录（2026-11-07）

### 已完成（Linux 侧，可复现）
1. **源码通道**：本机 git 协议不可用（`git clone` → `GnuTLS recv error -110`，
   `ls-remote` 超时），改用 codeload tarball 通道。`scripts/fetch_tarballs.py`
   按 `sources.lock.json` 与 `config/macos26-sources.json` 的 pin 取源码，
   落盘后写 `.source-pin`（url/commit/tarball sha256）。**pin 保真未牺牲**：
   `xnu ac9718fb`、`corecrypto 9612a959`、`UniXNU fb6aad6a`、
   `UniXNU-nabu 54b4f984` 等全部按原 SHA 取到。
2. **校验层**：`build_macos26_xnu.py` 新增 `pinned_identity()`，无 `.git` 时读
   `.source-pin`；`worktree_copy()` 用 copytree + 本地 `git init` 造工作树；
   `apply_series()` 先用 `git apply`，失败退到 `patch -p1 --fuzz=3`。
3. **补丁序列验证**：在 pin 下来的 xnu 树上实测
   **66 个基座补丁 + 6 个 nabu xnu 补丁 = 72 个全部干净应用**（0 失败）。
   - 5 个 `machine-cpu-*` / `nabu-armcpu-timer-intid19` 改的是 `ArmCpu/ArmCpu.cpp`，
     属 UniXNU 组件仓库，已移入 `patches/components/`（6 个）。
   - `nabu-managed-rt-normal.patch` 上下文来自 iPadOS 17 的 pmap.c，
     macOS 26 无法应用；同机制由基座 `0063-native-managed-ram-writecombine.patch`
     提供，已移入 `patches/superseded/`。
   - `0001-nabu-target.patch` 由手写改为在打过基座的树上 `git diff` 生成
     （81 行 / 4 文件），可逆应用验证通过。
4. **板级机器配置**：`TARGET_CONFIGS=DEVELOPMENT ARM64 NABU`、产物
   `DECELOPMENT_ARM64_NABU/libkernel.development.nabu.a`、`OUT=out/macos26-xnu-nabu`
   全部按 `BOARD` 推导；`sheng` 路径行为不变。
5. **`--prepare-only --board nabu` 在 Linux 跑通**：源校验、78 个补丁应用、
   记录写入 `out/macos26-xnu-nabu/build.json`（`stage: source-compilation`）。
6. **CI 按板参数化**：`.github/workflows/bringup.yml` 新增 `board` 输入
   （sheng/nabu），编译/链接步骤传 `--board`，缓存键与产物名
   （`macos26-<board>-kernel`）随之切换。

### 未完成（需 ARM64 macOS runner）
- 真正编译：脚本硬门 `compile on the ARM64 macOS runner; Linux supports --prepare-only`。
  需 `xcode-27` runner + `Kernel_Debug_Kit_26.5_build_25F71`。
- 驱动预链接（`ArmCpu`/`ArmDTPlatform`/`GopFramebuffer`/`ArmSmmu4K`/`NabuUfs`）
  需通过 `XnuPrelink` 在 macOS 上执行。
- 用户态 PAC 降级：`scripts/nabu_pac_strip.py` 已具备能力（arm64e Mach-O +
  format-7/8 chained fixups → ARMv8.2 无 PAC），待 BaseSystem 从 19.77 GB IPSW 解出后跑。
