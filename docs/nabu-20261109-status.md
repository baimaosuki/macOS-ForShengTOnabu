# nabu（小米平板 5 / SM8150-AC）引导现状 —— 2026-11-09

本文是 **nabu 路线**的权威现状记录。凡与其他 nabu 文档冲突，以本文为准；
历史文档的失效范围见文末「文档时效索引」。

- 目标设备：Xiaomi Pad 5 `nabu`，SM8150-AC（Cortex-A76/A55，ARMv8.2-A，无 FEAT_PAuth）
- 内核：源码构建的 macOS 26.5 / XNU 12377.121.6 ARM64，经 Uniform Slide 重定位
- 迭代路线：**ESP + rEFInd + UniXNU**（`fastboot boot` 的 NRAM 直载路线为旁路，见 §5）

## 1. 一句话结论

引导链已全部打通，内核跑到 **stage 2 `IOKit entry`** 后**挂住**（死等，不是 panic）；
尚未到达 `Root mounted`，因此 launchd / WindowServer 均未开始。

## 2. 已验证的引导链

```
stock UEFI → rEFInd (\EFI\Boot\bootaa64.efi)
           → \EFI\UniXNU\UniXNU.efi
           → Drivers/DarwinBoot.efi            （[driver] 注入）
           → Payloads/kernel.macho + Payloads/kernel.macho.adt
           → XNU
```

屏显证据（本轮实拍）：

- `MAC Framework successfully initialized`
- `using 890 buffer headers and 890 cluster IO buffer headers`
- `ZZ rs IOCpuInterruptController: not registry member at registerService()`
- 启动条 `BOOT [###-------]`

启动条编码：`sheng_boot_mark()` 置 `1u << stage`（`platform/nabu/macos26/log/boot_progress.h`，
`SHENG_BOOT_STAGES 10`：`Kernel / VM ready / IOKit entry / Root mounted / launchd exec /
Recovery exec / WindowServer exec / Display on / First swap / First frame`）。
`[###-------]` = mask `0b0000000111` = stage 0+1+2 ⇒ 当前停在 **`IOKit entry`**。

挂住的形态是**死循环**：无异常、无 panic 寄存器、USB 全无、二维码不出现
（二维码只存在于 panic 路径，见 §4）。

## 3. 三个硬契约（违反任一条都表现为"点不亮"，且不给内核日志）

| # | 契约 | 违反后的可观测现象 | 源码位置 |
| --- | --- | --- | --- |
| 1 | 驱动必须在 `unixnu.toml` **根级** `[[driver]] path = "..."`（或 kit manifest 的 `[driver]`） | `Config->DriverCount == 0` → 串口 `xnu: no loader protocol installed; UniXNU does not provide one` → `EFI_UNSUPPORTED` → 直接回 rEFInd 菜单，内核一个字节未被读 | `UniXNU/package/UniXNUPkg/Library/UniXNULib/Config.c:1414`（根级）、`:949`（kit）；`Boot.c:60` |
| 2 | ADT 必须有 `/chosen/memory-map` 节点，且给 ramdisk 时该节点下要有**恰好 16 字节**的 `RAMDisk` 属性（初值全零） | 属性存在但没给 ramdisk → `EFI_UNSUPPORTED`；节点缺失 → 内核 `brk` 硬断言 | `sources/unixnu-components/DarwinBoot/DarwinBootPkg/Driver/DarwinBoot.c` |
| 3 | ADT 的文本/日志状态由 `scripts/native_devtree.py:32 configure_native_text_state()` 写入（`embedded-panic-log-size = 0x80000` 等） | 面板不出现或尺寸错 | `scripts/package_nabu_fastboot.py:86`、`scripts/package_macos26_kernel.py:158` |

补充：kernel 段里的 **`adt =` 键是空操作** —— `Config.c:1207` 认的键名是 `dtb`；
DarwinBoot 自己去找 `<kernel 路径>.adt`。所以 ADT 只需替换同名文件。

## 4. 日志取证通道实况

现在最缺的是"内核完整日志"，四条通道的实测状态：

