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

---

## 7. 上机前的离线验收（round 6 新增）

### 7.1 镜像结构自检（`verify_android_boot_image`）

```
header_magic ANDROID!   page_size 4096
kernel 字段 gzip → ARM64 Image "ARM\x64"，解压 7,264 B，附 FDT 3,335 B（含 qcom board-id / msm-id）
ramdisk NRAM：kernel 24,787,072 B + adt 11,424 B，Mach-O magic 0xfeedfacf
```

### 7.2 DarwinBoot 解析器复刻验收（`scripts/verify_darwinboot_kernel.py`）

把 `DarwinBoot.c:119 ParseKernel()` 的全部拒绝条件在主机侧重实现，结果是
**accepted = True**，说明设备端加载器不会在第一步就否掉这颗内核：

```
__TEXT           0xfffffe0007004000 0x001ac000  fileoff 0x00000000  prot 1
__DATA_CONST     0xfffffe00071b0000 0x001b8000  fileoff 0x001ac000  prot 1
__TEXT_EXEC      0xfffffe0007368000 0x0093c000  fileoff 0x00364000  prot 5
__KLD            0xfffffe0007ca4000 0x00004000  fileoff 0x00ca0000  prot 5
__LASTDATA_CONST 0xfffffe0007ca8000 0x00004000  fileoff 0x00ca4000  prot 1
__KLDDATA        0xfffffe0007cac000 0x00008000  fileoff 0x00ca8000  prot 3
__DATA           0xfffffe0007cb4000 0x0016c000  fileoff 0x00cb0000  prot 3
__BOOTDATA       0xfffffe0007e20000 0x000d8000  fileoff 0x00d00000  prot 3
__LINKINFO       0xfffffe0007ef8000 0x00050000  fileoff 0x00dd8000  prot 1
__LINKEDIT       0xfffffe0007f48000 0x0097c000  fileoff 0x00e28000  prot 1
entry 0xfffffe000759f488 ∈ __TEXT_EXEC   base 0xfffffe0007004000   span 0x18c0000
cpusubtype 低字节 0（arm64，不触发 ARM64E 的 EL1 PAC 检查）
```

检查项与结论：magic/cputype/filetype ✓；sizeofcmds 与命令对齐 ✓；10 个非零段全部
`vmsize ≤ WINDOW_SIZE(256 MiB)`、`filesize ≤ vmsize`、文件内不越界、两两不重叠 ✓；
恰好一条 `LC_UNIXTHREAD`（cmdsize 288、flavor 6、count 68）✓；入口落在可执行段内 ✓；
整体 span 24.75 MiB ≪ 窗口 ✓。

反向对照：sheng 时代的 `kernel-nabu26.macho` 在同一检查下 `accepted = False`
（arm64e 子类型 + 入口不在可执行段），说明这套验收确实会拒绝不合格镜像，
不是空跑通过。

### 7.3 ESP/EFI 路线一致性

- UniXNU 读的配置文件名确认为 `unixnu.toml`（`UniXNULib/Config.c:1511`），
  与 `out/esp-payload/EFI/UniXNU/unixnu.toml` 一致；`path/adt` 指向 `Payloads/` 下实际存在的文件。
- 菜单里的 `type = "xnu"`、`Payloads/kernel.macho`、`Payloads/kernel.macho.adt` 三项与 ESP 目录逐一对应。

---

## 8. stage1 契约审计（round 7 新增，含一处会直接卡死的修复）

读 `platform/nabu/macos26/boot/nabu_darwinboot_fastboot.c`（fastboot 路线真正执行的那段
裸机加载器）后，逐项对照我们打的镜像：

| 项 | stage1 原本 | 我们的镜像 | 处理 |
|---|---|---|---|
| NRAM 定位 | FDT initrd 或扫 Bank0 `0x81000000..0x9b000000`（4 字节 `NRAM` + 0xC 处必须是 `0xfeedfacf`） | ramdisk 由 ABL 放入 Bank0 | 一致，无需改 |
| 窗口基址 | `NABU_OWNED_WINDOW_PA = 0xc0000000` | ADT `dram-base = 0xc0000000` | 一致 |
| 窗口大小 | 硬编码 `0x20000000`（512 MB） | ADT `darwinboot-memory-size = 0x40000000`（1 GiB） | **已改 stage1 为 1 GiB** |
| boot-args | 硬编码 `cpus=8 … t733-managed-rt-normal=1 native-ufs-root=1` | ADT 只有 `cpu@0` | **已改 `cpus=1`，去 UFS 根与 t733** |
| 段搬运 | `0xc0000000 + (vmaddr - min_va)`，跳过 filesize=0 | 与 `ParseKernel` 语义一致 | 一致 |
| 交接 | `VirtualBase=min_va`、`PhysicalBase=0xc0000000`、`DeviceTree=min_va+偏移` | ADT/内核链接基址匹配 | 一致 |
| 显示 | `args->Video[]` 用 `NABU_GOP_*` | 与 ADT `framebuffer-*` 同源 | 一致 |

