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
   —— 该文件是 **TWRP 临时 recovery**，不是本项目构建的镜像，也不是 ESP 备份；
   它的唯一作用是把 `/esp` 挂起来做 `adb push`/`md5sum`
3. `adb push` 到 `/esp/EFI/UniXNU/Payloads/kernel.macho`
4. `adb shell md5sum` 核对，与第 1 步一致再 `sync`
5. ADT 有改动则同法核对 `kernel.macho.adt`，并先跑 `audit_adt.py`
6. `adb reboot`，拍屏幕，读 `ELR/ESR/FAR` 和 STAGES 带
7. 记录：哪条指令、哪个符号偏移、源码哪一行

## 当前边界（2026-11-09 修订）

> 下面这段是 **2026-11-07 的判断，已被推翻**，保留作为历史记录。
> 线性现状见 [`docs/nabu-20261109-status.md`](nabu-20261109-status.md)。

~~内核已过：引导汇编 → MMU → `arm_vm_init` → 控制台 → SMP 8 核 → `bsd_init` 前半段~~
~~卡在 `trust_cache_init`：… 当前镜像是 0 个预链接 kext~~
~~因此真正的下一关是 prelink stage 2（指针重绑定）~~

三点更正：

1. **stage 2（指针重绑定）已经做完**，不再是待办：拷贝区间内 26,504 个指针槽，
   其中 13,358 个指向集合外（12,940 → `__TEXT_EXEC`，336 → `__DATA`，82 → `__DATA_CONST`）
   已按常量 slide `0x2000000` 重绑；跨模块 `BL` 重编码 19,860 处；`ADRP` 保持 0 处修改。
2. **当前卡点不是 `trust_cache_init`**，而是更早的 **stage 2 `IOKit entry`**：屏显启动条
   `BOOT [###-------]`（mask = `1<<0 | 1<<1 | 1<<2`），且是死等而非 panic，所以不出现二维码。
3. **"点不亮"的两个真因是引导器契约，不是内核源码**：`unixnu.toml` 缺根级
   `[[driver]]`（→ `Config->DriverCount == 0` → `xnu: no loader protocol installed` →
   `EFI_UNSUPPORTED`，内核一个字节都没被读），以及 ADT 缺 `/chosen/memory-map` 的
   16 字节 `RAMDisk` 属性。两条都写进了 `docs/nabu-20261109-status.md` §3。
4. **"SMP 8 核"这条也待复验**：ADT 的 `/cpus` 只有 1 个 CPU 节点（`/cpus/cpu@0`），
   而 toml 的 `cpus=8` 尚未收敛，已按单变量在做 `cpus=1` 试验。

## 规则八：ARMv8.2 缺失的加密指令必须在同步异常里就地仿真，不能等它 panic

**现场**。Kryo 485 是 ARMv8.2-A：没有 FEAT_SHA3、FEAT_SHA512、FEAT_SM4、FEAT_SM3、FEAT_PAuth。
而 Apple 的 arm64e 用户态二进制（libcorecrypto、FairPlayIOKit、AppleImage4、CoreTrust、
AppleAVD）里直接编进了 `eor3`/`bcax`/`rax1`/`xar` 和 `sha512h`/`sha512h2`/`sha512su0`/`sha512su1`。
这些码字全部落在 bits[31:24]=0xCE 的 "three same crypto" 编码区，在缺少对应 feature 的核上
就是未定义指令，硬件只回一个 `ESR_EL1.EC = 0x00`（EC 不分类），ISS 全零。

**落点（改动前）**。`osfmk/arm64/sleh.c`：`fleh_synchronous` → `sleh_synchronous()` 里
`case ESR_EC_UNCATEGORIZED:` 在 `:1001`（紧跟 `assert(!ESR_ISS(esr))`，EC=0 时 ISS 恒为 0，
所以这句断言不碍事），`handle_uncategorized(&context->ss)` 在 `:1013`；
`handle_uncategorized()` 定义在 `:1185`，它先 `COPYIN` 出错指令字，内核态直接
`panic("Undefined kernel instruction: pc=%p instr=%x", …)`（`:1253`），用户态走
`exception_triage(EXC_BAD_INSTRUCTION)`（`:1268`）。另外 `sleh_synchronous_sp1()` 的同一个 case
在 `:675`：那条路径是 `bl sleh_synchronous_sp1; b .`，在里面 return 会原地死循环，所以**故意不动**。