| 通道 | 状态 | 阻断原因 |
| --- | --- | --- |
| 屏显面板 | 可用 | 只有 stage 位图、服务计数、`LOG ...` 计数，没有全文 |
| panic 二维码 | **仅 panic 时出现** | 当前是死等而非 panic；`sheng_qr_prepare()` 唯一调用点在被 `sheng_panic_log()` 内部（`platform/nabu/macos26/log/retained_log.h:332`） |
| 内核 → ESP `XNU2LOG.BIN` | 关闭（面板 `LOG off gen=0`） | 需同时满足：`nabu-log=1`、`nabu-log-token=<32 位十六进制>`（`NabuLogUfs.h:107-109`）、ESP 根目录预置 **8,400,896 字节**且占 **2051 个连续簇**的 `XNU2LOG.BIN`（`NabuLogFormat.h`、`NabuLogVolume.h` 的 `nb_no_aliases()`）；另需 `NativeUfs` host 就绪 |
| UEFI 侧 `NativeLogSave` | 代码存在但**无效** | 参数化给的是另一台板：扫描 PA `0x880000000..0x8bb300000` 与 `0x8c0000000..0xa00000000`（34–40 GiB），并硬认 GPT 分区 `macoslog`（LBA 448512–464895、固定 GUID）。nabu 的 DRAM 是 `0xC0000000 + 5 GiB`（顶端 8 GiB），`NativeLogReserve()` 的第一个 `DarwinPhysicalAddress()` 断言即失败，`NativeLogArm()` 因此不 arm |

另：**TWRP 无法读物理内存** —— `/dev/mem` 不存在（`mknod` 后读取返回 `No such device or address`）、
无 `/proc/kcore`、无 `busybox`/`python3`。任何"进 recovery 扫 DRAM"的方案在第一步就失败。

## 5. ESP 账目（保持原生 300 MiB，不动分区表）

- 分区：`/dev/block/sda31`，LBA 2654272–2731071，76800 × 4096 = **314,572,800 B (300 MiB)**
- 挂载：`mount -t vfat /dev/block/sda31 /esp`
- 文件系统：FAT32，4 KiB 簇

| 文件 | 字节 | md5 |
| --- | --- | --- |
| `Payloads/kernel.macho` | 112,390,144 | `6c8fa3b38a9d47b2b1ff545a4e945f4e` |
| `Payloads/ramdisk.bin` | 183,914,912 | `b591fbdc69d051d7ef5949406b5fb8ac` |
| `Payloads/kernel.macho.adt`（**仓库跟踪**） | 11,824 | `0ac51a29ce3b6220ae26934b71ca7ed9`（无 `RAMDisk` 属性，`audit_adt.py` 14 节点全 PASS） |
| `kernel.macho.adt.ramdisk`（**仅设备**，= 上一行插入 16 字节 `RAMDisk`） | 11,876 | `96c93b78ed7cf31c4bc27e4603548dc0` |
| `unixnu.toml`（仓库跟踪版，524 B 原件） | 524 | `1f3e874ed604f00a66711b98e1b91682` |
| `unixnu.toml`（设备当前，`cpus=1` 试验变体） | 561 | `afd072f7c34d133ebc28d52ea2aaf787` |
| `Drivers/DarwinBoot.efi` | 45,056 | `3802c4bdf026e74b4a6d493e125769cd` |

- 已删除 `kernel.prev.macho`（28 MB）以容纳 175.4 MiB 打包 ramdisk；当前占用 288M/299M。
- **已知保真度缺口**：设备实际启动用的是 11,876 B 的 ramdisk 版 ADT，而仓库跟踪的是 11,824 B 原件；`scripts/insert_ramdisk_prop.py` 的产物尚未入库，`NABU-ESP-MANIFEST.json` 因此只 pin 得住原件。补齐前，任何人从仓库复原 ESP 都必须自行跑一次该脚本。
- 受管树：`platform/nabu/macos26/esp/`（14 个载荷入 git，两个大二进制标 `storage:"external"` + 哈希钉住 + `fetch_payloads.py` 取回）。

**两条迭代路线并存，`fastboot boot` 不需要 ESP：**

1. ESP 热替换：`fastboot boot recovery-win.img`（TWRP）→ 挂 `/esp` → `adb push` → 双侧 `md5sum` 一致 → `sync` → `reboot`；
2. fastboot 直载：`scripts/build_nabu_fastboot_image.py` 产出内嵌 NRAM 容器的 boot.img，完全绕过 ESP（其 boot-args 硬编码在 `platform/nabu/macos26/boot/nabu_darwinboot_fastboot.c:318`）。

