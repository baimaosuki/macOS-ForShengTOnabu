# 本轮 vsync 日志全文复核

已逐段读取 `out/nabu-logs-20261113/verified-vsync.txt` 第 1–1968 行，
不是只检索关键词。用户提供的 `xnu2log-vsync.txt` 共 1974 个 LF 行，
前六行是提取工具写入的 249 字节元信息，后面的 177594 字节与校验后的
正文逐字节一致。原文件均未修改。结构化证据保存在同目录
`vsync-full-review.json`，显示事件明细见 `vsync-display-audit.json`。

## 边界和当前状态

两个槽 CRC 都有效，选中 slot 1 / sequence 7808。覆盖数 0，但有 3 字节
争用丢失；最后一条记录在启动后 285.140 秒。4 MiB 的环形缓冲区没有写满。
这说明读取了本次已提交快照的全部正文，不能说明采集覆盖设备之后的全部运行。
不能用文件结尾证明第三帧之后永久死锁。

内核 banner 是 2026-11-13 06:20:18 UTC；启动参数含 cpus=8、sheng-smp=1、
CPU compositor、原生 GOP、TimeSync bringup 和 probe v4。本轮已进入用户态，
没有捕获到 NABU-PANIC-1、NABU-ABORT 或 WindowServer 的进程退出记录。
本轮也没有 `Didn't find ADM service`；不能套用旧单核日志的 ADM 退出结论。

## 时间线

| 启动后时间 | 事件与证据 |
| --- | --- |
| 19.494 s | GOP scanout 映射，1600×2560、stride 6400（正文 358） |
| 52.085 s | hardware started，首次定时器成功设定；52.102 s callback 开始 |
| 约 61–65 s | Data/VM 两轮写入、关闭、unlink 均成功；mount phase 1/2 完成 |
| 约 123 s | Early boot complete（1136）；启动任务完成不等于登录会话完成 |
| 141.115 s | opendirectoryd 正式 exec（1165） |
| 152.162 s | loginwindow console exec（1285） |
| 152.689 s | WindowServer 正式 exec（1339） |
| 162.259 s | OD 注册外部身份解析器，后续 22 个请求均返回 |
| 211.809 s | endpointsecurityd 退出 35，随后重新启动 |
| 241.538 s | display on，power notify begin/done 都返回（1895–1897） |
| 253.413 s | loginwindow 一次 msgid 2802 返回 MACH_SEND_INVALID_DEST（1905） |
| 282.955 s | first swap submitted（1925） |
| 283.007 s | 关闭文字控制台；随后开始写入黑色显示帧 |
| 283.025 / .099 / .430 s | 三帧 scanout copy 和原生完成分别返回 |
| 285.140 s | 最后一条状态：submitted=4、shown=3、dropped=0、current=NULL |

WindowServer exec 到 display-on 相隔 **88.849 秒**，display-on 到首次提交
又相隔 **41.417 秒**。这两段迟延都发生在所谓“第三帧阻塞”之前，必须单独排查。

## 显示证据的准确含义

三帧均为 4096000 像素、RGB 非零数 0，CPU 源拷贝、scanout 拷贝、原生完成均返回。
第一、第二帧使用**同一个** IOSurface 指针 `0xfffffe1ff13c4400`；第三帧改用
`0xfffffe1ff13c47e0`。日志只展示两个不同源 surface，不能据三次提交认定
三个不同缓冲区已经耗尽。

`centre=0xff000000` 的 alpha 也不能证明 WindowServer 原始 alpha 为 0xff：
本地 `nabuGopLayerRow` 对不透明场景层执行 `pixel | 0xff000000`。RGB 在
BGRA 1:1 路径保留，因此可确认此次读取并输出的 RGB 是黑色；不能由此证明
已经完成 Root Backdrop 或 loginwindow 内容渲染。

`current` 来自 +0x1790，类型为 `IOMFBSwapIORequest *`；完成后为空是原生释放
请求后的状态。+6412 / +0x190c 是最后显示 ID，已记录为 1、2、3。原版 mode 0
非阻塞查询这个仍作为最后显示帧的 ID 会返回 Busy。精确地址与回放验证见
`swap-wait-completion-audit.md` 和对应测试，不能把 ID 写入请求指针。