**做法**。在 `:1013` 之前插一行 `if (nabu_v82_emul_sync_exception(context, esr)) { break; }`，
仿真器本体放新文件 `osfmk/arm64/nabu_v82_emul.c`（整文件 `#if defined(ARM64_BOARD_CONFIG_NABU)`
互斥，`conf/files.arm64` 加一行）。流程：从 ELR 取 32 位指令字 → 精确 mask 解码 → 按 Arm ARM
伪码算 128 位结果 → 写回 trap frame 里的向量寄存器 → ELR += 4 → 正常 ERET 回被打断的代码。
无分配、无锁、无 printf（只有一行限速的"未实现指令"日志）。

**为什么写 trap frame 而不是读活寄存器**：`SPILL_REGISTERS`（`exception_asm.h:170`）在两条同步
向量上无条件把 q0..q31 + FPSR/FPCR 存进 `arm_context_t`，返回路径
`Lexception_return_restore_registers`（`locore.s:1951`，重装 q0..q31 在 `:1999`）对内核返回和
用户返回都从同一帧重装。也就是说"活"的向量寄存器在 ERET 那一刻一定被这个帧覆盖，仿真结果只有
写进帧里才算数；另一个好处是完全不碰被打断线程的活寄存器，不会被编译器/spill 顺序坑到。
GPR 只在 PACGA/日志需要时读，写 GPR 时 reg29/30 要单独走 `saved_state64()->fp` / `->lr`
（`x[29]` 不是通用槽位，`set_saved_state_reg()` 会写错位置）。

**指令表**（mask/base，全部用 llvm-mc 18 编码-反汇编双向核对，SM3/SM4 另用 capstone 5.0.7 与
llvm-mc `-mattr=+sm4` 核对）：

| 指令 | mask | base | 处理 |
| --- | --- | --- | --- |
| `eor3 Vd.16B, Vn.16B, Vm.16B, Va.16B` | `0xffe08000` | `0xce000000` | 实现（Ra=bits[14:10]） |
| `bcax Vd.16B, Vn.16B, Vm.16B, Va.16B` | `0xffe08000` | `0xce200000` | 实现 |
| `rax1 Vd.2D, Vn.2D, Vm.2D` | `0xffe0fc00` | `0xce608c00` | 实现 |
| `xar Vd.2D, Vn.2D, Vm.2D, #imm6` | `0xffe00000` | `0xce800000` | 实现（imm6=bits[15:10]） |
| `sha512h Qd, Qn, Vm.2D` | `0xffe0fc00` | `0xce608000` | 实现 |
| `sha512h2 Qd, Qn, Vm.2D` | `0xffe0fc00` | `0xce608400` | 实现 |
| `sha512su0 Vd.2D, Vn.2D` | `0xffe0fc00` | `0xcec08000` | 实现 |
| `sha512su1 Vd.2D, Vn.2D, Vm.2D` | `0xffe0fc00` | `0xce608800` | 实现 |
| `sm4e Vd.4S, Vn.4S` | `0xfffffc00` | `0xcec08400` | 解码+限速日志，落回 fatal |
| `sm4ekey Vd.4S, Vn.4S, Vm.4S` | `0xffe0fc00` | `0xce60c800` | 同上 |
| `sm3ss1 Vd.4S, Vn.4S, Vm.4S, Va.4S` | `0xffe08000` | `0xce400000` | 同上 |
| `sm3tt1a/1b/2a/2b Vd.4S, Vn.4S, Vm.S[i]` | `0xffe0c000` | `0xce408000` | 同上（`(insn>>10)&3` 选变体） |
| `sm3partw1/2 Vd.4S, Vn.4S, Vm.4S` | `0xffe0fc00` | `0xce60c000` / `0xce60c400` | 同上 |
| 其余 0xCE 码字 | `0xff000000 == 0xce000000` | — | 记为 crypto-3same，落回 fatal |

**实现 vs 记录后 fatal 的取舍**：8 条全部按 Arm ARM 伪码实现（包括 SHA-512 的两次串联语义），
SM3/SM4 只解码不实现。理由是实测分布：核心 `com.apple.kernel` 里 0 条 SM3/SM4，全部宿主二进制
里只有 2 条 sm4ekey 字形；而 SHA-512 在 corecrypto 里 40/40/32/32 条，不实现等于开机第一次
SHA-512 就 panic。SM4 的 S 盒手搓一遍不划算，命中时打一行
`nabu: unimplemented v8.2 crypto insn …`（含 mnemonic/instr/pc/esr/模式）再走原来的
`handle_uncategorized` → panic，至少现场能直接看出是哪条指令。

**PAC 保险，以及为什么它不是主线**。先量了一遍：用 capstone 把 `kernelcache.release.mac13g.nabu.kc`
的代码段全解码（1773 万条指令），PAC 系只有栈指针形式——`pacibsp` 140111 条、`autibsp` 33944 条、
`paciasp` 3 条，分布在 344 个 bundle（`com.apple.kernel` 自己 21508 条）；寄存器形式
（`pacia`/`autia`/`*z`/`xpaci`）**0 条**，内存/分支形式（`ldraa`/`ldrab`/`braa`/`blraa`/`retaa`）
也**0 条**。