## 6. prelink / Uniform Slide（已收口）

- 全镜像只需**一个常量 slide `0x2000000`**；`ADRP` 保持 **0 处修改**（有断言）
- 拷贝区间内指针槽 26,504 个；其中 **13,358** 个指向集合外（12,940 → `__TEXT_EXEC`，336 → `__DATA`，82 → `__DATA_CONST`）已重绑定
- 跨模块 `BL` 重编码 19,860 处；栈保护分支 `b.ne #0xd478f28`（字 `0x54000201`）保持原样
- 尚未审计：`LDR (literal)`、以及经结构体指针的 `BLR`

## 7. 当前待办（优先级序）

1. **ADT `/cpus` 契约违反**：ADT 只有 1 个 CPU 节点（`/cpus` `p=4 c=1`，`/cpus/cpu@0`），而仓库跟踪的 toml 写 `cpus=8` —— 内核会用 8 核拓扑去匹配单核 ADT。设备上已放 `cpus=1` 变体做单变量验证，**待读屏判定卡点是否移动**。
2. **恢复日志取证能力**：优先打通「内核 → `XNU2LOG.BIN`」（不碰分区表、只需 token + 文件），其次才是把 `NativeLogSave` 的 PA 区间/分区校验改成 nabu 参数并重编 `DarwinBoot.efi`。
3. **UFS 写路径**：`ShengUfs/NativeUfs.cpp` 目前只是寄存器探测，`gHost.ready` 为假；这是 §4 第 3 行与将来挂根的共同前置。
4. **小根盘**：Stage 4 只需 < 10 MB 的根/ramdisk，但当前没有 APFS 生成器（需 `newfs_apfs` 或自写写入器）。
5. **备份 GPT 同步**：盘尾备份表仍描述 2 GB ESP；主表为准。

## 8. 文档时效索引

| 文档 | 时效与范围 |
| --- | --- |
| 本文 `docs/nabu-20261109-status.md` | **权威** |
| `docs/nabu-kernel-bringup-lessons.md`「当前边界」 | **已作废**（2026-11-07 判断：SMP 8 核已过 / 卡 `trust_cache_init` / 下一关 stage 2），已在原处标注 |
| `docs/nabu-night-report-20261107.md` §8 的 boot-args 行 | 只对 **fastboot stage1** 成立（该 C 文件已改 `cpus=1`）；**ESP 路线的 boot-args 来自 `unixnu.toml`，仍是 `cpus=8` + `native-ufs-root=1` + `t733-managed-rt-normal=1`** |
| `docs/nabu-20261107-status.md` | 历史快照（2026-11-07 05:00–08:05），保留但不代表现状 |
| `docs/status.md` | **sheng（SM8550）路线**的状态，与 nabu 无关，勿混用 |

## 9. 外部静态审计报告的逐条裁定（2026-11-09）

收到一份以 `nabu_static_analysis_and_log_plan.md` 为题的静态审计报告。逐条对源码核对后的裁定：

