# NABU 25F71：按 sheng 27.3 补齐 Quarantine

## 参考与缺口

sheng 27.3 的固定参考为 UniXNU `268bfa1c587db5ee616e955cdfd97a281500a6fb`。
`profiles/os/macos-27.3-26C5035r/profile.toml:51` 的 prelink roots 包含
`com.apple.security.quarantine`；NABU 上一版的 14 个运行组件没有它。
本次复用该组件选择方法，实际二进制取自原版 25F71 KC，目标不切换为 27.3。

设备输入：`/home/baimao/nabu-boot-log-20261112-052546/boot.txt`，SHA-256
`e739ec967049340e547739cbd70cd9f05340eeffc0a803b7e336571591e9b57f`。
root 的 cfprefsd daemon 连续退出，原始 wait=0xfffffe00、signal=0、reason=0。

## 原程序失败链

1. 25F71 cfprefsd 的 0x100000604 调用 `_qtn_proc_apply_to_self`；非零时
   0x100000684 直接 `exit(w0)`。分支前的 os_log 文本为 quarantine apply 失败。
2. 原 libquarantine 的 apply_to_pid 调用 `__sandbox_ms("Quarantine", 0x57, args)`。
   该入口与 `__mac_syscall` 同址，实际 syscall 381。
3. 原 XNU `security/mac_base.c` 在找不到注册的策略时返回 `ENOPOLICY`，
   `bsd/sys/errno.h` 定义其值为 103。
4. libquarantine 在 0x18f571004 比较 errno 103，并映射为 -2 返回。
   因而这条缺策略失败链与设备连续的 cfprefsd 退出值完全吻合。

Darwin kernel 的 WEXITSTATUS 在非 UNIX03 分支不掩码，故会打印 -2；
UNIX03 wait 调用方看到 254。它是进程调用 exit 的状态，不是致命信号。

## 实施

`config/nabu-macos26-desktop-prelink.json` 新增原 `com.apple.security.quarantine`。
原组件 CFBundleVersion 为 4，依赖 AppleMatch、sandbox 和内核 KPI，均已选入。
依赖闭包只增此一个运行组件。保留其 `AppleSecurityExtension=true` 和
`OSBundleRequired=Root`，经正常 mac_policy_initmach/loadSecurityExtensions 启动。
原 kmod start 会注册名为 `Quarantine` 的 MAC policy 和实际 syscall handler。

复用成功 CI 43099264156 的 native 内核及已链接 UFS 模块，在 Linux 本机重新
prelink，未重新编译 XNU；根盘、Data、启动配置和另三个 payload 无需变化。
原程序和沙盒权限不作绕过。完整机器码地址链保存在
`out/nabu-service-startup/quarantine-evidence.json`。

## 产物与校验

产物：`out/nabu-service-startup/nabu-25F71-quarantine-policy-fix.zip`。
内核 SHA-256：`7c1fae7a5387498efb3f84f7d25f6a3a6105f512e7df9e68b9a060d9ee654cf4`。
独立预链接检查通过：22941 条 branch、抽样 1000 指针及 1000 内核绑定；
原 UFS 可执行 section 保持逐字节一致，未解析引用为零。
最终 __PRELINK_INFO 中 Quarantine 的早期安全加载属性已回读确认。

待设备确认：启动出现 Quarantine policy 初始化，cfprefsd 的同一 exit -2
循环消失。相同 LC_UUID 来自复用的 native 内核，修改后的预链接包以 SHA-256
识别。尚未实机运行，不宣称后续全部服务或图形桌面已通过。

## 同时对照的主线事项

主线源码 libpthread 与 NABU 原 KC pthread 是不同集成方式；NABU 已注册原版
pthread shims，不能再增加第二套 source bootstrap。主线的
`macos-kqworkloop-limits.patch` 针对 27 用户态与 XNU 12377 的计数差异；
当前 25F71 配对环境的 OD 失败是 PlistFile 内部连接的 SIGABRT 断言，
没有证据支持直接套用该限制补丁。

OD 的 `connection != NULL` 当前已追到内部异步完成等待失败，缺用户记录、
Mach 服务缺失、调大超时、修改 APFS Role 或硬编码 RTC 均未获根因证据。
digest-service 的 exit 1 可能经 OD 节点或 KerberosKDC 记录读取失败返回，
不通过禁用该服务或抹去退出状态掩盖。
