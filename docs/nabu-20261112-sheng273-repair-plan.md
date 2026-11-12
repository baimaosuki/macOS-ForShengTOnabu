# NABU 19:31 日志与 SHENG 27.3 修复方案复核

本轮审查对象是 `/home/baimao/nabu-boot-log-20261112-193111/boot.txt`：137513 字节、1551 个 LF 行，SHA-256 `9f5afe108d0a43fea8821ae7381184aa0b2fb34469a24e8c15c6126118b29e32`。行号保留 CR，按 LF 计算。对应测试内核来自 CI 43167776272，最终镜像 SHA-256 `605762f8864113cb0e9900186d23348130e8cf2909aa6198af37f8212f99c60c`。

设备实际是 **25F71 / macOS 26.5 / Darwin 25.5.0**。参考固定版本为 UniXNU `268bfa1c587db5ee616e955cdfd97a281500a6fb`、UniXNU-sheng `81f43ebaaa3317a5917b76093f7bc44c4949bc02`；其 AppleKeyStoreState 子模块是 `6ff4f11d8fdf853351f8d98e1bbe1373fb8da2e6`。本轮新增读取这些固定版本的源码，不声称它们是远端最新版本。27.3 的 Darwin 26 MAC hook、私有类 ABI 和用户态地址不直接用于 25F71。

## 1. 新确认的可修复缺陷：19.2 MHz 微秒换算

这次超大耗时不再只是一条待解释的打印。

1. 测试包 ADT 的 `/cpus` 和 `/cpus/cpu@0` 都是 `timebase-frequency=19200000`。
2. XNU `osfmk/arm/rtclock.c:141` 设置 `rtclock_usec_divisor = divisor / USEC_PER_SEC`，得到整数 **19**。
3. `absolutetime_to_microtime` 先求秒，再把本秒剩余 tick 除以这个 19。秒尾会得到 **1010526 微秒**，超过合法范围；下一秒又从 0 微秒开始。
4. 实际构建产物反汇编确认 `_microuptime -> _clock_get_system_microtime` 使用这套整数除法。APFS 原始二进制的目录遍历和事务计时都调用 `_microuptime`；实际 prelink 报告把它绑定到 native `_microuptime` `0xfffffe000792bb38`，不是错误符号或不同 timeval 布局。

例如，计数从 `19185057` 增至 `19200000`，实际只过约 779 微秒，旧公式却从 1009739 变成 1000000，得到 **-9739 微秒**。负差值按 uint64 解释再除一百万，就得到 **18446744073709 秒**。这两个数分别与日志 1300、1196 行完全一致。这里的 tick 是主机复现输入，设备日志没有记录当时原始 tick，因此不声称恢复了该次调用的实际参数。

APFS `_attrlistbulk_iterator` 把该无符号差值与 15000000 微秒比较，超过阈值就结束本轮遍历；异常会进入功能分支，不只是显示问题。它可能放大服务初始化和目录扫描故障，但尚不能证明修正后 OD 或 loginwindow 必然恢复。另两条异常事务耗时也仍需复测，不把所有数字都归到这一原因。

**建议首修**：保留真实 19.2 MHz，在 `absolutetime_to_microtime` 把剩余 tick 换算为：

```c
*microsecs = (uint32_t)((abstime * USEC_PER_SEC) / rtclock_sec_divisor);
```

此处 `abstime` 已减去整秒，不是原始开机总 tick。频率是 uint32，因此剩余 tick 乘一百万仍在 uint64 范围内。`clock_get_system_microtime` 会调用该函数，BSD `microuptime` 随之修正。`absolutetime_to_nanoseconds` 已经先乘后除，不需要同样修改。

候选补丁已写到 `out/nabu-service-startup/ws-193111/sheng-comparison/nabu-19m2-microtime-candidate.patch`，**未应用到内核源码、未编译或刷机**。主机检查覆盖 19.2/24 MHz 各两个完整秒、共 **86400002** 个 tick：微秒归一化、单调不减、与独立有理数参考相等，且 24 MHz 旧结果不变。检查结果和复现 JSON 保存在同目录；这不替代设备验证。