| 报告结论 | 裁定 | 依据 |
| --- | --- | --- |
| 屏显文字去 `SHENG` 化（列出 3 处） | **采纳，且范围更大** | 面向屏幕/二维码的字面量实为 **12 处**：`panel_console.h`（PANIC DETAIL / FIRST EL1 SYNC / FIRST PANIC / BOOT LOG / `SHENG: ` 共 5 处）、`retained_log.h`（`\nSHENG: ` / `arm_init` / `panic:` 共 3 处）、`boot_progress_kernel.h:57`、`smp_log.h:22`、`process_output.h:27`、`panic_qr.h:40`。已全改为 `NABU`；标识符与宏名（`sheng_*`、`SHENG_LOG_PREFIX`）一律不动 |
| 面板行数上限 24 → 36~40，理由"实机只剩 16 行" | **采纳改动，但理由数字要更正** | 实机 1600×2560、`scale=2`、`top=608`：`available_rows=(2560-608)/2=976`、`rows=(976-32)/16=59`、`panic_rows=16` ⇒ `normal_rows=43`，被硬上限 24 截断，**不是只剩 16 行**。已放宽到 **40**（不是 43：43 行时 `top+pixels` 正好等于 2560，零余量；40 行时 2464 ≤ 2560）。见 `vm_trace.h:69` |
| 底部 `LOG off` 是因为"设备树里关闭了 UFS 节点" | **证伪** | `LOG off` 只表示 `log_state == 0`（`boot_progress.h:123`）。真正的前置门是 `nabu-log-token` 缺失（`NabuLogUfs.h:107-109`，返回 `NB_TOKEN_FLAG_ABSENT`）；且 boot-args 里 `native-ufs-root=1` 与 4 个 `native-ufs-*` 参数都在（`esp/EFI/UniXNU/unixnu.toml:16`），UFS 节点并未关闭 |
| 暗坑 1：`cpus=8` 与单核 ADT 冲突 | **成立** | ADT `/cpus` 只有 `/cpus/cpu@0`；设备上已放 `cpus=1` 变体（md5 `afd072f7c34d133ebc28d52ea2aaf787`）做单变量；仓库跟踪的 524 B 原件仍是 `cpus=8`（md5 `1f3e874ed604f00a66711b98e1b91682`），两者差异必须显式记录 |
| 暗坑 2：`IOFindBSDRoot()` 60 秒死循环是"当前真机阻塞的真因"，对策 `rd=md0` | **两处需更正** | (a) 代码是 `IOService::waitForMatchingService(matching, 30ULL * kSecondScale)` —— **30 秒有界**，超时只打 `!BSD` 再继续（`IOKitBSDInit.cpp:725-729`），没有无限空转；(b) `initrd` 与 `rd=md0` **只存在于设备当前的 561 B 试验变体**（md5 `afd072f7c34d133ebc28d52ea2aaf787`）；仓库跟踪的 524 B 原件（md5 `1f3e874ed604f00a66711b98e1b91682`）既无 `initrd` 也无 `rd=md0` |
| 暗坑 3：`ml_static_ptovirt()` 只认启动物理窗口 | **方向成立** | DarwinBoot 已在 owned window（`0xc0000000` 起 1 GiB）内搬运；待确认的是 `/chosen/memory-map` 的 `RAMDisk` 16 字节必须由 DarwinBoot 填入真实 parcelled 地址（我们只预置了全零占位） |
| 暗坑 4：根卷必须是 APFS | **成立** | 与本文 §7.4 一致：当前没有 APFS 生成器，Stage 4 的小根盘仍是未解前置 |
| 暗坑 5：AMFI / TrustCache 处决 launchd | **部分过时** | 镜像已带预链接 kext 并完成指针重绑，"0 个预链接 kext 必然在 `trust_cache_init` panic"的前提不再成立；`-s` 与 `amfi_get_out_of_my_way=1` 目前**不在** boot-args 里，是否必需未验证 |
| 暗坑 6：PAC 指令在无 FEAT_PAuth 时安全穿透 | **正确** | `PAC*`/`AUT*` 属 HINT 编码空间，无该特性时执行即 NOP；真正的加密指令由内核侧仿真处理 |

### 9.1 一个可判定实验（优先级高于任何 boot-arg 改动）

`IOFindBSDRoot()` 在 `nabu-source-text=1` 时**一进函数**就打印
`NABU SOURCE V2 ROOT_SEARCH platform=… gic=… timer=…`（`IOKitBSDInit.cpp:715`）。

- 屏上**没有**这一行 ⇒ 根本没进 `IOFindBSDRoot`，卡点在更早的 `PE_lockdown_iokit` / IOKit 注册（`ZZ at …` 那一段），所有关于 `rd=md0`、ramdisk 放置、APFS 的推理都还不能启用；
- 屏上**有**这一行 ⇒ 才轮到 `md0` / `RAMDisk` / 根卷格式排查。

因此下一次开机的唯一判据是：**有没有 `NABU SOURCE V2 ROOT_SEARCH`**。

**2026-11-09 21:47 首次上机结果：该行没有出现。** 见 §10。

### 9.2 本轮代码改动尚未生效

`platform/nabu/macos26/log/*.h` 只在构建期内被拷进 XNU 源码树
（`scripts/build_macos26_xnu.py:271-296`），因此：

