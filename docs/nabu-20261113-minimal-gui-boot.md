# NABU Minimal GUI Boot test ESP

用户要求以可回退的白名单隔离并发服务负载。boot-arg `nabu-minimal-boot=1`精确开启；缺省、0或其他值不启用。初版25个指定标签加两个原版SecurityAgent标签，共27个；实机暴露会话依赖缺失后，按下面证据补7个精确job标签，现共34个。

实现位于`platform/nabu/macos26/desktop/minimal_boot.h`与`minimal-boot.patch`。构建只追加补丁，不修改既有integrated/upstream固定hash。门控位于kern_exec.c原生POSIX_SPAWN_START_SUSPENDED处理前，使用成功复制的kernel argv[1]，不重复读取父任务可变的userspace argv。只处理实际p_comm=xpcproxy的新映像，标签精确比较；未知、缺失、过长或截断标签保持代理挂起。

被阻止的任务调用原生`task_hold_and_wait(task,false)`，对新的子任务增加kernel suspend_count；不会增加user_stop_count。因此launchd原有START_SUSPENDED的resume或SIGCONT不能释放这份独立内核hold。父spawn任务不被hold，继续原生属性、签名、端口绑定和返回。任务停在进入dyld/xpcproxy用户态前，不执行服务主体；PID存活，避免正常退出引发KeepAlive循环。代理仍可被原生终止/系统重启清除；若launchd另有显式kill/超时策略，仍需实机确认，未伪造check-in或服务就绪。

原watchdogd hook仍在旁边：仅对minimal已启用的proxy路由跳过旧ENOENT拒绝，由新hold接管；minimal关闭和watchdog直接执行路由保留原策略。此门控不覆盖直接执行且未经过xpcproxy的其他服务，也不修改所有posix_spawn调用。

每次成功hold计数`nabu_minimal_boot_parked`，首次64次写直接RAM日志：`NABU-MINIMAL: parked pid=... ppid=... label=... mode=kernel-hold seq=...`；第65条写唯一日志上限标记。没有轮询任务、打印循环、强制退出或对原服务做假返回。

打包新增`--minimal-boot`：核对编译元数据里的header/patch SHA256和native符号后才写入唯一`nabu-minimal-boot=1`，继续保持cpus8、原ADT/TimeSync/CPU显示、签名trustcache和logger token；ZIP只携带ESP五个替换文件及说明，不改root、Data和原日志。删除参数或设为0关闭；备份旧五文件可回退。

测试：生产头文件ASan/UBSan覆盖27个allow标签、22个deny/近似标签、非proxy映像、无开关/0/其他值、非法标签、失败hold、非PID1父任务、64条限流；真实准备XNU上应用patch，并核对原task.c中kernel hold/user resume分离与current thread不自等待的合同。完整source prepare和Intel ARM64 CI编译、最终机器码检查、ZIP与ADT等交付验证另记build-plan。

这是用户指定的最小依赖试验，不承诺列出的集合完整，屏蔽trustd等服务可能令某些允许服务仍等待。没有以白名单消除全部SEP调用、修复LS store、5秒启动或实机点亮的结果声明。

## 首次 minimal 实机回归及会话依赖修复

输入`out/nabu-logs-20261113/xnu2log-min.txt`，SHA256
`b7c02e7a10b214281b0424447fae72f6dcc62c89a80dfbd79c3f65667b9d7495`。
当前读到412759字节，slot seq9548，boot id d99c2740000000009024000000000000；
最大显示时间1204.785658802秒。`loginwindow`零次，显示始终on0/submitted0。
但并非没有门控记录：实际64条parked加1条上限记录。

原25F71 `System/Library/xpc/launchd.plist`内的定义明确为
`com.apple.loginwindow`，Program为loginwindow.app主程序、MultipleInstances=true。
初版已经放行此标签。没有证据支持把它改成猜测的
`com.apple.windowserver.session.loginwindow`，也不能把无exec等同为直接拒绝该标签。

直接阻塞证据：WS pid106/tid436在158.632739635秒发送call204、msgid47213，
remote1103/receive70b，端口接收任务此时仍是launchd，进入等待后未捕获返回。
303.162530416秒和452.971461406秒两次完整栈相同，cache slide=0x1134000：

```
confstr                                  0x180386028
  _dirhelper_internal                    0x18fd10868
    _dirhelper_userdir                   0x18fd11968
      __dirhelper_create_user_local      0x18fd10124
        mach_msg                         0x1804adfc0
          mach_msg_overwrite             0x1804b69c0
            mach_msg2_internal           0x1804c0574
              mach_msg2_trap              0x1804adc34
```

`__dirhelper_create_user_local`在0x18fd10120调用mach_msg，send/receive options=3、
send size0x18、receive limit0x2c、timeout0；它构造的请求ID为47213(0xb86d)。
同轮门控明确挂起pid90 `com.apple.bsd.dirhelper`；原job也确实发布同名MachService。
因此本轮WS被自己的白名单阻止了用户目录创建服务，尚未进入显示初始化。
这不是上一轮LS FileCache/mapdb等待的实机结果。

原缓存及本轮门控还证明pid91 `com.apple.logind`、pid95
`com.apple.mobile.usermanagerd`、pid33 `com.apple.UserEventAgent-System`被挂起。
这些分别提供登录会话、用户管理与系统事件代理。按原job定义补回它们及
`com.apple.UserEventAgent-LoginWindow`/`com.apple.UserEventAgent-Aqua`会话实例。
用户所写smd标签在该缓存中实际为`com.apple.xpc.smd`，pid32也已被挂起；
补实际标签、保留旧显式标签的兼容性，不使用前缀匹配。

修复只扩充这7个原版精确标签；其余拒绝策略、kernel hold及64条上限不变。
ASan/UBSan生产头测试增加独立的8标签回归表（包括既有loginwindow），
防止仅遍历生产数组的测试漏掉必要标签被删除。新内核/ESP仍需实机验证，
不能预先声称上述7项已组成完整依赖闭包或已经解除原来的LS等待。
