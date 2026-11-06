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

---

## 06:15 更新：内核链接成功（CI 绿）

run `42297783693` — **success**。构建链首次走完 `source-archive → 链接 → 封签`：

```
out/nabu/kernel/kernel.development.nabu   24,786,872 B
sha256 0371fe7fb55ee409257ff439368de289e52ddebabfae08f1e1a21a1d0b2d6929
magic 0xfeedfacf  cpu 0x100000c (arm64)  filetype 2 (MH_EXECUTE)  ncmds 25
入口 __TEXT va 0xfffffe0007004000（与 macOS 26.5 内核缓存同基址）
段: __TEXT 1.7 MB / __TEXT_EXEC 9.2 MB / __DATA_CONST / __DATA 1.4 MB
    __BOOTDATA 864 KB / __LINKEDIT 9.5 MB
    __PRELINK_TEXT、__PRELINK_INFO、__PLK_* 均已存在但长度为 0（等待 prelink 填充）
内嵌: "xiaomi,nabu"、ArmDTPlatform、ArmCpu
```

为走到这一步新增的板级改动（共 7 个补丁 + 1 个 shim + 4 处脚本/工件修正）：

| 项 | 说明 |
| --- | --- |
| `0007-nabu-optional-kdk-support-library.patch` | `MakeInc.def` 对 `lib$(MACHINE).os.$(CONFIG).a` 的 `-force_load` 改为仅在文件存在时生效（KDK 只带 Apple SoC 的同名库） |
| `platform/nabu/macos26/shim/nabu_link_stubs.cpp` | 补 `ml_device_is_prod_fused` / `pmap_pending_preemption` / `tb_transport_startup`（Apple 放在每 SoC 支持库里） |
| 脚本 | `KERNEL_NAME` 派生；crypto 提供者归档、map/dSYM、workflow 工件路径全部按 `BOARD` 生成 |
| `seal_macos26_crypto.py` | 去掉对 `_sheng_corecrypto_bootstrap` 的断言（该符号全树无定义），改以 `__cc26_text` + 32 字节 `__fips_hmacs` 契约校验 |

## 卡点：prelink 工具不支持 macOS 26 的文件集内核缓存

`UniXNU/kits/NativePrelink/emit_prelink.py` 对现代内核缓存的两次硬拒绝（实测）：

```
$ emit_prelink.py --kernel kernel.development.nabu --prelinked kc.mac13g.macho ...
ValueError: Only ordinary little-endian ARM64 is supported      # cpusubtype arm64e

$ # 用 nabu_pac_strip.py --downgrade-subtype 转成 arm64 之后
ValueError: Expected MH_EXECUTE kernel images                   # 内核缓存是 filetype 12
```

结论：该工具是 BCM2837 时代的产物，只认**旧式 `MH_EXECUTE` 预链接内核**。macOS 26.5
的内核缓存是 fileset（`LC_FILESET_ENTRY`，346 个 kext 条目、982,911 个 chained-fixup
指针），工具既没有 fileset 解析，也没有 chained fixup 展平逻辑。要产出 DarwinBoot 需要的
`kernel.macho`，必须先补齐这一层。

两条可选路线（需要你定）：

1. **扩展 prelink 工具**（推荐）：解析 `LC_FILESET_ENTRY`，把每个 kext 的段搬进 native
   kernel 的空 `__PRELINK_*` / `__PLK_*` 段，展平 chained fixups（用 `nabu_pac_strip.py`
   已有的 format-8 链解析），再重绑导入表。工作量约数百行，纯 Python，可在本机跑。
2. **放弃改写内核、改走加载原始内核缓存**：需要 EL1 侧自己解析 fileset + 应用 fixup，
   等于重写加载器，比路线 1 更重。

另：`board.toml` 的 `early_security_init` 仍是 sheng 记录的
`com.apple.driver.AppleMobileFileIntegrity=0xfffffff005b70ae0`，macOS 26.5 的实际地址
要从 `kc.mac13g.macho` 的符号表重新取。

---

## 06:00 更新：封签内核落地

```
out/nabu/kernel/kernel.development.nabu          24,786,872 B
sha256 4554b22994cbf6be97d3c4a0d08b34f2e97f769eafcba864d236f94530d15e64
UUID   71582AC5-DFC1-3403-8E7E-103ACE5A35DE (arm64)  dSYM UUID 一致
build.json: executable_kernel_built = true, build_failures = []
corecrypto_integrity: __cc26_text @ file 0xc45994，HMAC 已写入 __fips_hmacs
```
`kernel.development.nabu.map` 与 `.dSYM` 一并入库（后者用于后续符号解析与 panic 定位）。

## 06:45 更新：文件集内核缓存的真实结构（更正前文判断）

`scripts/analyze_fileset_kernelcache.py` 对 `kc.mac13g.arm64.macho` 的实测：