- `out/macos26-xnu-nabu/source/pexpert/arm/pe_sheng_panel.h` 等 staged 副本**仍是旧文本**；
- 两处改动（12 个字面量 + 行数上限 40）要生效，必须经 macOS ARM64 runner 重编内核
  （`scripts/build_macos26_xnu.py:456` 硬门禁），再按 §5 的 ESP 热替换流程落地。

## 10. 首次上机：NABU 改版内核的运行结果（2026-11-09 21:47）

### 10.1 刷入内容（逐文件 md5 双侧核对）

| 文件 | 大小 | md5 | 结论 |
| --- | --- | --- | --- |
| `Payloads/kernel.macho` | 112,390,144 | `61b89561eeebb0a83f8cc13f4df8bb94` | **新内核**（提交 `405af29` + uniform15 重打包） |
| `Payloads/kernel.macho.adt` | 11,876 | `96c93b78ed7cf31c4bc27e4603548dc0` | 未动（ramdisk 版） |
| `Drivers/DarwinBoot.efi` | 45,056 | `3802c4bdf026e74b4a6d493e125769cd` | 未动 |
| `unixnu.toml` | 561 | `afd072f7c34d133ebc28d52ea2aaf787` | 未动（`cpus=1` + `initrd` + `rd=md0`） |

新内核的 prelink 报告（`out/nabu-uniform15.405af29.json`）与上一版**逐字段零差异**：
`components=11`、`copied_segments=44`、`pointer_rewrites=26504`、
`branch_rewrites_kernel=19860`、`branch_unresolved=2`、`output_bytes=112390144`，
只有 `output_sha256` 变化 ⇒ 改动没有扰动布局，只是字符串与面板行数。

### 10.2 屏上观察

```
NABU - BOOT LOG
NABU: PE_lockdown_iokit
```

- 面板标题已是 `NABU - BOOT LOG`，状态行前缀是 `NABU: ` ⇒ **改品牌在硬件上生效**，且 `retained_log.h` 的 `\nNABU: ` 前缀确实被链接。
- 状态行的内容是最后一条 `kernel_bootstrap_log()`：`osfmk/kern/startup.c:913` 的
  `kernel_bootstrap_log("PE_lockdown_iokit")`，紧接着执行 `PE_lockdown_iokit()`
  （`pexpert/arm/pe_init.c:438` = `zalloc_iokit_lockdown(); StartIOKitMatching();`）。
- **全屏没有其余控制台文本** —— 但原因不是内核没跑到，而是控制台输出被丢弃了，见 §11 的根因（`retained_log.h` 的 24 行守卫拒绝了 40 行，`sheng_panel_ready` 永远为 0）。
- 关键判据 `NABU SOURCE V2 ROOT_SEARCH` **未出现**。

### 10.3 结论（**已撤回**，2026-11-09 22:05）

本节的原始推断有两条，均因未察觉"控制台被丢弃"而作废：

1. ~~外部报告"暗坑 2：`IOFindBSDRoot()` 60 秒死循环是当前阻塞真因"在硬件上被证伪~~
   —— **撤回**。`NABU SOURCE V2 ROOT_SEARCH` 缺失**不能**当作"没进 `IOFindBSDRoot`"的证据：
   控制台字符在 `sheng_panel_ready == 0` 时全部被 `sheng_log_putc` 丢弃，这一行根本到不了屏幕。
2. ~~卡点收敛到 `PE_lockdown_iokit()` 内部~~ —— **撤回**。状态行只反映最后一条
   `kernel_bootstrap_log()`；`bsd_init` 之后不再产生新的 bootstrap stage 日志，
   所以状态行停在 `PE_lockdown_iokit` 并不能说明内核停在那里。

就报告本身而言，§9 里对"30 秒有界、`rd=md0` 只在设备变体"的两处更正是**独立的源码事实**，
不依赖本条撤回，仍然成立。

### 10.4 操作侧新事实（下次别再踩）

- **槽位**：设备为 A/B 双槽。`current-slot=b` 时 `fastboot boot recovery-win.img` 会
  `FAILED (remote: 'Failed to load/authenticate boot image: Load Error')`；
  `fastboot --set-active=a` 之后同一镜像 `Booting OKAY`。
- **ESP 与槽位无关**：`esp -> /dev/block/sda31` 在 a/b 下是同一个分区（299 MiB，288 MiB 已用）。
- **换内核必须先删后写**：剩余空间只有 11 MiB，`rm` 旧 `kernel.macho` 后为 118 MiB，
  再 `adb push` 112,390,144 B；推送后 `md5sum` 复核一致，`sync` + `umount` 再重启。
