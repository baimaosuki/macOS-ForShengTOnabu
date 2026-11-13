# NABU-SESSION IPC 对端诊断

本变更是默认关闭的 RAM 日志观察器，由 `nabu-session-log=1` 开启。按当前进程名选择
`WindowServer`、`loginwindow`、`opendirectoryd`，不使用固定 PID。它不修改消息、权利、
超时、调度状态、端口队列或 IPC 返回值，也不读取设备 Data。

## 日志含义

`ipc-enter` 记录 mach_msg2 输入，`ipc-wait` 记录发送步骤成功后、即将进入原生接收函数
的状态；`ipc-receive-result` 记录 `mach_msg_receive_results` 开始时的原生 `ith_state`，
覆盖阻塞后通过 continuation 返回的路径。同步返回还会产生 `ipc-return`，其 result 是
trap 的最终结果。`ipc-receive-result` 是接收状态快照，后续 copyout 仍可能产生别的错误。

- `call` 是本次观察分配的关联号；`tid` 是内核 thread ID。
- `msgid` 是发送方 mach_msg2 打包参数中的 ID，**不是收到的回复 ID**。
  receive-only 调用输出 `msgid_known=0`，并清零不可用的 send 字段。
- `remote`、`local` 分别是调用者 send header 的 remote/local port name；
  `receive` 是独立的 `rcv_name`，可能是接收权或 port set。三者不能混用。
- `peer_pid`、`peer_receive` 是发送 remote port 对应接收方的 PID 和接收方 namespace
  内端口名的瞬时快照；接收权随后可以迁移。它们不是 bootstrap 服务名。
- `peer_name_at_lookup` 是 IPC 解锁后通过 PID 做的 best-effort 名称查询；进程可能已退出
  或 PID 可能复用，因此它不是受引用保护的任务身份。探针从不输出或保留 task 指针。
- `peer=space-busy/port-busy` 表示 try-lock 没成功；`invalid-name/space-inactive/
  port-inactive/unknown` 分别说明快照不可得的原因。receive-only 为 `no-send`，
  不推断它在等哪个 bootstrap 服务。
- `result=10004003 status=timed-out` 对应 `MACH_RCV_TIMED_OUT`；
  `result=10004004 status=too-large` 对应 `MACH_RCV_TOO_LARGE`。后者不表示超时。

WindowServer和OD继续使用旧关联表，类别各保留64个槽（原表总192槽，loginwindow已绕开），记录中只有数值、固定名称和静态字符串。
它在发送工作完成后、调用原生 trap receive 前安装记录，避免 send 内部的 kernel receive
抢走用户接收的关联。原生 continuation 放弃 trap 栈后，通过 tid 找回记录。每个槽用一次
非阻塞 CAS 保护；新 trap 入口用独立 atomic owner 失效自己的旧关联，即使前次非阻塞清理遇忙也不会复用旧 ID。忙或同类 tid 哈希冲突时输出 `call=0 assoc=unavailable`，绝不套用别的
线程的 msgid/port。不新增 thread 字段，不保留线程引用，不跨调用猜测“最近 send”。

## 锁与生命周期依据：XNU 12377.121.6 / 25F71

代码位于 `platform/nabu/macos26/log/session_ipc.h`，由新增
`zzzzzzzz-nabu-session-peer.patch` 在 `mach_msg.c` 的 IPC includes 后包含。

1. current task 保持 current space 有效。用原生
   `lck_ticket_lock_try(&space->is_lock, &ipc_lck_grp)`，不阻塞等待 space 锁。
2. space 锁下确认 active，再用 `ipc_entry_lookup` 验证完整 name/generation，并验证条目
   含 send/send-once/receive 类型。receive 类型可用于 header 的 MAKE_SEND 情况，诊断不消费权利。
3. space 锁固定条目和其对象。对 port 用 `ip_mq_lock_try`，失败立即解锁，避免对原生
   port-first/SMR 路径造成等待式锁反转。
4. port 锁下确认 active，再调用本版原生
   `ipc_port_get_receiver_task_locked(port, NULL)`，只取得 PID 标量；用
   `ip_get_receiver_name` 取得端口名。原生 helper 自己处理 in-space、kernel/reply space
   和 task 生命周期条件；探针不直接访问 `ip_receiver` 或 `is_task`。
   本版 `task_pid` 只对 `task_tokens->tc_pid` 做 relaxed atomic load，不查 proc、不分配。
5. 先解 port，再解 space。所有 borrowed pointers 的使用都在锁内结束。PID 和端口名是
   值拷贝，不需要额外 port/space/task 引用。之后才查询进程名、格式化和写 RAM journal。

锁内没有日志、proc_name、用户 copy、分配、task reference 或阻塞锁。
这不是 `ipc_port_translate_send` 后留住它返回的借用 port；该阻塞 API 未用于探针。

## 预算与保留限制

WindowServer和OD继续使用entry/result/error独立通道、初始256条后每秒采样；
因此WS高频receive-only循环不会改成无限日志。resolver另有独立预算。
这类事件级采样**不能保证一组入口与完成配对**，旧版loginwindow正是因此在397.54秒
成功结果通道耗尽后丢失30176的配对结果；此前“250秒仍有采样”测试没有覆盖这个缺陷。