```
fileset 条目 346 个，全部带 __TEXT，代码合计 13.3 MB
父级 __PRELINK_TEXT vmsize 13,369,344 (12.75 MB)、__PRELINK_INFO 4,292,608
                              ← kext 段仍然被压进父级 coalesced 区间
关键 root 的加载地址（非页对齐，说明是各 kext 的 Mach-O 头地址）：
  com.apple.filesystems.apfs                      text 345.5 KB  va 0xfffffe0007c0f8b0
  com.apple.driver.AppleMobileFileIntegrity       text 120.4 KB  va 0xfffffe00075e56d0
  com.apple.iokit.IOGPUFamily                     text  47.3 KB  va 0xfffffe000798b490
  com.apple.iokit.IOMobileGraphicsFamily          text  37.8 KB  va 0xfffffe00079c45b0
```

结论修正：物理布局与旧式预链接内核**同构**（kext 段在父级 coalesced 区间内），
`emit_prelink.py` 的段拷贝 + `append_segment` 增长空 `__PRELINK_TEXT` 的逻辑可以直接用。
真正的缺口收敛为两处：

1. 组件发现：旧工具用 `_PrelinkExecutableLoadAddr` 在父镜像里定位 kext 头；文件集内核要在
   顶层解析 `LC_FILESET_ENTRY`（本工具已实现解析，346/346 全部可枚举）。
2. 指针重绑：旧工具读 `_PrelinkLinkKASLROffsets`（裸 64 位指针表），macOS 26.5 用链式修复
   （`nabu_pac_strip.py` 已实测 982,911 个指针、format 8），需要把链式槽位解析成
   “站点→目标”后再按 slide 重写。

内核侧 ABI 也已就位：`0008-nabu-sparse-prelink-index.patch` 打开 kalloc 的 `__macho_list`
稀疏 prelink 解析并导出 `kalloc_sparse_prelink_index_version = 1`（`emit_prelink.py` 的准入检查）。

## 07:05 更新：fileset prelink 原型（阶段一：布局）跑通

新增 `scripts/prelink_fileset_kernel.py`。对 `--root com.apple.filesystems.apfs` +
`com.apple.driver.AppleMobileFileIntegrity` 求依赖闭包后实测：

```
组件 9 个，组件正文 3,227,691 B
__PRELINK_TEXT   va 0xfffffe0008a0c000  size 745,472  fileoff 24,788,992
__PLK_TEXT_EXEC  va 0xfffffe0008b32000  size 2,523,136
__PLK_DATA_CONST va 0xfffffe0008d9a000  size 331,776
__PRELINK_DATA   va 0xfffffe0008daf000  size 86,016
稀疏头索引 __macho_list va 0xfffffe0008b31f38，9 个条目
输出 /tmp/nabu-prototype.macho 28,475,392 B（阶段一，loadable=false）
```

即：文件集内核缓存的 kext 段可以被搬进 native kernel 的空占位段，段命令/节命令原地
改写、数据追加在文件尾，与 `emit_prelink.py` 的 `append_segment` 思路一致。

## 阶段二待办（下一轮）

1. **链式修复解析位置修正**：`LC_DYLD_CHAINED_FIXUPS` 只出现在**顶层**镜像（1 个），
   不在每个 fileset 条目里；当前实现按条目找，所以 `pointer_slots_total` 报 0。要按
   顶层段索引遍历 `starts_in_image`。
2. **指针重绑**：对每个被搬运组件，遍历其数据段链式槽位，解析 rebase/bind 目标；
   组件内目标 → `target + slide`；内核符号 → 用 native kernel 导出表按名解析；写回裸 64 位。
3. **kmod 元数据**：组件的 `_PrelinkKmodInfo` 结构（start/stop、header 地址）也要按新
   布局重写，否则 kext 加载链表断开。
4. 之后才轮到 `__PRELINK_INFO` plist 重写（新 VA 下的 kext 表）。

## 07:25 更新：阶段二（指针重绑）实测数据

`scripts/kc_chains.py` 解出顶层链式修复的真实布局：

```
LC_DYLD_CHAINED_FIXUPS 只有顶层 1 个；格式 8（DYLD_CHAINED_PTR_64_KERNEL_CACHE）
槽字：target:30 | cacheLevel:2 | diversity:16 | addrDiv:1 | key:2 | next:12 | isAuth:1
target 是相对镜像基址 0xfffffe0007004000 的偏移
全部 982,911 个槽的 target 都落在本镜像顶层段内（0 个越界）→ 基址假设成立
```
顶层段：`__TEXT 0x7004000/32K`、`__PRELINK_TEXT 0x700c000/12.75M`、`__DATA_CONST 0x7ccc000/12.4M`、
`__TEXT_EXEC 0x88a0000/57.8M`、`__PRELINK_INFO 0xbfcc000/4.29M`、`__DATA 0xc3e4000/3.8M`、
`__LINKEDIT 0xc788000/24.8M`。