关键在于 `paciasp`/`pacibsp` 编在 HINT 空间（`paciasp`=0xD503233F = `hint #25`，
`pacibsp`=0xD503237F = `hint #27`）：基架构规定 HINT 是 NOP，缺 FEAT_PAuth 的核不会把它当未定义
指令，所以这 14 万条一律当 NOP 跑过去了，根本不会进这个钩子——这也是为什么现有 bring-up 能跑进
kext 代码而没有满屏 EC=0。

钩子里兜的是 `0xDAC1xxxx` 这段寄存器形式（这才是"没有 FEAT_PAuth 就 UNDEFINED"的那一段）：
mask `0xffff8000`、base `0xdac10000`、`opc=(insn>>10)&0x1f <= 17`，覆盖
`0xDAC10000..0xDAC147FF`（0..7 双寄存器 PAC/AUT、8..15 单寄存器 *z 形式、16/17 XPACI/XPACD），
语义取恒等 `Xd = Xn`（*z 形式 `Xd = Xd`）；`pacga Xd, Xn, Xm` 单独用 `0xffe0fc00`/`0x9ac03000`
精确匹配（它和 UDIV/SDIV/LSLV/ASRV/RORV/CRC32 共用 0x9AC0xxxx，mask 松一点就会把除法当 PAC
处理），语义取 `Xd = Xn ^ Xm`。"自己签自己验"的成对使用在恒等语义下是自洽的，比每帧 panic 强。
内存/分支形式不在这里处理：`ldraa` 要真的做一次带偏移和写回的加载、`blraa` 要真的跳转，
不是"写回一个寄存器"能糊过去的，而且实测这个内核集合里一条都没有。

**开关与日志**。默认开启。`nabu-v82-emul=0` 或 `nabu-v82-emul-off` 关闭
（`PE_parse_boot_argn`，`STARTUP(TUNABLES, STARTUP_RANK_LAST, …)` 里打一行
`nabu: ARMv8.2 crypto emulation active (8 insns + PAC fallback, boot-arg nabu-v82-emul=0 to disable)`）。
关掉之后行为与打这个 patch 之前完全一致，方便二分定位。

**实测规模**（`scripts/nabu_scan_v82_crypto.py` 的结论，不必重测）：812 个 0xCE 字形分布在 33 个
bundle；按严格 mask 并用 capstone 复核后，550 条真实"缺 feature"指令落在 20 个 bundle，直方图：
`eor3`=191、`xar`=79、`bcax`=72、`sha512h`=40、`sha512h2`=40、`sha512su1`=33、`sha512su0`=32、
`sm3ss1`=29、`sm3tt1b`=14、`rax1`=10、`sm3tt2b`=4、`sm3tt2a`=2、`sm3tt1a`=2、`sm4ekey`=2；
剩下 262 个字是数据段误判。热点 bundle：corecrypto 291、FairPlayIOKit 192、AppleImage4 14、
CoreTrust 13、AppleAVD 9、com.apple.kernel 5、Libm 5。PAC 系另外按 mnemonic 统计（见下段），
不要和这 550 条混在一个直方图里。

**主机一致性验证**（`scripts/nabu_v82_emul_selftest.py`）。仿真器的解码与语义整块放在
`BEGIN/END NABU_V82_CORE` 注释之间，这段代码不依赖任何内核符号，所以可以原样切出来在主机上编译。
脚本从 patch（或已打补丁的源码树）里取出这段，用 `scripts/nabu_v82_emul_testgen.py` 的 Python
参考模型生成随机向量，再用 `scripts/nabu_v82_emul_test.c` 跑：每条指令 128 组随机用例，
另加 64+64 组 SHA-512 两轮串联用例（期望值用 FIPS 180-4 轮函数独立算出，参考实现本身先与
`hashlib.sha512` 对齐，因此不依赖伪码转写）。结果 1152/1152 通过，PAC 解码 21/21 通过；
编码再用 `llvm-mc-18 -mattr=+sha3` 反汇编回读，100/100 的 mnemonic 与寄存器字段一致。
这套测试不进 7 分钟 CI，改仿真器时本地跑一遍就够（`python3 scripts/nabu_v82_emul_selftest.py`）。

**别做的三件事**：不要重测那 550 个字形（数据已在上面）；不要动已有 patch——本文件是
`zz-` 前缀、排序最后，`patch -p1` 可重复应用；不要为了"省事"直接删掉
`handle_uncategorized` 里的 panic，那会把所有未定义指令（包括真的代码 bug）都变成静默跳过。
