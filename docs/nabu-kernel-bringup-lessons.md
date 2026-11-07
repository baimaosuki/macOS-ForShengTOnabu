# nabu 内核 bring-up 经验（硬教训）

本文记录 Snapdragon 860（SM8150-AC / Kryo 485，ARMv8.2-A，无 FEAT_SHA3）上把
macOS 26 内核从「黑屏」推进到「控制台 + SMP + 调度器循环」过程中固化下来的规则。
每条都附证据来源和错误做法，避免重复踩。症状复现方法见[日志与崩溃诊断](debugging.md)，
当前边界见[移植状态](status.md)。

## 一句话结论

**内核跑不起来时，先取屏幕寄存器快照，把 ELR 符号化并反汇编那一条指令，再回源码查契约。**
不要先猜、不要先加探针、不要先在二进制里试补丁。

## 证据链优先级

| 优先级 | 来源 | 说明 |
| --- | --- | --- |
| 1 | 屏幕寄存器快照 | `XNU/ELR/ESR/FAR/SPSR/SCTLR/TCR/TTBR0/TTBR1/MAIR` + STAGES 带，异常现场唯一可信来源 |
| 2 | 反汇编 ELR 附近 ±64 字节 | 一条指令就能区分「硬件未定义」「断言陷阱」「访存错」 |
| 3 | 内核源码契约 | `SecureDTGetProperty` 调用点、`panic()` 文案、`assert()` 条件 |
| 4 | QEMU 内存 dump | panic 文本在 `_sheng_log_buffer` 里可原样读出 |
| 5 | 构建日志 | 只在编译期问题上有意义 |

**不要用**「屏幕没变化」当作「没有推进」：字符 writer 忙时会跳帧，但 STAGES 带和寄存器不会骗人。

## ESR 解码速查

| ESR | EC | 含义 | 典型原因 |
| --- | --- | --- | --- |
| `0x20000000` | `0x00` | 未定义指令 | 访问本核不存在的系统寄存器、缺少指令集扩展 |
| `0xF200BFFC` | `0x3C` | BRK | XNU 的 `assert()` / `__builtin_trap()` 冷路径，ISS 就是立即数 |

`FAR == ELR == PC` 且 `TTBR1_EL1 == 0` 的组合通常意味着**更早的一次故障把上下文冲掉了**，
先把前一次异常修掉再看，不要顺着被污染的现场查。

## 规则一：Apple 私有系统寄存器必须二进制穷举，不能逐点打补丁

Kryo 485 没有 Apple 的 `S3_1_C15_*`（PMCR0/PMESR/PMCR2..）、`S3_2_C15_C*`（PMCn/PMEFR）、
`S3_5_C15_C2_6`、`S3_7_C15_C6_4`（UPMCR）等实现定义寄存器，**任何一条都会触发 ESR EC=0 的未定义指令异常**。

实测分布（同一个内核镜像内）：

| 函数 | 命中数 |
| --- | --- |
| `_cpc_hw_read_regs` | 10 |
| `_cpc_machine_regs_apply` | 9 |
| `_cpc_machine_regs_reset` | 8 |
| `_kpc_get_rawpmu_config` | 7 |
| `_cpc_fiq` | 3 |
| `_kpc_get_configurable_config` | 3 |
| `_awl_pm_state_change_cbk` | 2 |
| `_cpc_cpmu_pmesr_write_events_internal` | 2 |
| `_awl_mark_hv_entry.cold.1` | 2 |
| 合计 | **46** |

**错误做法**：按源码里 `__builtin_arm_wsr64("S3_2_C15_C%d_C0_0", …)` 这类模板去写 C 宏补丁。
第一版只覆盖了带 `%d` 的写入点，漏掉 `_cpc_cpmu_regs_zero_pmcs()` 里 8 条**字面立即数**
写法，于是故障从 `0x75B231C` 前移到 `0x75B2760`，白刷一次机。

**正确做法**：链接后用 `scripts/nabu_neutralize_apple_sysregs.py` 扫全镜像：

- `mrs Xd, S3_*_C15_*` → `mov Xd, #0`（该特性按「不存在」读取）
- `msr S3_*_C15_*, Xn` → `nop`（没有可编程的东西）
- 架构定义空间（PMU `S3_3_C9_*`、cache ID `S3_x_C0_*`）**保持原样**，这些本核确实有

该步骤已接入 `scripts/build_macos26_xnu.py` 的链接后阶段（密封之前），CI 产物天生干净。
判定标准只有一条：**复扫必须为 0**。

## 规则二：设备树是契约，不是配置

内核对 ADT 的要求是硬断言，缺一项就 `brk`，不会降级：

| 断点 | 源码位置 | 要求 |
| --- | --- | --- |
| `panic: Expected 256 seed bytes from bootloader, but got 64` | `prng_random.c` | `/chosen/random-seed` 必须 **256 字节**（arm64 是 `4 * SHA512_DIGEST_LENGTH`） |
| `panic: Unexpected /chosen/memory-map/TrustCache property size` | `arm_vm_init.c` | 属性存在时必须是 `sizeof(DTMemoryMapRange)` = 16 字节 |
| `assert(err == kSuccess)` → `_arm_vm_prot_init.cold.3` 的 `brk #0xbffc` | `arm_vm_init.c` | `/chosen/memory-map` **节点本身必须存在**；属性缺失有 `err` 兜底，节点缺失没有 |