全量（345 个有镜像的 kext，另 26 个 codeless 只有 plist）：
```
component_text_bytes      59,873,163
copied_segments          1,377
pointer_slots_seen        982,911
pointer_rewrites          916,450（其余 66,461 个槽不在被搬运的段内）
  ├ 指向被搬运组件 → 重定位   250,759
  └ 指向未搬运区间   665,691  （__TEXT_EXEC 658,265 / __DATA 5,120 / __DATA_CONST 2,303）
输出 /tmp/nabu-full.macho  99,581,952 B  sha256 3f207388f3b6bb21…
耗时 2m47s，稀疏头索引 345 条
```
最小集（apfs + AMFI，9 组件）：24,848 槽重写，内部 11,859、外部 12,989。

结论：**内部引用重定位已闭环**（组件→组件全部改到新 VA）。剩余 66 万“外部”目标不是枚举不足
（346 个子镜像的 `__TEXT/__TEXT_EXEC/__DATA_CONST/__DATA` 已全部纳入、区间零重叠），而是目标落在
子镜像未被搬运的段里（`__LINKEDIT`/`__KLD`/`__PPLTEXT`/`__LASTDATA_CONST` 等，共 23 段/镜像），
以及指向原生内核符号的引用（内核符号地址由源码重建决定，不能沿用缓存里的旧地址）。
下一轮做两件事：①把每镜像全部 23 段按属性并入对应 coalesced 段；②对落在原生内核符号表的
目标改用 `kernel.development.nabu` 的符号解析结果（名称 → 新地址）。

## 08:05 更新：跨构建指针问题的决定性证据 + ESP 载荷树组装

对全部 982,911 个链式槽做目标归属统计（把 346 个子镜像的全部 1,749 个段建索引）：

```
可归属到子镜像：982,911（100%）；无归属：0
引用目标按被引用镜像统计 top6：
  com.apple.kernel                     731,678  ← 74.5%
  com.apple.iokit.IOThunderboltFamily   17,453
  com.apple.iokit.IO80211Family         12,382
  com.apple.driver.AppleM2ScalerCSCDriver 11,303
  com.apple.iokit.IOHIDFamily            8,798
  com.apple.iokit.IOUSBHostFamily        8,034
```

结论（对方案有决定性影响）：**74.5% 的指针指向内核缓存里的 Apple 版 `com.apple.kernel` 镜像**。
我们是从源码重建的 XNU（同一基址、不同内部布局），链式槽里只有 30 位偏移、没有符号名
（`imports_count = 0`），因此**这些指针无法靠重写搬运**：kext 与 Apple 内核是按同一份构建预链接的，
换内核就必须重新链接 kext（按未定义符号名解析）。链式/指针层面的“通用预链接器”到此为止。

可继续的两条路：
1. 先用**裸内核**上机（无 kext），拿 `NABULOG2` 串口日志——这是明天设备的第一个可执行目标；
2. kext 侧改为「按未定义符号名重链」或「用源码构建自有驱动」，不沿用 Apple 缓存里的绝对地址。

ESP 载荷树已组装（不写分区，仅供复制到 ESP 或 `fastboot boot` 试验）：

```
EFI/UniXNU/UniXNU.efi              90,112  19e1b4fff9a7b3ce
EFI/UniXNU/Drivers/DarwinBoot.efi  45,056  4ceb18422e446db5
EFI/UniXNU/Drivers/UniXNUDxe.efi   16,384  ecf6729ce8bb2582
EFI/UniXNU/HelloPayload.efi        20,480  c3b2f683440f1db7
EFI/UniXNU/Payloads/kernel.macho   24,787,072  83ec42f2bd5d0b67（0008 版内核）
EFI/UniXNU/Payloads/kernel.macho.adt    5,428  445f9e4b36974c89（旧 sheng 派生，见下）
EFI/UniXNU/unixnu.toml                  454
```

**ADT 是当前硬缺口**：用 `UniXNU-nabu/device_bringup/adt/validate_adt.py` 严格校验，
现有 `kernel.macho.adt` **14 项检查里失败 8 项**：

```
required_nodes     缺少顶层 defaults 节点
nvram_proxy_props  缺少 /chosen/nvram-bank-size
chrp_nvram_image   nvram-bank-size 非法
random_seed        缺少 /chosen/random-seed
cpu_topology       存在 cpu1..cpu7（受控首启要求单核）
arm_io_ranges      /arm-io/ranges = [0x17000000,0x17000000,0x8000000]，应为 [0x0,0x17000000,0x8000000]
intc_gic           缺少 /arm-io/intc 节点
forbidden_extras   存在 /arm-io/ufs（首启禁 UFS 节点）
```

即该 ADT 仍是 sheng 常量（GIC 0x17100000/ranges 0x17000000），nabu 需要 GIC 0x17a00000/redist
0x17a60000、timer 19.2 MHz、单核首启、无 UFS 节点的版本。下一轮按校验器的 14 条契约生成
`make-nabu-adt.py` 直到 `passed = true`。
