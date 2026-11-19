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
