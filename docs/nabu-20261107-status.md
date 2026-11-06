# nabu / macOS 26.5 夜间进度（2026-11-07 05:00）

目标：Xiaomi Pad 5（nabu, SM8150）以**临时 RAM 启动**方式运行 macOS 26.5
（25F71 / XNU 12377.121.6，arm64），不写任何分区。

## 已完成并验证

### 1. IPSW 与内核缓存

| 项目 | 值 |
| --- | --- |
| IPSW | `UniversalMac_26.5_25F71_Restore.ipsw`，19,769,944,180 B |
| sha256 | `fbaa50395763009b93af4125cad7fe87722b91d8b2576498c0d341a7a5171899` — **与固定值一致** |
| 本地路径 | `private/apple/UniversalMac_26.5_25F71_Restore.ipsw` |

IPSW 内含 12 份按 SoC 分离的 `kernelcache.release.<soc>`（IM4P / DER，载荷 `krnl`，
LZFSE）。已解出全部 12 份，并把 `mac13g` 转成扁平 Mach-O：

```
out/macos26-assets/kc.mac13g.macho   116.6 MB   sha256 19ae26f46899eb893052436482ac7a7cbf3bbb2b7b0c46b964a849c14442d137
  __TEXT 0xfffffe0007004000  __PRELINK_TEXT  __DATA_CONST  __TEXT_EXEC
  __PRELINK_INFO 0xfffffe000bfcc000 (4.3 MB)  __DATA  __LINKEDIT
  cputype arm64 / arm64e，filetype 12 (fileset)，357 条 load command
```

工具已入库：`scripts/decode_kernelcache.py`（可指向 IPSW 或单份 kernelcache，
输出 Mach-O + JSON 元数据，含 `kernelcache_sha256`）。

### 2. arm64e → ARMv8.2 指令翻译（PAC 剥离）

`scripts/nabu_pac_strip.py` 直接吃 macOS 26.5 的文件集内核集合（`--kind fileset`）：

| 指标 | 数值 |
| --- | --- |
| fileset 条目 | 346 |
| 扫描可执行字节 | 71,221,248 |
| 改写指令 | 779,384 |
| PAC/AUT/XPAC→NOP | 380,641 |
| BLRAA/BLRAB→BLR | 262,931 |
| RETAA/RETAB→RET | 110,642 |
| BRAA/BRAB→BR | 23,998 |
| LDRAA/LDRAB→LDR 族 | 1,102 |
| chained fixups | format 8，982,911 指针，875,816 认证指针转换（IA 873,116 / DA 2,700） |

报告：`out/macos26-assets/kc.mac13g.strip.json`。

### 3. EFI 载荷（本机构建，GCC/AArch64）

本机没有 clang/llvm-ar（apt 依赖破损），改用 EDK2 的 `GCC` 工具链族 +
`aarch64-linux-gnu-` 交叉编译器，与 CI 使用同一份 Mu_Basecore：

```
out/esp-payload/EFI/UniXNU/UniXNU.efi                 90,112 B  PE32+ AArch64 (EFI application)
out/esp-payload/EFI/UniXNU/Drivers/DarwinBoot.efi     45,056 B  PE32+ AArch64 (EFI driver)
out/esp-payload/EFI/UniXNU/Drivers/UniXNUDxe.efi      16,384 B
out/esp-payload/EFI/UniXNU/HelloPayload.efi           20,480 B
```

### 4. 内核构建链的板级修复（CI）

`sheng` 分支的补丁序列直接套到 nabu 后，逐层暴露了 7 处板级假设。新增补丁：

| 补丁 | 作用 |
| --- | --- |
| `0001-nabu-target.patch` | NABU 板级目标（`-DARM64_BOARD_CONFIG_NABU -march=armv8.2-a`）、`NABU.h` |
| `0002-nabu-export-headers-and-warnings.patch` | `NABU.h` 进入 pexpert 导出列表；`-Wno-reserved-identifier` 对 SHENG/NABU 同时生效 |
| `0003-nabu-processor-and-driver-matching.patch` | `proc_reg.h` 处理器判定接受 NABU；`KernelConfigTables` 增加 `ArmCpu`/`ArmDTPlatform` 的 `xiaomi,nabu` 匹配 |
| `0004-nabu-disable-cpu-counters.patch` | `CPU_COUNTERS_BASE` 排除条件加入 `SOC_CONFIG_nabu` |
| `0005-nabu-cpmu-constants.patch` | `NABU.h` 提供 `CPMU_*`（十计数器描述，Apple 专有特性置 0） |
| `0006-nabu-16k-pages.patch` | `__ARM_16K_PG__`：macOS arm64 的 16K 页粒度，pmap commpage 断言依赖它 |
| 构建脚本 | 对象目录/内核文件名/记录字段/device-tree 匹配串改为按 `MACHINE`/`BOARD` 派生 |

里程碑：`source-archive` 阶段已真实产出 **149 MB** 的 `libkernel.development.nabu.a`
（`source_archive_built = true`）。此前构建在「验证 IOPlatformExpert 记录」处抛出，
原因是脚本把 `DEVELOPMENT_ARM64_SHENG` 写死。

## 未完成 / 早上需要决策

1. **内核链接收尾**：CI 正在跑（run `42290771399`）。成功后会产出
   `kernel.development.nabu.unstripped.noctf` 与 `macos26-nabu-kernel` 工件。
2. **prelink 输入需要改 pin**：`UniXNU-nabu/board.toml` 的 `[kernel.prelink]` 仍是
   BCM2837 的记录（`kernel = build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/...`，
   `kernelcache_sha256 = f7d1…`，`slide = -0x1360000`）。需改成 nabu 的 native kernel
   与 `kc.mac13g` 的 sha256，并按新内核基址重算 slide。
3. **BaseSystem 是 AEA 加密**（`*.dmg.aea`），Linux 上没有 `aea` 工具，用户态
   （dyld/launchd/系统卷）今晚无法取出；这一环需要 macOS 侧或用 `ipsw` 工具链补。
4. **设备侧（等你连接设备）**：ESP 整体字节备份（`/dev/block/sda31`）→ 建
   `macosroot`/`macoslog` GPT → 放 ESP 载荷 → `fastboot boot` 临时引导。
   任何一步都不写分区表以外的既有分区。

## 复现命令

```bash
python3 scripts/decode_kernelcache.py private/apple/UniversalMac_26.5_25F71_Restore.ipsw \
    --soc mac13g --output out/macos26-assets
python3 scripts/nabu_pac_strip.py --input out/macos26-assets/kc.mac13g.macho \
    --output out/macos26-assets/kc.mac13g.stripped.macho \
    --report out/macos26-assets/kc.mac13g.strip.json
```
