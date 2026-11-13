# NABU LaunchServices 与 FileCache 核查及修复候选

## 当前实机证据

EVI的lsd216/tid1017高CPU总量90.997552s(user34.509751/system56.487801)，没有该线程栈或系统调用分布。system还包含NABU的EL0 PAC/ARMv8.2指令仿真异常成本，不能全部算成某个syscall。v6的lsd137首轮214.646s快照主线程位于Foundation __NSNewStringByAppendingStrings+0x344，当前指令与有限string-count循环有进展，FP后续失效；未捕获bulk循环，不能把静态缺陷冒充该次高CPU唯一根因。

v6 loginwindow108在308.576s有如下匹配UUID的栈：主线程558在___ulock_wait2→__os_unfair_lock_lock_slow→_FileCacheLockTransitionToPreparer(FileCache+0x28 recursive unfair lock)；调用者是NSApplication init里的NSSafeAperture URL资源属性查询。不是pthread_mutex、HIToolbox或CarbonCore。worker1243通过Foundation NSXPC和LaunchServices同步等待，且位于同一FSURL准备函数分支；该分支持有其自身FileCache preparer锁跨RPC，但两线程FileCache对象地址/锁owner未捕获，不能宣布形成死锁环。worker现有最后IPC早于raw41.39s，266s给coreservicesd的10019已成功、267s给notifyd的1012也已成功；不能把其中任意对端移植成308s同步请求对端。

EVI的403s msg30176已精确定位为SLSGetAppearanceThemeLegacy；v6的AppKit FileCache栈是新的更晚初始化位置，不是该旧RPC的同一现场。原生命周期选择名单未含SecurityAgent/lsd/authd/launchservicesd，且输出依赖console；不存在SecurityAgent exec记录不证明其未启动。ViewBridge是框架名也不是通用必启动进程断言。

## 真实功能缺陷与最小修复

匹配已交付EVI内核的getattrlistbulk在lower union lookup成功后无进展检查就fg_offset=0并重试。若覆盖路径解析回相同mount，则空目录可在syscall内循环，非空目录可重复首批目录项。普通lookup已有SHENG同mount guard。新增desktop/bulk-progress.patch将相同mount回退当作无lower目录的普通EOF，放在MAC检查/引用和fd替换之前，沿用原vnode_put清理。保留正常lower遍历、MAC/授权失败、ref失败、snapshot和ERANGE语义。

测试从固定XNU Git对象ac9718f及原build patch顺序重建生产vfs_attrlist，抽取真正zero-offset及union控制体，对修前/修后各跑12情形：修前复现empty循环/nonempty重读，修后单次EOF返回。实际root镜像Cryptex symlink已核，但当前bindfs路径可能终止于另一mount，因此该bug的可复现不等同证明EVI目录实际命中。

csops所在kern_proc.c及mac_syscall所在mac_base.c与固定原版源码字节一致，原机器码仍分派原检查及错误码。未找到本地返回假成功引起这两调用无限重试的shim。APFS私有函数的cookie和EOF行为未获实机证明，不擅自替换。

## 已修复的观测漏洞

loginwindow入口/完成原本分别共享256记录后1/sec预算，其他tid可吃掉主tid返回，导致403s之后无记录无法解释。现在按tid和call预留entry/receive/final-correction容量，已准入完成不再采样；每60s明确epoch，开发late phase400s更新额度，额度不足有drop，下epoch报告完整丢弃计数。没有把1/sec放开成无限洪水；WS/OD采样保持。

新增lifecycle-diagnostics.patch选入SecurityAgent/authd/lsd/launchservicesd，xpcproxy label仅取自原25F71plist，所有生命周期记录统一单行directRAM，console释放后仍可见。没有假设ViewBridge label。

bootshim v7保留默认raw-only，加入coreservicesd/launchservicesd可选三轮及x0..x28真实原始寄存器；不读取未经验证的私有锁对象，不暂停目标，不伪造owner。

## 集成和交付

两个新desktop patch都位于integrated.patch和session-filesystem.patch之后，不更改历史固定patch和manifest。完整ordinary ARM64 source prepare为out/nabu-ls-filecache-repair/source-prepare；17定向tests及17subtests通过。需Intel完整ARM64内核CI并重新打包ESP，不能只把v6启动工具包换名字。真机尚未验证高CPU或登录桌面已修复。

参考：SHENG27.3 UniXNU dff276535db3ac394f628cec58a718cd46c20ac2 docs/porting/{display,macos,gpu-diagnostics}.md；已导入regularlookup same-mount修复；用户NABUiOS16 a7148554052f42159207245e2070b96492a046da/HakiOS96ffbc532e970f3540885755d457e401e04d4707的原始栈/原生显示完整生命周期思路，地址和ABI仍按25F71匹配。