**SHENG 的对应解法**：`macos-droidvm-k27-parity.patch` 实现 `timebase-hz=24000000`，把整个内核计数、定时器和用户态 trap 配成一致的 5/4 缩放；`timebase-user-scaled-read.patch` 再配套改用户态读计数路径。24 MHz 整除一百万，避开上述截断。相关补丁还处理 Swift 硬编码 Apple 时间比例的问题。它是后续可选的完整兼容路径，不等于只改 DT 频率。固定版本中的用户态直接缩放优化明确注明尚未实机验证。当前 libdispatch 已核实支持 625/12，不能据此说全部用户态都要求 24 MHz。

## 2. 当前进不了桌面的直接表现

两次 WindowServer 的退出栈都还原为：

```text
SLXServer -> server_loop -> CGXRunOneServicesPass -> run_timer_pass
          -> idle_timeout -> exit(0)
```

`idle_timeout` 检查活动代次与 `CGXConnectionsExist()`，无客户端连接时退出。原始 25F71 指令和本轮 PID 100、162 的捕获栈相互吻合。详细地址见 `nabu-20261112-windowserver-idle-exit.md`。

因此当前任务分为两条：**让登录/会话客户端完成连接**，以及**证明 CPU 合成与 framebuffer 真正呈现第一帧**。三页 CompositorSW/BGRA 修改已应用、显示已 on，但零 GOP swap；不能从这些记录推导桌面已渲染，或只剩 GPU 驱动。

与客户端相关的实际失败仍有：1237 行 OD 外部身份查询超时 60 秒；1224 行 OD 线程高水位；digest-service 五次 exit 1。60 秒结合原始 SystemCache 的注册实现支持 resolver 已注册，不能继续用“没注册”或旧首次连接断言解释。高水位也不等于线程硬上限。

## 3. SHENG 对照清单与执行顺序