loginwindow现在绕开事件通道，按**整次call**准入。16个永久tid-owned槽用CAS认领，
每个槽只有对应线程访问其record，其他loginwindow线程不能消耗其额度，也不会因hash碰撞
丢关联。每tid每60秒有128KiB字节预留额度，总计16tid最多2MiB/epoch，不动态分配大缓冲。
开发观察器在400秒明确开始新的late-capture phase，此后仍按60秒epoch；这个调试边界不改变
IPC或产品行为。每epoch首个call都会输出`ipc-epoch`，含epoch/limit/previous_dropped。
额度不足输出一次`ipc-drop reason=epoch-byte-bound`，下一epoch汇报完整drop数；
16个tid槽用尽输出明确`tid-slots-full`，绝不把它伪装成服务超时。

入口一次预留entry512B、completion256B及最多一条final-correction256B，共1024B/call，
每tid每epoch最多128次新call。入口一次预留这些记录的最大长度，只有准入成功才输出entry。
`ipc-wait`不独立占一行，只在该tid槽保存wait_ns；completion通过同call报告wait_ns和
stage=receive/return。已准入completion**不再查事件计数、时间或其他线程额度**，因此可以
跨400秒和任意后续epoch仍输出。send-only错误由return完成，continuation由receive hook完成；
后者仍是原生copyout前receive state。同步return与receive state相同就去重；
若copyout后最终返回值不同，额外输出已预留的`ipc-return stage=final-correction`保留最终errno。
阻塞continuation会直接`thread_syscall_return`，不经过trap栈的end hook；因此新增
`mach_msg_receive_continue()`中results/copyout之后、syscall_return之前的final hook。
它只读取当前tid已准入且已记录receive state的loginwindow槽，比较最终结果、差异correction、
同值去重并清空selected/waiting；不新建call，不影响WS/OD。copyout期间内部kernel IPC的
receive callback因result_logged已设置而不能替换外层结果。若下一trap发现旧call一直没有观察到结果，先用旧预留
输出`result_known=0 stage=next-trap`，再覆盖旧pending，不制造成功结果。
compact completion保留`event=ipc-receive-result name=loginwindow call`以供离线按call合并；
msgid/ports/peer等元数据来自匹配的entry，不能要求每个compact completion独立携带全部字段。

这里保证的是**不再被本观察器的独立事件限流丢弃**。底层NabuLogCore仍为try-lock RAM ring，
可能因写竞争丢字节，或因全局4MiB ring绕回覆盖旧数据；本次未修改core，也不能声称无损保存。
容量界限、epoch和drop marker把本层可解释的缺失显式化；仍需结合core lost统计和retained导出。

每行缓冲为 512 字节；截断时强制以 ` truncated=1\n` 结尾，正常记录也强制换行，
避免 journal 内多条事件粘连。RAM journal 自身容量和导出时机仍限制最终可见历史。

## 验证

```sh
python3 -m pytest -q tests/test_nabu_session_ipc.py tests/test_nabu_session_repair.py
```

实际生产 header 在 ASan/UBSan 下运行。fixture 对 proc_name、日志和锁操作作断言，覆盖
开关与进程名筛选、send-once、无效/死亡端口、两类锁忙、无 receiver task、所有解锁路径、
真实 local/receive 区分、receive-only、同步/continuation 关联、槽冲突/忙、OD 预算隔离、
独立错误预算、250 秒后仍采样、长行截断换行。新增真实EVI 403秒30176入口seq465回放，
先证明旧同秒入口/完成都会丢，新准入则配对保留；另覆盖同秒多次成功/失败完成、其他tid刷满隔离、
跨epoch continuation、最终copyout错误correction（同步及直接syscall-return continuation）、内部kernelIPC不误关联、397秒额度耗尽后400秒显式phase恢复、未观察旧pending显式unknown和16槽满。另在真实已准备的 XNU `mach_msg.c` 副本
应用新增 patch，并核对原生 helper 和 task_pid 实现契约。

本轮也完成全补丁 ordinary ARM64 源码准备：

```sh
MACOS26_OUT=/home/baimao/nabu_macos/out/nabu-session-ipc-20261114/source-prepare \
  python3 scripts/build_macos26_xnu.py --board nabu --arch arm64 --prepare-only
NABU_SESSION_IPC_SOURCE=/home/baimao/nabu_macos/out/nabu-session-ipc-20261114/source-prepare/source \
  python3 -m pytest -q tests/test_nabu_session_ipc.py tests/test_nabu_session_repair.py
```

旧版7项测试通过；修复call配对后增加真实EVI回放，总计8项测试通过；生成的两份 IPC header 与仓库文件字节一致，build.json 记录新 patch 及
`session_trace_sha256`、`session_ipc_sha256`。原源码测试兼容已应用和未应用新 patch 的准备树，
仅对临时副本先反向、再正向应用，不改准备树本体。

host 测试和源码准备不替代 macOS 内核编译或真机验证，也不证明对应 IPC 对端就是启动阻塞根因。
本任务未启动 CI、未发布包、未修改现有显示或 bootshim 实现。