进一步静态定位：worker的同步NSXPC方法是`_LSDReadService / getServerStoreNonBlockingWithCompletionHandler:`，默认服务`com.apple.lsd.mapdb`（`LSD_SERVICE_BASE`可覆盖、root/user domain未捕获）。原agent/daemon均发布此MachService。方法跳过显式seeding-group等待，但在执行前仍进入LSDBExecutionContext syncRead和CoreServicesStore读访问；handler会回实际db、错误或blockingfetch endpoint，没有支持伪造成功或去锁的证据。精确原机码/路由见`.work/nabu-display-init-20261114/v6-lsd-mapdb-route-audit.md`。


## Minimal GUI Boot：精确调用链补充与服务隔离

本轮cache UUID46e0097ff38536c884a9a40d315a32d1，所有以下地址均unslid。完整指令和表定义分别见docs/nabu-20261113-exact-lsd-address-audit.md及docs/nabu-20261113-lsd-seeding-path-plan.md。

loginwindow worker：FSURLCopyResourcePropertyForKeyInternal(1847cb010) → prepareValuesForBitmap(1847cc964) → URLPropertyProvider::prepareValues(180a9e200) → prepareIsApplicationValue(180c8aa74) → Database::Context::_get(180aee1d8) → LSContextInitCommon(180b828d8) → LSCopyServerStore(180a9f824) → NSXPCDistantObjectSimpleMessageSend1(181dc0ba8) → NSXPCConnection _sendSelector(181dc0c74) → _sendInvocation(181db9634) → NSXPCCONNECTION_IS_WAITING_FOR_A_SYNCHRONOUS_REPLY(181dbb5fc)。后四帧属于Foundation。默认服务由_LSDReadService.connectionType=0、lightweight=0解析到com.apple.lsd.mapdb，方法getServerStoreNonBlockingWithCompletionHandler:。DIR call5915发送成功，5916在receive5a03等待回复。peer unknown/-1/0未记录最终接收任务PID，不能直接冒充pid137。

lsd早期：LSServerMain(180c72354) → LSDBExecutionContext beginServerDBBootstrap(180c979e8) → seedDatabase block(180c7273c) → LSDatabaseBuilder createAndSeedLocalDatabase(180b88a1c) → LSDatabaseCreateFromPersistentStore(180b7f4a4) → IgnoringRecoveryFile(180b7f7ec) → LSCreateEmptyStore(180b7ffc4/180b80004)。末端分别是CSStringBindingStoreAddTable/CSGetConstStringForCharacters，固定13普通表、15绑定表和8常量字符串，不等于直接扫描整个System。

LS旧round2 347.035772343s：PC18031e0a8=__malloc_zone_malloc+70，LR18031eee8=reallocf+20，x3 unslid18031a11c为typed allocator回调；父FP记录__filldir(1803d10a0) → __opendir_common(18038682c) → __opendir2(1803d0d50) → CFIterateDirectory(180548130) → return7。x20=0/x21=4000与reallocf(NULL,16384)吻合；live-unsuspended父帧存在返回点不一致，未捕获目录路径和fd内容，不能宣称连续150秒同一函数无限扩容。

createAndSeedLocalDatabase在180b88a50发布真实server database，读取seeded/complete标志；未播种时先同步处理插件，然后180b88e40通过dispatch_channel_async调度block180b88f6c中的后续目录注册。不能把全部异步扫描耗时直接等同为bootstrap同步锁持有时间。当前packed Data seed persistent=false且无csstore，默认root store路径为/private/var/folders/zz/zyxvpxvq6csfxvn_n0000000000000/0/com.apple.LaunchServices.dv/com.apple.LaunchServices-20971544-v2.csstore。

用户授权的Minimal GUI Boot是独立、默认关闭的服务隔离试验：boot-arg nabu-minimal-boot=1仅放行minimal_boot.h所列27个精确job label（25个指定label和两个原版SecurityAgent别名）；其余xpcproxy在原生exec成功加载后、用户态运行前施加独立内核task hold。父spawn继续完成，代理不正常退出，避免用ENOENT/exit触发KeepAlive重拉；原生user resume不能消耗该kernel hold。此策略不改变Mach RPC成功值、LS数据库内容或原生显示完成语义。明确可能使白名单服务对被屏蔽服务的请求等待；需要实机验证依赖是否足够，不宣称已解决FileCache/LS根因或5秒进桌面。直接执行且不经xpcproxy的服务不由此门控拦截。