现有合成循环遍历 0、2、3、1 四层，最后处理原生 cursor 层；并不是只遍历 0。
mask 是更新掩码，置位而 surface=NULL 表示清空。旧日志在空 surface 或零面积
分支之前没有打印，所以没有给出 layer1/2 被略过的具体原因。这是需要补齐的
观测缺口，不能反向断言它们有有效内容却被丢掉。场景 BGRA 不自动代表启用
alpha 混合；要按原生层语义处理，不能让 alpha=0 的不透明底层消失。

## 启动性能与服务故障

| 项目 | 全文证据 | 当前判断和处理方向 |
| --- | --- | --- |
| init-exclavekit | 到下一启动事件约 23.099 s（1126–1136） | 记录的是事件间隔，包含调度/并行工作，尚不能全部算到函数内部；需核原平台缺失处理 |
| init_featureflags | 到下一任务约 15.375 s（1055–1069） | 是明显早期迟延，应核模板数据、访问路径及实际等待点 |
| auearlyboot | 约 5.948 s；自身报告没有 Early Boot Accessories、关键条目等待 0 s | 不能把全部间隔归因于等待固件设备 |
| endpointsecurityd | --init 793.8 ms 后正常 exit 0；常驻 PID98 存活 62.999 s 后 exit35，PID226 重拉 | 早期任务成功不能覆盖常驻服务失败；应核独立 EndpointSecurity 实现/密钥错误 |
| digest-service | PID172 存活12.123 s、PID187存活58.240 s，均exit1；PID244再次启动 | 本轮确有认证辅助服务重启，尚无栈或致命文本证明阻塞loginwindow |
| AKS/MACF | `Failed to handle the new encryption key ... 0xe00002d8`（1751）；contextstored打开AKS被MACF拒绝（1866） | selector返回0不足以证明全套KeyStore协议正确；前者未带PID，不能仅凭相邻行归属trustd/ES |
| OD | 22个kauth结果，0 requester timeout；result中位5.196 ms、最长16.177 s；103次timedwait返回均0 | 不是旧版本的30秒ETIMEDOUT形态，但启动初期仍有实际慢请求 |
| OD结果到唤醒 | seq31348/51/52/53 结果提交之后仍等1.259/1.617/1.077/1.074 s | 应查唤醒/调度及诊断开销，不能全部归咎OD数据库查询 |
| IPC | OD msg11602有17次MACH_SEND_TIMED_OUT；loginwindow有一次无效目标、两次RCV_TOO_LARGE及两次RCV_PORT_CHANGED | IPC错误需要按调用语义解释；不能声称所有IPC都正常，也不能把轮询超时直接当死锁 |

## 存储、数据布局和平台残缺

Root、SystemCryptex、AppCryptex、Data 均 mount-complete。Data/VM 写探针成功，
Hardware映射成功，17个firmlink成功；AppleInternal和pkg缺失被跳过。
Root仍缺默认快照认证信息，报告 Need authenticator(81)，但随后挂载并进入用户态。
Data role=0、Root不在volume group；这些是布局差异，日志没有证明它们是黑帧根因。

dirs_cleaner 对 `/private/var/run` 的 RENAME_SWAP 返回 EPERM（1024），APFS rmdir
授权同样失败。应核原模板目录flags/owner和路径权限，而非把全部Data判成只读。
Data低盘通知记录 free=778723 blocks；不能读成磁盘已满。

PRNG持久化种子与virtual random加载失败（1119–1121）、tzinit回退系统分区、
Image4 nonce/manifest信息缺失、boot-perf信息缺失均在本轮仍存在。
FIPS全部自检通过，但这不能证明持久化种子、AKS和账户认证都已正常。
TimeSync族已注册、Ethernet identity无carrier；没有本轮时钟服务缺失的直接证据。

## 诊断自身的缺陷

OD、loginwindow和WindowServer都打印了 `Sampling completed, processing symbols...`，
随后由脚本15秒guard终止为143。不是尚未运行sample，也不是已拿到可用线程栈。
当前策略把符号化阶段和采样阶段共用短超时，导致真实取样无法导出；必须核实
工具支持的原始输出/符号化控制方式或拆分导出，不能靠反复跑同一个失败探针。
ps同样有一次5秒guard终止，启动期间大量诊断子进程增加负担，不能忽略。

本轮的全文结果支持继续修完成时钟、明确各层状态和采样导出；它不支持将
current指针写为ID、强制Busy成功、宣称只有一个阻塞或已进入可用桌面。