不修的后果：ADT 只描述 1 个 CPU，XNU 拿 `cpus=8` 会等不存在的从核；`native-ufs-root=1`
会让内核在没有 UFS 节点的情况下找根设备。两者都会让首启表现成“卡死”而不是给出诊断。

### 8.1 重打后的镜像

```
stage1  nabu-darwinboot-fastboot.bin   7,232 B（原 7,264 B）
容器    out/nabu/image/nabu-macos26-kernel.img   24,813,568 B
        sha256 d9557c9e7dfbed1d7693222ae019d9eee34cd8ab6b8e70a9f4c69d17950df606
NRAM    kernel 24,787,072 B + adt 11,424 B（14/14 通过）
stage1 生效参数：-v debug=0x14e keepsyms=1 cpus=1 armcpu-self-ipi=1 nabu-vc-scale=2
```

> 注意：fastboot 路线用的 boot-args 来自 stage1 里的 C 字符串，**不是** NRAM 里的
> `unixnu.toml`（后者只对 EFI/UniXNU 路线生效）。今天两者已统一为同一组参数。

---

## 9. QEMU 预跑 stage1（round 8 新增，真机前唯一一次运行时验证）

装好 `qemu-system-aarch64 8.2.2` 后，把 fastboot 路线真正执行的那段裸机代码搬进
`-M virt` 里跑了一遍。给 `nabu_boot_common.h` 加了 `#ifdef NABU_QEMU_SERIAL`
诊断镜像（把屏显文字同时写到 PL011），生产镜像编译结果逐字节不变
（stage1 仍 7,232 B / `a8eac3b2…`，boot image 仍 `d9557c9e…`）。

复跑命令：`python3 scripts/qemu_stage1_preflight.py`（自动编诊断版、把 NRAM 放到
`0x81000000`、跑 15 s、解析串口、判定契约）。

### 9.1 结果：stage1 契约 PASS

```
DARWINBOOT NABU SM8150 - FASTBOOT RAM LOADER (macOS 26.5 / 25F71)
EL:                  0x0000000000000001      ← 以 EL1h 进入
MIDR:                0x00000000412fd471      ← QEMU cortex-a710
MPIDR:               0x0000000080000000
SCTLR:               0x0000000030c50838      ← 入口时 MMU 状态，随后被 DarwinEnter 关闭
FDT PA:              0x0000000048000000
NRAM PA:             0x0000000081000000      ← Bank0 扫描命中我们放的容器
KERNEL BYTES:        0x00000000017a3880      ← 24,787,072，与内核文件字节数完全一致
HANDOFF -> ENTRY PA: 0x00000000c059b488      ← 0xc0000000 + (0x759f488 - 0x7004000)
ARGS PA:             0x00000000c18c0000      ← 窗口内 args（span 0x18c0000 对齐）
```

这一段证明：NRAM 定位、Mach-O 头校验、10 个段逐个搬进 `0xc0000000` 窗口、
`DarwinBootArgs`（0x480 字节）构造、`DeviceTree` 指向窗口内 ADT、以及
`DarwinEnter` 的 cache 维护 + 关 MMU + 跳转，全部按设计执行。

### 9.2 跳转之后：XNU 已经跑在虚拟地址上

QEMU 记录到第一条异常时 `ELR = 0xfffffe0007bdc684`——这是**内核虚拟地址**
（`__TEXT_EXEC` 内），说明 XNU 的 `__start` 已经完成页表建立、把 PC 切到
`0xfffffe00…` 空间后继续执行了十几 MB 才停下（该处指令是普通的
`sub sp, sp, #0xd0` 函数序言，说明是 QEMU 的 CPU 模型在更早的指令上抛
EC=0 异常，属 virt/A710 模拟缺口，不是寻址错误）。

