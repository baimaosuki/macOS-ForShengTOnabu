# NABU A640 / macOS 27.3 移植记录

目标是 26C5035r、XNU12377.121.6、arm64 + Zeta PAC。A640 后端与 A740 的寄存器、GMU/HFI、ZAP/SQE 固件及电源表分离保存。

`devices/nabu/package/gpu` 已接入编译，普通启动不启用 GPU。`nabu-gpu=1` 在 IOBSD 后执行真实上电、SMMU、GMU/HFI、CP scratch 与 fence 验证；只有真实读回才报告成功。不确定 DMA 停止时保留资源。

GPU SMMU 为 0x02ca0000，与 USB/UFS 的 0x15000000 分离。GPU 非安全流使用 CB0，GMU kernel SID5/0x405 使用 CB1；原厂 gmu_user SID4 并非这条 kernel 流。

已有 Host 证据：31 个 GPU 目标文件及实际日志 accessor 的 SDK 编译/链接、真实 ARM64 ring 代码执行、SMMU ASan/UBSan 故障测试、原厂 DTB 与生产 ADT 比对。没有真机 GPU 完成或帧率结论。

M2V 46e7cc96 的 0063/0064 补丁已应用。Mesa 保持用户指定的 c823a83；主线另一个编译清单仍为 85fda3b，其补丁不能直接应用，必须移植。A640 wire ABI v1 与当前客户端 v2 尚需适配，不能直接混用标志位和方法号。

Metal bundle/addon 尚未启用。无 task/可分页 BO、macOS accelerator ABI、shader cache 预热及 GPU scanout 仍需分别实现和验证。完整源码来源与当前阶段见 package/gpu/sources.lock.json、PROVENANCE 和 gpu-port27.json。

可选 GPU 性能记录器改为首次请求时分配 4096 条采样与任务记录，容量保持不变。
移除 1,163,264 字节静态 BSS，避免原生 DATA 段越过 26C5035r 保留 kext 边界。
Host ASan/UBSan 覆盖两次分配分别失败、并发启动及采样/任务生命周期。

Mesa c823a83 的 24 项 Darwin 补丁已移植并可完整反向还原原始输入。
本地 SDK 26.5 已完成普通 arm64 Turnip、M2V 静态库和 macOS Metal 插件编译链接；
最终插件和 Turnip 代码扫描未见 PAC 指令。这些是交叉构建证据，尚无真机 GPU 运行证据。
独立 gpu-userspace CI 构建签名产物，不自动安装；v1/v2 内核协议适配和 GPU 扫描输出仍待完成。

Clang 显式设置 `-mcpu=generic -march=armv8.2-a+crypto`。Darwin 的默认 Apple CPU
会让返回地址诊断产生 XPACI 寄存器指令；generic 改为无 PAuth CPU 安全忽略的 HINT #7。
最终代码检查同时拒绝 XPACI/XPACD 与鉴权分支；CI 增加真实编译器探针。

用户态 CI 44614135059（19f8c3b）已通过 SDK 26.5 编译、4 KiB 签名与严格签名检查。
最终扫描 6,873,990 条代码指令，未发现不支持的 PAuth。独立 addon 没有发布标记，
未安装、未并入 ESP；Vulkan 前端运行测试、内核协议适配及真机执行仍待完成。

用户态 CI 同时缓存 Cargo 目标文件和完整 Rust bridge 静态库。成品复用绑定
bridge/translator 输入、普通指针 ABI、CPU 特性、LTO 参数及编译器版本，之后仍由
上游构建器核对源码身份与静态库哈希。缓存缺失、损坏或不完整时回到 Cargo。
Host 测试覆盖精确复用和不兼容输入拒绝，不代表 Vulkan 前端或真机已经验证。


P026 集成代码：A640 wire v2（Submit 3104 字节，128 IB，17/18 为优先级/Note）；
无 task 可分页 WC BO，缓存 IOSurface 保留 CPU 属性并执行显式缓存维护，GPU PTE 为 NC。
发布层使用 26C5035r 的 IOAccelerator，GOP 交接按连接/task 校验并在关闭时清理。
fb-gpu 模式不执行继承的 SM8550 DPU 寄存器路径。单独 GPU 菜单项启用链路；
同一内核按启动参数选择 signed-text 规则，CPU 使用原 10 条，GPU 使用 6 条，
避免 CPU 的 renderer=nil / no-Metal 补丁禁用 GPU。签名用户态复用既有 CI 产物。
这些是源码与 Host 证据；完整内核 CI 和最终包结果另行记录，真机桌面尚未验证。

审计修复：以签名产物为准，19f8c3b 的 Shader Cache 工具和 LaunchAgent
均使用 `/Applications/Shader Cache.app`。设备内核仅将该应用前缀写入
Data 对应的 `/Applications`，保留二进制、签名资源与其他 addon 路径。
WindowServer 与 backboardd 统一处理导出和诊断；Submit 能力报告内核
缓存维护，legacy/V2/async 提交统一更新前后台优先级。原交付内核 CI
44630035429 已通过，本轮修正已由内核 CI 44640851880（49d151a）和最终打包校验通过；仍未验证真机 GPU 桌面。

GPU 启动移到真实 IOBSD 发布后的设备 BSD late hook，不再使用静态
STARTUP 注册。硬件线程在分配 A640 状态或访问寄存器前等待本轮日志
writer=2 与固定快照落盘（最多 120 秒）；后续检查点保留 5 秒上限，
容忍暂时提交竞争。原 EARLY_BOOT 记录值为 18，在 task/thread 初始化
之后；arm_init 中的 4 是 KPRINTF。照片中的 MMU 前色条不足以证明
具体故障点，本轮修正的真机效果仍需日志验证。