| 顺序 | 本机证据 / 差异 | SHENG 解法与 NABU 具体处理 | 构建范围 / 验收 |
| --- | --- | --- | --- |
| A1，确定缺陷 | APFS -9739 微秒、超大秒数并提前停止目录遍历 | 上述微秒有理数换算。先做最小修正；完整 24 MHz 缩放另列，不能只改频率 | **重编内核、重新 prelink 打包**；根盘/Data 不用重刷。验收跨秒值合法、不倒退，原异常和提前停止消失 |
| A2，确定初始化缺失 | 日期仍从 1970 开始，fsck 报镜像时间在未来 | SHENG `BoardClock.cpp` 对接平台时钟，并把内核构建日期作为下限。NABU 先用可信启动时间或构建时间下限初始化 calendar；PMIC RTC 后补，不能照搬 PMK8550 寄存器 | 平台/内核或启动工具改动；验收启动日期合理、fsck 未来时间警告消失。它与 A1 的单调计数问题独立 |
| A3，最短路径定位剩余连接失败 | OD 查询超时；loginwindow exec 后未证明连上 WS | 下一测试内核同时只补关键记录：kauth 请求序号、发起 PID、查询类型、GETWORK/RESULT；loginwindow 的等待端口/错误/退出栈；TimeSync 实际匹配与 clock readiness。先用 A1 修正后的结果决定 OD 改动，不复用已撤回的旧 OD overlay | 和 A1 合并一次诊断构建，目标是把失败落到具体请求/返回；不是扩大所有 syscall 日志或再盲改 OD 队列 |
| B1，显示依赖待闭环 | en0 最后才出现；TimeSync 身份发布，但没有本轮 clock ready 证据；ADM 未找到；零 swap | SHENG macOS 指南明确要求原生 Ethernet/TimeSync clock，显示指南要求 IOSurface 帧、复制、completion。NABU 接通实际时钟匹配，核对所需显示服务，再验证 CPU 合成首帧与完成通知 | 对应服务/显示内核构建。验收至少一张真实用户态帧及 swap/completion，随后连续变化的登录画面；不是仅注册服务 |
| B2，确定实现差异、尚无直接触发证据 | 当前 root union 只改部分 fsid/dev 返回；有下层目录遍历补丁，但不等于完整身份一致性 | SHENG parity 补齐所有 getattrlist 形式，包括 bulk、REALDEVID/REALFSID、proc_info 与 vfs 列表，并修正 firmlink 的 `..` 和删除/改名最后一段语义。NABU 应按 **25F71** 调用路径补齐；优先检查 lsd/kernelmanager 的实际等待，不能把高 CPU 直接当成已证实的 fsid 故障 | 内核改动；验收相同根文件通过各接口得到一致设备身份、目录扫描完成。当前日志没出现 SHENG 的 `FileIDTreeGetVRefNumForDevice -36`，不能声称同一根因已发生 |
| B3，实际路径拒绝 | `/private/var/run` swap/purge EPERM，但普通写探针成功 | 对照 SHENG 保留 UID/GID、ACL/xattr 的模板与 firmlink 语义，区分 vnode flags、APFS rootless 和具体 MAC 拒绝。只修该拒绝的归属。SHENG `macos-end-rename-swap.patch` 是 **Darwin 26 MAC slot 55** 的 begin/end 配对，不能因同叫 RENAME_SWAP 就移植到 25F71 | 模板问题则只换 Data；路径/授权问题则内核。验收 rename/purge 成功。无需先改 APFS role 或全局关闭 MAC |
| B4，确有访问拒绝 | contextstored 打开 AppleKeyStoreUserClient 被 MACF 拒绝 | 已有与 SHENG 同功能的状态和软件 wrap/unwrap；检查具体客户端的策略、凭据和 25F71 用户客户端身份，不重新添加同一套 AKS，也不让所有不支持操作返回成功 | 按实际拒绝点改服务或策略；验收该客户端成功打开且关键调用返回预期值。当前尚未证明它阻塞了 loginwindow |
| C，出现对应等待时处理 | 当前无 mobileactivationd/cryptexd 等待栈；日志中有 mdmclient | SHENG parity 记录：对不支持的 mobileactivationd/cryptexd 仅拒绝 exec 曾让 mdmclient 等待，继而挡住登录；其 absent-service stand-in 主动结束服务连接。只有抓到本机同类等待才引入精确 stand-in，不能凭进程名批量禁服务 | 启动 addon/服务策略；ABI 和 IPC 协议按 25F71 核对。验收等待端收到明确终止、不再阻塞登录 |

这不是说 B/C 都必须完成才可能出现桌面；其中若干项可能在 A1 后自然缓解。建议首个功能测试包集中在 A1、A2，并带 A3 的小范围记录。随后按实际结果处理客户端连接及 B1，避免一次改动所有未证实分支。

## 4. 本轮排除的重复或错误方向

**Data 不是缺用户的空盘。** 交付包 `data.dbr` 与原始构建产物 SHA-256 都是 `a5c9bd38b9bf83bf6d28eb0e8d385a91ab9e47403eb997ec4d4b86c2decc7630`。交付分支的 `scripts/build_nabu_desktop_data.py` 以 root 调固定主线 seed 脚本，使用 `--skip-setup --user --auto-login`，挂载回读核验 `.AppleSetupDone`、`Users/nabu/.skipbuddy`、dslocal 用户、kcpassword、home UID 501、自动登录名和 Data UUID。保持凭据文件私有。本轮没有重新挂载 APFS 核验全部 ACL/xattr，也不把历史构建记录当作启动后目录没有变化的证明。

**编入包的 AKS 不是只读状态 stub。** 构建元数据选择 `platform/nabu/macos26/services/NabuKeyStoreComponent.cpp`，SHA-256 `9a25c253658195c1dfe2c9fa0aa4481929ec84a0d96c39c7f1dde9f3ac55460a`；它与本次下载的固定 SHENG AppleKeyStoreState 源码仅有注释中的开关名差异，包含 selector 10/11/23/40/160。对象内也有 deviceKey 实现。另一路 `sources/unixnu-components/...` 是旧版状态实现，不能拿它推断当前已交付二进制的功能。