也就是说 `VirtualBase = 0xfffffe0007004000` / `PhysicalBase = 0xc0000000` 这套
重定位在运行时是成立的；真机上 SM8150 的 A76/A55 与 QEMU 的 A710 模型不同，
这一步不能作为真机结论，但足以排除“交接参数写错导致第一步就崩”的可能。

### 9.3 一个待观察项

`MIDR 0x412fd471`（Cortex-A710）是 QEMU 的 CPU；真机是 A76（0x414fd0b1 一族）。
若真机在同一位置停下，说明是内核里某条 armv8.2 之外的指令；届时用
`fastboot boot` 的屏幕输出即可区分。

### 9.4 QEMU 里 XNU 停在哪（round 9 追加）

用内核符号表反查第一条异常的落点：

```
异常 ELR 0xfffffe0007bdc684 ∈ __TEXT_EXEC（段内 0x874684）
前一条指令 0xfffffe0007bdc680 = brk #0xbffc，所属符号 _timer_queue_assign.cold.1
  → 这是 timer_queue_assign() 里 assert 的冷路径（panic 陷阱）
```

`osfmk/arm/arm_timer.c:217 timer_queue_assign()` 的冷路径条件是：

```c
if (cpu_data_ptr->cpu_running) { … } else {
    assert(ml_is_quiescing());          /* ← 这里炸 */
    queue = &cpu_datap(boot_cpu_id)->rtclock_timer.queue;
}
```

而 `cpu_running` 只有 `osfmk/arm64/cpu.c:753`（`cpu_init()` 末尾）会置 TRUE。也就是说
引擎跑到“某处比 `cpu_init()` 更早地武装了 rtclock 定时器”，且 QEMU 的 62.5 MHz 计数
频率与我们 ADT 里写的 19.2 MHz 不一致（`/cpus/timebase-frequency`），中断提前到来，
于是踩到这条 assert。

结论分两层：

1. **对 nabu 是利好**：能走到 `timer_queue_assign`，说明 XNU 已经完成 `pmap_bootstrap`
   → 平台发现（我们的 ADT 被 `ArmDTPlatform` 接受）→ GIC/定时器注册 → Mach 初始化，
   这是“裸内核能在没有 kext 的情况下跑起来”的最强证据。
2. **真机首启要盯的一处**：如果 nabu 上也在这条 assert 停下，说明 timebase 或
   timer 中断路径与 ADT 不一致（nabu 实测 19.2 MHz，与 ADT 相同，正常不该踩）。

### 9.5 指令集安全性扫描

对 24.8 MB 的成品内核做 PAC 指令全量扫描：

```
PACIA/PACIB (0x3f23_03d5 / 0x7f23_03d5) → 0 处
AUTIA/AUTIB (0xff23_03d5 / 0xbf23_03d5) → 0 处
```

即内核中**没有任何指针认证指令**，与 SM8150 的 Cortex-A76/A55（armv8.2，无 PAC）匹配；
`cpusubtype` 也是低字节 0（arm64，非 arm64e）。这条与 §7 的 DarwinBoot 检查互为印证。

---

## 10. 设备侧一键跑本（round 10 新增）

报告里的步骤做成了可直接执行的脚本 `scripts/nabu_device_session.sh`（已 `bash -n`
检查 + 全流程 dry-run 验证过；除 ESP 的字节备份外不写任何分区，全程没有
`fastboot flash` 这个词的有效调用）：

```bash
cd ~/.gemini/antigravity/scratch/macOS-ForNabu/macOS-ForSheng

./scripts/nabu_device_session.sh check          # 只读：镜像哈希 + 两个 slot 变量
./scripts/nabu_device_session.sh check --dry-run # 不接设备先看会执行什么
./scripts/nabu_device_session.sh backup         # ESP 字节备份（dd + pull + 校验）
./scripts/nabu_device_session.sh boot           # fastboot boot，仅 RAM
./scripts/nabu_device_session.sh collect        # 事后收 dmesg / 提示要拍哪些照片
```

- `check` 会打印镜像大小/sha256，并读 `slot-unbootable:b`、`slot-retry-count:b`、
  `current-slot`，方便和“跑之前”的值对照。
- `backup` 走 `adb shell su -c 'dd if=/dev/block/by-name/esp …'`，拉到
  `private/nabu-backup/esp-<时间戳>.img` 并写 `.sha256` 旁注。