**教训**：`secure DT lookup` 的 `assert` 比属性检查更致命 —— 先保证节点存在，再谈属性内容。
修改 ADT 后必须跑 `scripts/audit_adt.py`，它把内核里每一条硬编码期望都写成了断言。

就地修改已有 ADT 时，**属性长度字段（值前面 4 字节）也要同步改**，否则解析器按旧长度继续读，
后续所有 key 全部错位（表现为 `UnicodeDecodeError: can't decode byte 0x96`）。

## 规则三：不要自己写汇编探针，用现有五阶段日志

曾经的 `Lsheng_early_repaint` 探针没有正确返回，把 `x21` 覆盖成非零，触发 `start.s` 里
`cbz x21` 的暖启动分支误判，`TTBR1_EL1` 被写成 0，屏幕只剩一期品红。
`x19..x30`/`sp` 在早期汇编里不是自由寄存器，**任何自研探针都不许碰**。

可用的既有设施（SHENG 五阶段，宏内联、只用 `x9/x10` 暂存）：

| 阶段 | 颜色 | 归属 |
| --- | --- | --- |
| `bootstrap_trace` | 绿 `0x34D399` | 引导汇编 |
| `c_trace` | 青 `0x38BDF8` | C 运行时 |
| `vm_trace` | 品红 `0xF472B6` | `arm_vm_init` |

MMU 打开之后**禁止再碰 MMIO**，里程碑只写 `.data` 环形区；快照 `{fb_base,row_bytes,width,height}` 只取一次。

## 规则四：编译期互斥，不要早 return

在函数体里插入 `return;` 来跳过 Apple 专属逻辑，会在 `-Werror,-Wunreachable-code` 下直接构建失败
（`cpc_arm64.c` 的 PMCR 写函数就是这么炸的）。正确写法是编译期二选一：

```c
#if defined(ARM64_BOARD_CONFIG_NABU)
	/* nabu 路径 */
#else
	/* Apple 路径，原样保留 */
#endif
```

同时注意 `-Werror,-Wcast-qual`：调试打印里把 `const` 指针转成 `void *` 会直接失败，
要么去掉指针参数，要么全程 `const`。

## 规则五：clang 的 feature 开关不是 GCC 那套

`-march=armv8.2-a+nosha3+nosha512+nosm4` **不被 Apple clang 接受**（`unsupported argument`），
构建会停在编译参数上，看起来像别的问题。关闭 FEAT_SHA3 的正确写法：

```
-Xclang -target-feature -Xclang -sha3 -Xclang -target-feature -Xclang -sm4
```

A76 只有 AES/SHA1/SHA2/CRC。Apple 默认代码生成会直接吐 `bcax`/`eor3`/`rax1`，
在 `_qrcodegen_encodeSegmentsAdvanced+0xb34` 上炸掉整条启动链。

## 规则六：`fastboot boot` 报 Load Error 先看槽位

`Failed to load/authenticate boot image: Load Error` 不是镜像坏了，通常是把另一槽的 vbmeta
拿去校验这个 boot 镜像。`fastboot --set-active=a`（切到与镜像匹配的槽）后立刻恢复 `OKAY`。
不要因为这个错误去重刷 boot 分区。

## 规则七：QEMU 取证用相对文件名

HMP `pmemsave` 拒绝绝对路径（`invalid char 't' in expression`），给相对文件名，
文件落在 QEMU 的 cwd。panic 文本可从 `_sheng_log_buffer` 原样 dump，比猜有用得多。

`gdb-stop.py` 抓不到 `_panic` 断点，panic 之后要靠内存 dump 或屏幕取证。

## 刷机与验证清单

每次改动后固定走这一套，缺一步就会把「没生效」误判成「没修好」：

1. 本地或 CI 产出内核 → 记录 md5
2. `fastboot boot recovery-win.img`（失败先查槽位）→ 等 `adb devices`
3. `adb push` 到 `/esp/EFI/UniXNU/Payloads/kernel.macho`
4. `adb shell md5sum` 核对，与第 1 步一致再 `sync`
5. ADT 有改动则同法核对 `kernel.macho.adt`，并先跑 `audit_adt.py`
6. `adb reboot`，拍屏幕，读 `ELR/ESR/FAR` 和 STAGES 带
7. 记录：哪条指令、哪个符号偏移、源码哪一行

## 当前边界

- 内核已过：引导汇编 → MMU → `arm_vm_init` → 控制台 → SMP 8 核 → `bsd_init` 前半段
  （NVRAM、swap、lockdown、amfi_policy、vmguard、dtrace 全绿）
- 卡在 `trust_cache_init`：`img4if`/`amfi` 只能由 `AppleImage4` 与 `AMFI` kext 注册，
  而当前镜像是 **0 个预链接 kext**
- 因此真正的下一关是 prelink stage 2（指针重绑定），不是继续在源码里放行断言
