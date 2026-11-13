# NABU Minimal GUI Boot test ESP

用户要求以可回退的白名单隔离并发服务负载。boot-arg `nabu-minimal-boot=1`精确开启；缺省、0或其他值不启用。核对原25F71 launchd缓存中的SecurityAgent标签后，25个指定标签加`com.apple.security.agent`、`com.apple.security.agent.login`两个同一必需组件的真实标签，共27个。

实现位于`platform/nabu/macos26/desktop/minimal_boot.h`与`minimal-boot.patch`。构建只追加补丁，不修改既有integrated/upstream固定hash。门控位于kern_exec.c原生POSIX_SPAWN_START_SUSPENDED处理前，使用成功复制的kernel argv[1]，不重复读取父任务可变的userspace argv。只处理实际p_comm=xpcproxy的新映像，标签精确比较；未知、缺失、过长或截断标签保持代理挂起。

被阻止的任务调用原生`task_hold_and_wait(task,false)`，对新的子任务增加kernel suspend_count；不会增加user_stop_count。因此launchd原有START_SUSPENDED的resume或SIGCONT不能释放这份独立内核hold。父spawn任务不被hold，继续原生属性、签名、端口绑定和返回。任务停在进入dyld/xpcproxy用户态前，不执行服务主体；PID存活，避免正常退出引发KeepAlive循环。代理仍可被原生终止/系统重启清除；若launchd另有显式kill/超时策略，仍需实机确认，未伪造check-in或服务就绪。

原watchdogd hook仍在旁边：仅对minimal已启用的proxy路由跳过旧ENOENT拒绝，由新hold接管；minimal关闭和watchdog直接执行路由保留原策略。此门控不覆盖直接执行且未经过xpcproxy的其他服务，也不修改所有posix_spawn调用。

每次成功hold计数`nabu_minimal_boot_parked`，首次64次写直接RAM日志：`NABU-MINIMAL: parked pid=... ppid=... label=... mode=kernel-hold seq=...`；第65条写唯一日志上限标记。没有轮询任务、打印循环、强制退出或对原服务做假返回。

打包新增`--minimal-boot`：核对编译元数据里的header/patch SHA256和native符号后才写入唯一`nabu-minimal-boot=1`，继续保持cpus8、原ADT/TimeSync/CPU显示、签名trustcache和logger token；ZIP只携带ESP五个替换文件及说明，不改root、Data和原日志。删除参数或设为0关闭；备份旧五文件可回退。

测试：生产头文件ASan/UBSan覆盖27个allow标签、22个deny/近似标签、非proxy映像、无开关/0/其他值、非法标签、失败hold、非PID1父任务、64条限流；真实准备XNU上应用patch，并核对原task.c中kernel hold/user resume分离与current thread不自等待的合同。完整source prepare和Intel ARM64 CI编译、最终机器码检查、ZIP与ADT等交付验证另记build-plan。

这是用户指定的最小依赖试验，不承诺列出的集合完整，屏蔽trustd等服务可能令某些允许服务仍等待。没有以白名单消除全部SEP调用、修复LS store、5秒启动或实机点亮的结果声明。
