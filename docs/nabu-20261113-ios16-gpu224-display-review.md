# iOS16 on NABU：GPU224 包与软件桌面路径复核

复核日期：2026-11-13。结论：该项目有先运行软件桌面、后接入 A640 GPU 的历史路线。GPU224 是后续图形候选，不能倒过来当成首次点亮桌面的必要条件。

## 输入与版本

- 用户提供 `GPU224-Deployment-Kit-no-system.zip`，SHA-256 `08ebf971735d0295c1acc6eb3808081c37a0ab3161f0ae1102e5082d9198c3b8`。
- 用户提供 `GPU224-Dynamic-Storage-Update-20261112.zip`，SHA-256 `00ed338190f238ce570983791e3e0341e926c19e940b11c085a60ad95ba3fbc9`。
- 包内 `source-version.txt` 指向 [iOS 项目 a7148554](https://github.com/buhuo666/XIAOMI-PAD-5_nabu-on-IOS/tree/a7148554052f42159207245e2070b96492a046da)。按此提交取得有关源文件，未执行包内安装器或 Windows 程序。
- 依赖锁中的软件 framebuffer 基底为 [HakiOS 96ffbc53](https://github.com/oosexgang/hakios-xnu-kernel/blob/96ffbc532e970f3540885755d457e401e04d4707/xnu/files/iokit/Kernel/MiniIOMobileFramebuffer.cpp)。这是基底源码；GPU224 另有板级补丁，不能把基底逐字视为最终内核。
- 再次查询远端 main：UniXNU `2366e33e4934696424bb9f5aa7598df925fbc0c1`，UniXNU-sheng `cd73e23f0657c28a6638f165590d04f71ab76419`。主线指南在 UniXNU 的 `docs/`，sheng 仓库提供 `porting.toml` 和板级代码。

证据保存在工作区 `.work/ios16-gpu224-reference/`；最新 SHENG 指南与记录在 `.work/sheng-latest-20261113/`。`review-evidence.json` 记录提取文件摘要。

## 已实际拆出的内容

`bootstrap.apfs` 不是一个单独文件系统，而是顺序拼接的三个容器：

| 容器 | 字节数 | 与包内报告摘要一致 |
| --- | ---: | --- |
| Boot.apfs | 16,007,168 | 是 |
| NabuInit.apfs | 41,648,128 | 是 |
| App.apfs | 10,485,760 | 是 |

用系统已有 7-Zip 只读解析并提取 NabuInit，取得 454 个文件，包括实际 launchd plist、注入清单、Metal 工厂、前端、Turnip 和 CA timer 库。没有安装 apfs-fuse，也没有从设备拉取 OS 卷。

## 最初的软件桌面如何实现

1. **保留固件 framebuffer。** MiniIOMobileFramebuffer 使用 boot loader 留下的 framebuffer，不需要先重写面板初始化。板级补丁按实际 GOP 几何设置 1600×2560，并将物理尺寸和系统型号分开处理。
2. **软件渲染与显示服务配套。** 基底源码 `publishAccelerator()` 发布 IOAcceleratorES，但默认不设置 Metal/OpenGL 插件名。源码明确解释：客户端跳过这些后端，让 CoreAnimation 保留软件 renderer。这条行为属于 20F66，不能未经核对套到 macOS 25F71。
3. **刷新通知进入用户态。** framebuffer 有周期定时器及异步通知端口，不只是内核计数增长。iOS 项目的 `DISPLAY-CLOCK-BRINGUP.md` 进一步从原版 QuartzCore 核对 `CA_FORCE_TIMER=1` 的实际分支，并将它加在 backboardd 环境中。
4. **CPU 把图层写入实际 scanout。** 基底的 `present()` 保留各层 surface，presenter 线程消费请求，`show()`/`settle()` 遍历图层，`drawLayer()` 按源/目标矩形裁剪缩放并写 framebuffer。这里的 `blend()` 用于缩放滤波，不代表完整的任意 alpha 合成实现。
5. **首次画面接管文本控制台。** 首次可画 surface 到来时切换图形模式，然后继续处理帧与 surface 生命周期。

历史记录依据：iOS 项目同提交 `AGENTS.md:116` 记载第 161 轮桌面、Spotlight 键盘和输入已由用户确认，随后才开始 GPU；第 162 轮验证 CP 写入，第 166 轮验证 Vulkan。这是项目记录中的实机结果，本次没有重新启动设备复验。

## GPU224 在此基础上增加了什么

- backboardd 与 SpringBoard 都由 `nabu-metal-wait-exec` 包装启动。它最多等 180 秒，检查当前 boot 的插件挂载收据、AGXAccelerator 的 MetalPluginName/MetalPluginClassName 和 registry ID，然后 exec 原程序。
- 插件挂载助手保留原 AGX bundle，将 NativeAdrenoMetal 接入。前端使用 `libnabu-metal-frontend.dylib` → `libvulkan_freedreno.dylib` → A640 内核后端。
- `nabu-dpu=1` 选择后续的 NabuDpuDisplay：等待 producer 完成、映射 IOSurface、切换 DPU scanout，并依据实际刷新推进 retirement。它与早期 CPU 写 GOP 路径不同。
- 实际 backboardd plist 含 `CA_FORCE_TIMER=1`、`NABU_CA_FRAME_TRACE=1`、`NABU_CA_CONTEXT_TIMER=1`；实际 inject.conf 加载 `libnabu.ca-timer.dylib`。
- CA context timer 源码只针对 **20F66/backboardd**，检查 QuartzCore UUID，再替换两个指定调用槽；针对零时间 context commit 调用原 base timer，并保留 VSync keepalive。不能把这两个地址、库或环境键当作 macOS 通用修复。
- `bootstrap-report.json.current_candidate.device_executed=false`，context_timer 同样为 false。早期桌面成功与 GPU224 最新候选未实测可以同时成立。
- 动态容量更新增加 Data 布局、原生磁盘 Data 与日志安装支持，不是独立的显示修复。

## 对当前 macOS 黑屏的具体启发

| 项目 | iOS 路径 | 当前 NABU macOS 的证据与缺口 |
| --- | --- | --- |
| 软件 renderer | 不声明不可用的 Metal 后端，保留软件路径 | 三页 CPU compositor 补丁已应用；尚不能由此证明登录会话已生成持续的桌面画面 |
| 图层呈现 | show/settle 遍历图层并读取源/目标矩形 | 当前 program_hardware_gated 选最大匹配 surface 后复制一层；有 `mask=0x7` 的历史提交，其他层和矩形语义值得核对 |
| 刷新 | 定时器向客户端发送协议要求的回调，后期接硬件 VBlank | 日志中的 `vbl` 是 swap_begin_gated 软件调用计数，不能证明 WindowServer 收到并接受刷新通知 |
| 首帧 | CPU 写到固件 scanout 后交接 | 当前 BGRA 路径缺少像素内容统计；旧 shown=0 采样均早于那次迟到的 first swap，不能判断那帧最后没画出来 |
| 登录客户端 | SpringBoard/backboardd | 最新 macOS 日志仍有 OD 首次 connection 断言及后续查询延迟，不能把所有问题收敛成 ADM 名称 |

这是能力与证据差异，不是已证明的唯一黑屏根因。尤其 `mask=0x7` 只说明三个位设置，尚不证明三层都含桌面像素或都必须叠画。

## 与最新 SHENG 27.3 解法对照后的实施方向

1. **先闭合软件画面路径。** 保留 25F71 原生 IOMobileFramebufferLegacy/IOSurface ABI，核对实际 swap 的各层、源/目标矩形及更新/清除含义；实现必要的 CPU 图层呈现，而不是直接复制 iOS16 的 selector 和结构偏移。用已知非黑图案验证 GOP 写入与首次成功画面后再交出控制台。
2. **核对客户端收到的刷新和时钟。** iOS 的 CA_FORCE_TIMER 分支是可参考案例；macOS 应以自身 QuartzCore 和 TimeSync 协议为准。最新 [macOS 指南](https://github.com/UniXNU/UniXNU/blob/2366e33e4934696424bb9f5aa7598df925fbc0c1/docs/porting/macos.md) 明确 WindowServer 等待取自原生 Ethernet/802.11 地址的 TimeSync clock；当前 en0 已命名不能替代 clock ready。
3. **同时修复已发生的 OD 启动断言。** 最新日志 pid 68 的 connection != NULL 和 SIGABRT 已证实；修复后还需看到 loginwindow 真正建立 GUI 会话。不能只避免 abort、挂个空 ADM 服务或延长进程寿命来声称进桌面。
4. **桌面稳定后再接 A640。** 复用 iOS 项目的板级资源与 A640 实现思路，按 25F71 重建内核接口和 Metal 工厂。最新 SHENG `gpu.md` 同样要求按 GPU 代际/OS ABI 匹配，不能用 A740 后端替代 A640，也不能直接加载 iOS dylib。

SHENG 最新 `display.md` 要求已知帧、swap completion 和 IOSurface lifetime；`porting.toml` 还记录 macOS 插件只放 Extensions 会被 trusted-bundle 检查拒绝，以及 half-float/BGRA 的不同处理。这些是具体合同，不能简化成改服务名字。

本轮完成包解读、容器提取、版本固定和源码对照；没有改写设备，没有生成或宣称新的可刷桌面修复版。软件呈现/刷新修改通常需要重编显示组件并重新链接打包内核；iOS 的现成 kernel、bootstrap 和 dylib 不能替换当前 macOS 文件。