- `boot` 打印**屏幕上应该依次出现什么**（banner → NRAM PA/KERNEL BYTES/HANDOFF →
  XNU 详细输出 → panic 或停在 Darwin Kernel Version 之后），以及
  “60 秒无输出就 电源+音量下 回 fastboot” 的处理方式。
- `collect` 尝试抓 `adb shell dmesg`，并提示另外两样要带回来的东西：panic 屏幕照片、
  `fastboot getvar all | grep slot-` 的输出。

dry-run 实测（节选）：

```
image: …/out/nabu/image/nabu-macos26-kernel.img (24813568 bytes)
sha256: d9557c9e7dfbed1d7693222ae019d9eee34cd8ab6b8e70a9f4c69d17950df606
  [dry-run] fastboot getvar slot-unbootable:b
  [dry-run] fastboot getvar slot-retry-count:b
  [dry-run] fastboot boot …/nabu-macos26-kernel.img
```

## 11. 现在的状态一句话

能离线做的都做完了：内核（0008 版）→ DarwinBoot 解析器复刻验收 PASS →
ADT 严格契约 14/14 → stage1 契约审计并修掉 `cpus=8`/UFS 根/窗口不一致 →
QEMU 里 stage1 全流程 PASS、XNU 跑到 Mach 初始化（`timer_queue_assign` 冷路径）→
用户态 dyld/launchd 完成 PAC 降级 → 镜像 `d9557c9e…` 待上机。

剩下三件事都卡在设备或外部条件上：真机首启实测、kext 重链、BaseSystem 的 `.aea`
个性化加密（需 Apple TSS 密钥，离线无解）。

---

## 12. kext 重链可行性量化（round 11 新增，给 §4 的硬边界补证据）

工具：`scripts/analyze_kext_symbols.py`（新增）。思路是内核缓存里每个 kext 仍是独立
Mach-O 镜像、各自带 `LC_SYMTAB`，所以**未定义符号的名字还在**，可以拿我们源码重建的
内核导出表去比对。

```
我方内核导出符号        87,024
KC 里含未定义符号的 kext  342
未定义符号合计          150,887
我方内核能解析           123,100   → 81.6%
```

典型 kext：

| kext | 未定义 | 可解析 | 比例 |
|---|---|---|---|
| AppleBCMWLANCore | 1,574 | 512 | 33% |
| **apfs** | 1,278 | 1,099 | **86%** |
| IO80211Family | 1,014 | 709 | 70% |
| AGXG13G | 1,005 | 550 | 55% |
| IONVMeFamily | 842 | 695 | 83% |
| IOThunderboltFamily | 827 | 819 | **99%** |

**怎么读这组数**：重建内核的 ABI 忠实度其实很高（apfs 86%、雷电 99%），说明
`NABU` 目标编译出来的 XNU 与 Apple 那颗内核在符号层面基本对齐。但这**不能**变成
“把 Apple kext 装到我们内核上”的办法——真正卡住的不是符号是否存在，而是
§4.1 那个事实：链式修复表在 Apple 构建时就已全部解成绝对地址
（`imports_count = 0`），**槽位与符号名之间的对应关系已经不存在**，没法按名字重建引用。
换句话说：符号表能告诉我们“缺不缺”，不能告诉我们“往哪写”。

### 12.1 由此修正的一条路线（原 §6 的“下一步”细化）

`IOKit` 的核心（`IOService`/`IORegistry`/`IOMemoryDescriptor` 等）本来就编在 XNU 里，
属于内核的一部分；只有 `IOStorageFamily`、`IOUSBFamily` 这类*家族*才是 kext。而我们
仓库里**有 `IOStorageFamily` 的源码**（`sources/macos26/IOStorageFamily`），也有自己的
`UniXNU/drivers/NativeUfs`。所以上机之后真正可推进的路线是：

1. 把 `IOStorageFamily` 与 `NativeUfs` 以**内核内建组件**的方式静态链进
   `kernel.development.nabu`（而不是打成 kext 等加载器搬运），并注册对应的
   IOKit personality；
2. 用 §8 的 stage1 把 UFS 控制器（`0x01d84000`）暴露给这颗内建驱动，只读挂载
   `os_lba 2739968` 所在的卷做验证；
3. 这一步做完，才谈得上“macOS 有自己的根设备”；在此之前，裸内核预期停在
   `timer_queue_assign` 之后的 Mach 初始化/根设备查找。