**1222 行 encryption-key 报错属于 coredump。** 已定位原始打印到 XNU `bsd/kern/sys_generic.c:2986`，调用 `IOProvideCoreFileAccess(kdp_core_handle_new_encryption_key, ...)`，属于 `kern.coredump_encryption_key`。其 `kIOReturnNotReady` 不证明用户钥匙串失败，先从登录阻塞清单降级。与 contextstored 的 MACF 是两件事。

**ADM 已定位，但作用仍待核实。** 原始共享缓存字符串位于 IOMobileFramebuffer framework；引用函数是 `_AppleDisplayManagerOpenDefault` `0x18f2651a0`，它匹配 `AppleDisplayManager` 服务，匹配为空时输出该句。它是具体的显示依赖失败，不是泛指 GPU；本轮退出栈仍指向 no-client idle，不能直接把缺 ADM 宣称为唯一原因。

**27.3 特有补丁不能因名字相似就全上。** `macos-kqworkloop-limits.patch` 处理新 launchd 在旧 XNU 上的 workloop accounting；当前没有该原因的 kill 证据。`macos-service-port-derive-check.patch` 改的是 Darwin 26 MAC hook 返回类型；NABU 是配套 25F71。两者当前都不是可直接套用的 OD 解药。

## 5. 距离“进系统”的实际剩余工作

已经完成的本机证据包括：UEFI/内核、UFS 三个根窗口、APFS 根与 Data/VM、两阶段 mount、cryptex、FIPS 自检、launchd Early boot complete。当前是**登录会话和图形呈现 bring-up**，不是重新解决存储启动。

到可见登录/桌面，还必须证明三个结果：

1. 目录/身份服务的实际请求完成，loginwindow 或其他会话客户端连接上 WindowServer；不能仅以 exec 或进程未退出替代。
2. CPU compositor 产生有效 IOSurface，native framebuffer 完成首帧和后续 swap，面板确实显示用户态画面。
3. 本地用户进入 session，Dock/Finder 启动并有可操作画面；交互还要确认键鼠/触摸到达该 session。

它们是三个验收门槛，不是“正好剩三个 bug”。A1 是本轮已定位、可先修的确定错误，但还没有设备结果证明它能同时解除三个门槛。SHENG 使用实际 A740/Metal/Turnip 的链路，不提供已经验证可直接替换 NABU 的完整 CPU 桌面方案。当前也没有足够依据给出 90% 或一包必进桌面的承诺。

无需等待 Adreno GPU 驱动完善再尝试上述 CPU 路径。下一轮至少需要一次内核构建验证时间修正；其后的构建次数取决于会话连接与第一帧结果。不能从 CI 的约四十分钟编译时间推算总修复时间。

## 6. 复核产物

- `out/nabu-service-startup/ws-193111/sheng-comparison/apfs-time.disasm`：原始 APFS 目录计时和事务计时。
- `apfs-microuptime-bindings.json`：实际测试包的关键 prelink 绑定。
- `time-conversion-repro.json`：-9739 与 18446744073709 的有理数复现。
- `microtime_math_check.c` / `.txt`：86400002 样本检查及输出。
- `nabu-19m2-microtime-candidate.patch`：待审最小换算补丁。
- `adm.disasm`：原始 IOMobileFramebuffer 的 ADM 缺服务打印分支。
- `failure-scan.txt`：本轮日志的失败关键词摘录；不是额外启动日志。

本轮没有设备写入或重启；没有把候选补丁加入构建。全量日志记录的其他次要问题仍见 `nabu-20261112-full-boot-audit.md`；本报告的微秒换算、coredump 来源及实际 AKS 编入路径结论更新了其中原先待定位的项目。
