# NABU 25F71：批准项目 1–5 的集成记录

目标保持 macOS 26.5 / 25F71、NABU / SM8150。参考固定在 UniXNU
`268bfa1c587db5ee616e955cdfd97a281500a6fb`、UniXNU-sheng
`81f43ebaaa3317a5917b76093f7bc44c4949bc02`。保留已在设备生效的
Quarantine、UFS/根盘、执行兼容、Preboot、watchdog 策略与 ESP 日志。

| 批准项目 | 已落实 | 尚待确认 |
|---|---|---|
| 1. OD 内部连接超时 | 原版二进制和最新日志核对，见 `nabu-20261112-od-cold-start.md` | 已打包首次连接同步化实验候选；11:03 本轮无断言，另有已注册解析器查询超时，尚未证明候选能修复 |
| 2. Hardware | 补丁 22 将公共路径映射到已验证的可写 Data，启动前完成真实写入/读回/删除 | 新内核的设备启动结果 |
| 3. AppleKeyStore | 固定主线软件服务，核对 25F71 libaks selector，接入正常 OSKext 生命周期 | 用户态实际打开及软件 wrap/unwrap；不提供 SEP |
| 4. 显示 | 25F71 473-slot framebuffer ABI、NABU GOP 几何和身份、ADT、原版图形 families | WindowServer 的有效 root IOSurface、完成呈现及实际 GUI；不提供 Metal/GPU |
| 5. TimeSync | 原版 TimeSync/gPTP families，本地 Ethernet 身份、稳定 MAC、400-slot ABI 校验 | configd 命名 enN、原版 TimeSync 时钟及 WindowServer 等待状态 |

## 实际构建路径

- Hardware 修改进入 `desktop/integrated.patch`；15 个 preimage 哈希匹配，全新 prepare 成功。
- AKS 的两份 cpp、TimeSync 的一份 cpp 编译为独立对象，链接进 NativeUfs companion；由 VFS-late 回调在普通 OSKext 构造完成后启动。
- GOP 复用对应 25F71 的私有 ABI，按 SHENG 的 provider/swap/ownership 分工适配。主线的 iOS17 ABI 未复制到 25F71。
- 新闭包为 35 个原版 25F71 组件。新增的 graphics/networking 依赖全部来自同一 KC。
- AppleKeyStore 和本地 MAC 使用同一份持久保存的板级 UUID；构建包将其写入 ADT `/options/platform-uuid`。该身份是公开软件材料，不是硬件秘密。
- 保持 `cpus=1`；接口保持 carrier down；仅通过 `native-gop-display=1` 启用显示。

## 本轮发现并修正的打包问题

预链接器原先用 segment 的文件长度作为引用范围，漏掉图形组件的 BSS。
现按完整 VM 范围翻译地址，只复制文件字节，其余保持零初始化。

Libm `_log10` 常量表中的 double `0x3fb9806d94167d88` 被误判为 BL。
新增识别器只在完整函数、表哈希、索引边界和消费指令均匹配时标记为数据，
保留常量原字节。未放开未解析引用或绑定任意内核地址。

35-family 参考链接：0 未解析引用；独立校验通过，抽验 1000 指针、56031 分支、
1000 内核绑定。这个参考链接验证依赖和打包器，不代表新服务已在设备运行。

首轮 CI `43113229182` 在 TimeSync 的 SDK 网络头弃用警告处失败。
修复只作用于这些头文件的 include 范围，项目源码仍保留 `-Werror`。
后续 CI、最终包及 SHA-256 以交付目录 `delivery.json` 为准。

最终 CI `43115207926` 成功，编译提交
`c62acd4ea2cb1c7b5f554088c1f75ad9ece315a8`。实际新对象完成本地预链接和独立
校验：0 未解析引用、显示 473 槽、网络 400 槽、4 个真实 TU 构造函数。
定向测试 14 项通过。SDK 的 reserved2–5 仅在对应继承槽 331–334 映射到原版
四个方法，所有 vtable 外引用拒绝；保留匹配原版 ARM64 PAC 翻译后 family 的精确哈希。

测试包 `out/nabu-service-startup/nabu-25F71-services-43115207926.zip`，
172170245 字节，SHA-256
`2ca23a64b8a0ad0bd7a53712ed381b2639d2c862491549faf5b9122795e6085d`。
内核 314085376 字节，SHA-256
`ebc06c818ca467773b782b4dc53fbeece6a4b83dea2e87e20493eddec9fa6845`。
ZIP CRC 检查通过；安装配置只追加 `native-gop-display=1`，原日志 token 保留。
本轮设备保持 TWRP，已备份原五文件与日志，未刷入新包。

## 打包入口

`scripts/package_nabu_services.py` 接收成功 CI 产物、run metadata、当前安装的 TOML
与持久身份 JSON；执行实际链接、预链接、独立校验并装配五文件 ESP 测试包。
保留归档设备的日志 token 和启动项，仅添加显示参数，不重新分配 `XNU2LOG.BIN`。

不以服务 exec、driver start 或 host 校验声明桌面成功。当前 Data 仍为 RAM seed。


## 后续 11:03 实机证据

用户提供 `/home/baimao/nabu-boot-log-20261112-110344/boot.txt`，SHA-256
`92cba1533c3c619c1e6968923c3b32a055748baf66380886b3b56e6040ddf0e5`，
对应本包已在另一操作侧刷入并启动。Hardware 映射返回 0；软件 AKS 注册并回应
selector 0/7/17，但 contextstored 的 user client 被 MACF 拒绝。GOP 后端已启动，
尚无 swap 证据；TimeSync 家族启动，本地 en0 注册，legacy TX 下 fsw 附加被跳过。
WindowServer/loginwindow 已 exec，不能据此宣称桌面呈现。

OD 这轮未捕获 abort，32 线程高水位不是连接超时证据。另有 kauth 60 秒请求超时，
意味着解析器曾完成注册；新症状与首次连接断言需要分开。具体候选、成功轻量 CI、
包校验和和运行限制见 `nabu-20261112-od-cold-start.md`。本代理未对设备进行写入。