- 挂死状态下 adb/fastboot 都不在线，只能物理长按电源断电后音量下 + 电源进 bootloader。

### 10.5 下一个单变量

把 `unixnu.toml` 退回仓库跟踪的 524 B 原件（`cpus=8`、无 `initrd`、无 `rd=md0`），
**内核保持 `61b89561…` 不变**。若屏上重新出现 `MAC Framework` / `ZZ at ArwCpu` 系列，
即证明是 `initrd` 或 `cpus=1` 把停机点提前了；若仍停在 `PE_lockdown_iokit`，
则说明卡点与新内核改动无关，需要在 `PE_lockdown_iokit()` 内部加细粒度标记
（`zalloc_iokit_lockdown` 前/后、`StartIOKitMatching` 前/后）再开一刀。

## 11. 真正根因：面板行数契约只改了上游（2026-11-09 22:05）

### 11.1 故障链（源码级）

```
vm_trace.h:73    bounded_rows = normal_rows > 40 ? 40 : normal_rows;   // 40，上游策略
vm_trace.h:82    sheng_console_rows = bounded_rows;                    // 40
arm_init.c:397   sheng_log_bind_panel(..., (unsigned)sheng_console_rows, ...);
retained_log.h:160  (rows < 6 || rows > 24) -> return;                  // 40 > 24，整函数提前返回
retained_log.h:186  sheng_panel_ready = 1;                             // 永远执行不到
```

`sheng_panel_ready` 恒为 0 之后：

- `sheng_log_putc()` 里 `if (!sheng_panel_ready) return;` ⇒ 所有控制台字符被静默丢弃；
- `sheng_boot_render()` 第一句也是 `if (!sheng_panel_ready) return;` ⇒ 进度条不绘制；
- 唯一不受影响的只有 `sheng_status_render()` 直写的顶部两行状态栏 ⇒ 屏上只剩
  `NABU - BOOT LOG` / `NABU: PE_lockdown_iokit`。

内核本身仍在正常前进（AMFI、AppleImage4、MAC Framework、BSD buffer cache 都执行了），
只是前台的控制台驱动被这句陈旧上限拦在门外。

### 11.2 修复

`retained_log.h` 的两处守卫改为纯荒谬值过滤，行数策略只保留在 `sheng_vm_trace()` 一处：

| 守卫 | 旧 | 新 | 理由 |
| --- | --- | --- | --- |
| 控制台面板 `rows` | `> 24` | `> 64` | 策略上限是 `vm_trace.h` 的 40，守卫不该编码策略 |
| panic 面板 `rows` | `> 16` | `> 32` | `panic_rows` 由 `vm_trace` 钳到 16，同上 |

提交 `2c37b7e`（`log: let the console bind accept the widened 40-row panel`）。

### 11.3 契约表（改动这类参数前必须整条走一遍）

| 量 | 产生处 | 传递 | 最终守卫 | 当前值 |
| --- | --- | --- | --- | --- |
| 可用行数 | `vm_trace.h:65-73` | `sheng_console_rows` → `arm_init.c:397` | `retained_log.h:160`（≤64） | 40 |
| panic 行数 | `vm_trace.h:67` | `sheng_console_panic_rows` → `arm_init.c:400` | `retained_log.h:143`（≤32） | 16 |
| 列数 | `vm_trace.h:63-64` | `sheng_console_columns` | `retained_log.h`（32..100） | 88 |
| 像素边 | `vm_trace.h:74-76` | — | 自身 `top + pixels <= v_height` | 2464 ≤ 2560 |

教训：这次改的是"显示策略"，断的是"显示通道"。凡是把某个量交给另一个函数，就要顺着
被调用方的入参校验一路查到 return 分支，不能只改产生处。

### 11.4 下一个观察点

控制台恢复后，下一次开机就会给出真实停机点（40 行视野，足以覆盖 `PE_lockdown_iokit`
之后的 `bsd_init` 阶段输出）。届时再看 `NABU SOURCE V2 ROOT_SEARCH` 是否出现，
才是判断"是否进入 `IOFindBSDRoot`"的合法证据。

