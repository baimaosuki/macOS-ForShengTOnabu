# NABU 25F71：Early boot complete 之后

输入：`/home/baimao/nabu-boot-log-20261112-040445/boot.txt`，SHA-256
`859c3f599b2a56e653033a4a371968e11d75e08fb3deb46dd9793b3314764291`。
行号按 LF 计数，与 `rg -n` 一致。

## 已通过的设备阶段

- 729：writable Preboot 检查 `stage 5, status 0`。
- 962–963：launchd `Early boot complete. Continuing system boot.`。
- 1007–1008：WindowServer 原程序 exec 成功。
- 1025：loginwindow 已执行。
- 1060：fskitd 打开 AppleLIFSUserClient；不再停在缺少 lifs 服务。
- 1188–1192：cryptexd proxy 和原程序执行；本次没有记录到旧 mkdirat EROFS abort。

ESP 日志两个槽均 CRC 有效，选中 slot 0 / sequence 369 / 144445 字节。
slot 1 的 sequence 是 368。零 ring overwrite/contended bytes 不代表覆盖了
所有用户态日志，最后一次提交后的字节也可能缺失。

## 下一次启动必须回答的问题

cfprefsd、digest-service、eligibilityd、endpointsecurityd 重复执行，现有记录
不能区分正常短任务、不同账号的实例、非零退出和致命信号。PID 增长和
DYLD unnest 本身不是失败原因。

1027–1028 明确记录 opendirectoryd PID 68 因 SIGABRT 退出；1032 的 PID 104
重新执行。没有第二次 OD 退出证据。当前种子的全部 304 条用户数据库 plist
可解析，303 条原模板记录未缺失；不重建 `/Search`，它是 OD 逻辑节点。

已有 lifecycle trace 的选中列表漏掉上述服务；`event=exit` 为零不能说明
退出追踪未编译。当前产物反汇编确认 `_proc_exit` 调用 `_nabu_lifecycle_exit`。
普通 `abort()` 的致命信号还可能绕过 `abort_with_payload` 观察点。
修订沿用现有 ESP 日志，扩充选中目标并观察默认致命信号递送，记录真实 UID/
有效 UID、退出状态、信号及候选用户栈；保留每类预算和原退出/信号行为。

## 不据此放宽的权限

877–878 的 dirs_cleaner RENAME_SWAP/purge 返回 EPERM。原种子中 ino 9174
确为 `/private/var/run`，bsd_flags=0，uid=0，gid=1，mode=0775；parent 8629
为 `/private/var`，SF_NOUNLINK，uid=0，gid=0，mode=0755。Data 可写与 Preboot
写入检查已经通过，不能把 EPERM 等同于只读，更不能据此清除整树保护标志。

## 独立的后续缺口

765 的 AppleKeyStore 服务缺失属实。sheng catalog 固定的 AppleKeyStoreState
`8c70daf814aafdf4a3d35e40dde0322655fd0a83` 提供软件状态和 wrap/unwrap；当前
NABU 未接入，旧本地 checkout `e59bac4` 也没有完整软件包装。它需要稳定板级
identity、平台回调及 25F71 selector ABI 核对，现阶段不能将该 warning 当作
上述重启的已证实原因。

本次包没有桌面显示 backend。WindowServer exec 证明用户态进入该程序，
不证明显示初始化成功，也不能据此承诺下一步直接出现桌面。