P028 内核 CI 44694522097（8b122cc）及最终内核启动顺序、PAC、
签名页、显示、日志回放通过；FAT 读回和 ZIP CRC 通过。仍待 nabu
真机确认能否跨过原色条卡点以及 A640 是否真正完成初始化与渲染。

## P025 真机与生产模式修复

P025 真机日志已确认 GMU/HFI、PAS13 认证、CP scratch `6401cafe` 和 fence
完成。WindowServer 随后在 `WSSystemCanCompositeWithMetal` 等待 30 秒后退出。
交付 addon 漏了 `GPUBundles/NativeAdrenoMetal.publish`，插件因此进入诊断路径并
返回 nil。生产 ESP 整合现已补齐此可读旁置文件，不改签名 bundle。
Host 回归执行了插件实际的模式选择函数，确认原有签名文件、trustcache 和 CPU
入口保留。同轮后续 `CP_OPCODE_ERROR` 尚未定位到具体命令，GPU 桌面仍需真机验证。

GPU 启动项还需 `nabu-gpu-scanout=1`，使签名 presenter 能将完整的固件 GOP
显存导入为 no-copy Metal buffer；缺少时会走普通 RAM 属性查询并拒绝导入。
导入仍限制为当前 GOP 完整连续可写物理范围，不允许任意设备内存。

未获队列范围的客户端失败清理不再调用 owner 0 的取消操作，避免误取消
WindowServer 的未完成任务。回归用旧生产函数复现错误，修正后通过。
真实 ARM64 ring 回放扩展到 400 次带 IB 的提交、包校验、上下文前导和一次环回绕；
该 Host 测试不执行固件或着色器。首个 CP 故障按原厂 SQE selector 抓取 opcode，
并保留有效位与 live fence，避免后续空 IRQ 覆盖；P025 具体坏命令仍待真机定位。


P026 hardware follow-up (2026-11-20): the installed publish marker is present,
and the production source identity matches P030 despite the unchanged logger
build ID. Scratch/fence 1 succeeds, but the first framed IB submission fails
with CP_OPCODE_ERROR (opcode register 0, RPTR=WPTR=51, submitted/completed=2/1).
WindowServer subsequently aborts in WSSystemCanCompositeWithMetal; no rendered
GPU desktop is established. This log has no ring/IB snapshot and cannot identify
the actual offending instruction from opcode 0 alone.

Legacy BO view reuse now preserves the same WC/WB map options as the first
mapping. The previous cached-view branch returned default cache flags, allowing
a second mapping to disagree with its allocation on this NC GPU path. Host
regression executes the production method and reproduces the prior failure;
this does not prove it caused the P026 first-submission fault. The production
resource-submit path now retains a bounded first-IB head, and a CP failure prints
only the latest ring/IB snapshot once, including read status and captured length.
No arbitrary physical alias or per-frame pixel scan is added.


P027 hardware evidence confirms the P031 USB correction: the apps SMMU uses
USB slot4/CB55, verifies UFS slot2/CB1 unchanged, and native AppleUSBXHCI starts
and creates ports. External keyboard/mouse enumeration is not yet demonstrated.
GPU source64496146 still faults at fence2/1. The complete 26-word ring is
well-formed; the 28 captured IB words match Turnip's A6xx initialization prefix.
Only 28 of its 182 words were captured, so the failing instruction is unknown.
The local A630 SQE disassembly confirms handlers for the captured PM4 opcodes;
this is static firmware evidence, not execution or memory-visibility proof.

Legacy owned-BO CPU views now retain the prepared IOBufferMemoryDescriptor
itself, preserving the VM-object/cache/lifetime relationship used by the V2
mapping path. Physical-range descriptors remain only for borrowed/imported
backing without an owned descriptor. Host tests cover original-object identity,
WC/WB mapping reuse, borrowed fallback and failed-publication reference cleanup;
the previous production function fails the object-identity regression. Whether
this resolves the P027 CP failure remains a hardware question. Fault records now
include the A6xx IB1/IB2 base/remaining registers and up to 256 IB words. Remaining
counts exclude prefetched words and are not labeled as an exact faulting PC.


P030 真机已运行 a9f3265：bootstrap fence1 完成，生产 fence2 在首个 IB 失败。
提交前快照的前448字节（7条64B缓存行）为零，words144..165 也为零；
此前只在页属性变化时同步缓存，changed=0 时遗漏了实际写入发布。
现在 owned WC 和 cached imports 都按 job 的实际 IB/resource 字节区间执行
CIVAC+DSB SY，完成后 IVAC；legacy、V2 和直接提交共用发布函数，多区间
同 handle 不再被引用去重逻辑漏掉。V2 CPU 映射显式指定 WC/WB。
首批用户 BO/IB 独立限量记录 MAIR、真实 kernel/physmap PTE 的 ATTRINDX/SH
以及 clean 前后数据。用户页表不作无锁遍历；V2 地址仅记录，实际采样 physmap。
这些修复不能从旧日志证明哪一条用户 PTE 产生了脏行，真机结果仍需验证。


P031 在首个 GMU BO 的 cacheSnapshot 提前 Panic：手工 gVirtBase 线性换算
绕过 XNU ptov_table，访问了静态映射空洞。现已同时修正诊断和缓存维护：
BO/页表读取调用 phystokv，CIVAC/IVAC 按 phystokv_range 返回边界分段。
推送前核对现有内核 LC_SYMTAB 的全局符号与 XNU 源码签名，Host/ARM64
回放使用非连续 physmap 和 P031 页表地址。上机仍需先通过 BO/PTE，再验证
fence2 和提交前后数据；P031 尚未运行到原来的 CP_OPCODE_ERROR 路径。