## 12. 第二次上机：守卫修复后的真实画面（2026-11-09 22:10）

### 12.1 刷入内容

| 文件 | md5 | 说明 |
| --- | --- | --- |
| `Payloads/kernel.macho` | `0a1fd1e408e8c3eddd6881cd4ba45a22` | run `42630470033`（`2c37b7e`，含 bind 守卫修复），112,390,144 B |
| `unixnu.toml` | `afd072f7c34d133ebc28d52ea2aaf787` | 561 B，`cpus=1` + `initrd` + `rd=md0`（pair B） |
| `Payloads/kernel.macho.adt` | `96c93b78ed7cf31c4bc27e4603548dc0` | 11,876 B，含 `RAMDisk`（与 initrd 成对） |

### 12.2 屏上实录（约 33 行，面板已按 40 行绘制）

```
NABU - BOOT LOG
NABU: PE_lockdown_iokit
ZZ rs native-gop
ZZ sm native-gop
ZZ rs ArmDTPlatform
ZZ sm ArmDTPlatform
ZZ at CoreAnalyticsHub ×2
virtual bool CoreAnalyticsHub::start(IOService *)::150:CoreAnalyticsHub start
ZZ rs CoreAnalyticsHub
ZZ sm CoreAnalyticsHub
Registered CoreAnalyticsHub Functions with xnu.
virtual bool CoreAnalyticsHub::start(IOService *)::229:CoreAnalyticsHub start com…d
ZZ at ArwCpu ×2
Copyright (c) 1982, 1986, 1989, 1991, 1993 The Regents of the University of California…
MAC Framework successfully initialized
using 890 buffer headers and 890 cluster IO buffer headers
ZZ at ArwCpu ×2
ZZ at AppleImage4 ×2
ZZ at ArwCpu ×7
IOServicePM::kIOMessageSystemCapabilityChange: will 0xf->0xf
IOServicePM::systemPowerChange to 0x30
AMFI: developer mode is force enabled on this platform
IOServicePM::kIOMessageSystemCapabilityChange: did 0xf->0xf
ZZ rs IOCPUInterruptController
IOCPUInterruptController: not registry member at registerService()
ZZ rs ArwCpu
ZZ sm ArwCpu
BOOT [###-------] - IOKit entry
```

### 12.3 结论

1. **守卫修复有效**：控制台文本与进度条都恢复了，面板按 40 行绘制（本屏 33 行）。
2. **`NABU SOURCE V2 ROOT_SEARCH` 缺失这次是合法证据** —— 控制台在工作，注释掉它不再是原因。
   结论：内核尚未进入 `IOFindBSDRoot()`，`IOKitBSDInit.cpp:715` 那一行还没有机会打印。
   这也再次确认外部报告"`IOFindBSDRoot()` 死循环=当前真因"不成立。
3. 已确认走到的位置：`MAC Framework successfully initialized` + `using 890 buffer headers`
   （`bsd_init` 早期，`bufinit()` 的打印），以及 IOKit 匹配线程的 `ArwCpu` / `AppleImage4` /
   `CoreAnalyticsHub` / `IOCPUInterruptController` 输出。
4. 停滞区间：**`bufinit()` 之后、`setconf()` 之前**（`bsd_init.c:889` 的 `setconf()` →
   `IOFindBSDRoot()`）。中间候选：`bsd_autoconf()`、`loopattach()`、`net_init_run()`、
   `inittodr(0)`。
5. `bsd_init_kprintf()` 在 `bsd_init.c:504/506` 是条件编译的，所以"屏上没有 `bsd_init: …`"
   **不能**用来判断是否走到某处 —— 必须换成无条件打印。

### 12.4 已下的观测补丁

`platform/nabu/macos26/patches/zz-nabu-bsd-init-checkpoints.patch`（提交 `0f6a578`）把 8 处
`bsd_init_kprintf()` 改成可见的 `printf("NABU-CHK …")`：
`bsd_bufferinit`、`vfsinit`、`bsd_autoconf`、`loopattach`、`net_init_run`、`inittodr`、
`setconf`、`vfs_mountroot`。纯观测，不改行为；`--prepare-only` 已确认自动应用（staged 树 8/8）。
下次开机的判据：屏上最后一条 `NABU-CHK` 是哪个，卡点就在它后面那个调用里。
