# 25F71 LS 持久化路径、播种阶段及可执行修复路线

本轮实际执行离线 cache 反汇编、Data seed APFS目录清单检查及 root身份解析。没有改设备、根镜像、原Data seed或原日志，没有发布未经验证的新ESP。

## 数据库读取路径

数据库入口使用本地 LaunchServices image（unslid base0x180a90000）；用户提到的0x181d9e000在这个cache是Foundation。先前地址全部按匹配UUID46e0097ff38536c884a9a40d315a32d1解析，不混用其他build。

调用路径：

```
_LSDatabaseCreateFromPersistentStore (180b7f1d8)
  → _LSDatabaseCreateFromPersistentStoreIgnoringRecoveryFile (180b7f6dc)
  → _LSGetStoreNode (180b84878 / once block180b884e4)
  → [_LSDefaults databaseStoreFileURL]
  → databaseStoreFileURLWithUID: (180b9cff4)
  → databaseContainerURLWithUID: (180b9cff0)
  → dataVaultURLWithUID: (180b9e3fc)
  → __user_local_dirname(uid,0,buf,1024) (18fd0ed68)
```

dataVault方法在180b9e440调用__user_local_dirname，追加`com.apple.LaunchServices.dv`，用rootless_mkdir_datavault创建真实目录。文件名方法读取环境变量`LS_NAME_PREFIX`，默认`com.apple.LaunchServices`；格式为`%s-%03llu-v2.csstore`，schema=0x1400018=20971544。

从原data.dbr的root.plist只读取uid/generateduid，未读取或输出密码字段。uid0/generateduid=FFFFEEEE-DDDD-CCCC-BBBB-AAAA00000000。按实际libsystem_coreservices函数的UUID+big-endian UID编码，32字符为zzzyxvpxvq6csfxvn_n0000000000000，因此默认路径：

```
/var/folders/zz/zyxvpxvq6csfxvn_n0000000000000/0/com.apple.LaunchServices.dv/com.apple.LaunchServices-20971544-v2.csstore
```

`/private/var`是该路径的真实系统布局别名。System内容快照位于同一目录，文件名前加`SystemDataOnly-`。此路径依据当前seed身份及默认环境计算，并非从347秒进程fd表读取。设备若更改账号UUID、代理UID或LS_NAME_PREFIX，应重新计算。

PersistentStore函数先尝试原文件读取，失败且flags允许时走create-empty回退；用户列出的180b7f4a4正是第二次允许创建调用的返回位置。它强烈说明该次未直接复用现成store，但具体失败errno仍没有打印。`dbRemoveDBOnStartupURL`恢复标志也可触发重建，不能把所有create-empty都归结为相同磁盘错误。

当前打包器使用out/nabu-desktop-data/data.dbr，其SHA256=a5c9bd38b9bf83bf6d28eb0e8d385a91ab9e47403eb997ec4d4b86c2decc7630，build.json明确persistent=false、logical3GiB。对其离线稀疏展开副本执行7z只读清单，共9051个Path条目（含archive项），没有.csstore，private/var/folders下没有已播种缓存。因此“每次从当前seed重启没有现成LS数据库”得到包级证据。seed含模板目录和账号，不是完全空白Data。

## 扫描来源及同步/异步边界

本cache没有命名为_LSGetDatabaseSystemSeedingPaths的符号；实际范围由LSDatabaseBuilder的searchPathsForDomain:/addPathsForDomain:toOrderedSet:/findAndRegisterApplicationBundles:建立。

searchPathsForDomain在180b89af4取6项固定定义，调用sysdir_start_search_path_enumeration_private，再依次sysdir_get_next_search_path_enumeration。定义为：

| sysdir directory ID | 含义 | 后缀 |
|---|---|---|
| 10 | CoreService | 无 |
| 1 | Application | 无 |
| 3 | DeveloperApplication | 无 |
| 5 | Library | ScriptingAdditions |
| 5 | Library | Services |
| 5 | Library | Input Methods |

3、10仅对domain2或8枚举，Library/Services也有相同域限制。findAndRegisterApplicationBundles按domain8(system)、2(local)、1(user)、0x40及0x1000（私有domain，未猜运行路径）收集；网络domain4单独扫描。sysdir最终路径依domain/系统布局动态返回，不能把候选列表等同于设备实际打开列表。

显式追加的三个路径：

```
/System/Applications
/System/Cryptexes/App/System/Applications
/System/Cryptexes/App/System/Library/CoreServices
```

OSModule扫描以/System/Volumes/Data为卷标识调用LSEnumerateCryptexGraftsOnVolume，graft下面仅追加Applications/和System/Library/CoreServices。这不是递归扫描整个Data根。

ApplyScanPath在180b8a4ec调用ScanURL，在允许继续时180b8a574调用_XCFURLEnumerate；已维护已扫描路径set。没有在已反汇编的路径构造函数中发现可配置“仅CoreServices”的env/defaults开关。LS_NAME_PREFIX仅改数据库文件名；LS_NO_PERSISTENT_PREFS用于偏好持久化，不是扫描白名单，LSD_SERVICE_BASE改服务名前缀，不应误用。

关键阶段：

```
createAndSeedLocalDatabase:
  180b88a18 创建/读取真实数据库
  180b88a50 setServerDatabase:
  180b88a60/6c 检查isSeeded/isSeedingComplete
  已播种：180b88a88跳过后续初始化
  新库：同步注册extensionEnumerator的插件
       180b88d04保存SystemDataOnly数据库
       180b88e40 dispatch_channel_async(builder.ioQueue, block180b88f6c)
  返回beginServerDBBootstrap，再释放上下文锁

异步block180b88f6c:
  syncWrite(registerLibraries)
  findAndRegisterApplicationBundles
  scanAndRegisterOSModuleContent
  performAsyncWrite(...)
  syncWithMI
  remotePlaceholder、network、Spotlight
  最后提交真实seeding状态及保存
```

所以不能把整个异步播种耗时直接算作beginServerDBBootstrap同步锁持有时间。异步注册的局部写事务仍可能阻挡读取，但旧347秒截断栈不能证明该时刻处于哪个事务。

## 347秒寄存器与每一帧

| 记录 | unslid | 函数/含义 |
|---|---|---|
| PC |18031e0a8|__malloc_zone_malloc+0x70|
| LR |18031eee8|reallocf+0x20|
| x3 |18031a11c|xzm_malloc_zone_malloc_type_malloc回调|
| frame0 |1803d10a0|__filldir+0x254|
| frame1 |18038682c|__opendir_common+0x164|
| frame2 |1803d0d50|__opendir2+0x2c|
| frame3 |180548130|__CFIterateDirectory+0x58|
| frame4 |7|无效，采集结束|

x0=0x102e20000是malloc zone；x1=0x4000是该次分配大小；x20=0和x21=0x4000对应reallocf入口保存的旧指针NULL/大小16KiB。与第一块目录缓冲分配吻合，不是已捕获持续realloc膨胀。x22=3等值已被allocator复用，不能当作fd3。

这条FP链还存在具体不一致：__filldir+0x254是free返回点，其reallocf返回应为+0xb4；__opendir2+0x2c是open返回点，调用opendir_common的返回应为+0x44。工具明确live-unsuspended，因此寄存器与逐次FP读取会竞争，不应把混合现场描述成完整原子栈。

CFIterateDirectory在180548118将路径转换到自身stack+0x8de，接着opendir。若日志保存的该父FP0x16d400e20仍匹配这次函数实例，则buffer地址可算为0x16d4009be；原日志没有该地址的内存字节，也没有DIR对象内容、fd表/vnode路径，无法从日志恢复目录名或完整更外层LaunchServices调用者。这一缺失不能靠反汇编补出。

## 可执行路线及验收

优先路线是**复用原生已播种store**，不向VFS伪造EOF：

1. 用当前25F71、当前SystemOS/App挂载及相同账号身份完成一次原生LS建库，获取上述真实数据库及对应SystemDataOnly文件；必须验证native API可读取且isSeeded/isSeedingComplete为真。其他OS/build/用户名下的任意csstore不合格。
2. 保存原生目录所有权、权限和DataVault相关元数据，将该目录并入一个新的Data seed离线副本，保持原volume UUID和账号generateduid。原seed保留。
3. 从该离线副本重新打包ESP；匹配当前原代码的180b88a60/6c检查通过后，180b88a88直接走已播种分支，不调度整轮初始扫描。这个分支是真实二进制快路；不保证介质读取和全部bootstrap耗时5秒。
4. 仅保留packed RAM seed无法保存本次启动产生的新库；若要后续变更自动持久化，需要有界、经过校验的cache导出/导入或真实持久Data存储，不能声称当前打包器已经实现。

目前本地seed没有一个合法已播种store，Linux不能执行macOS框架生成它。无可用源文件时不填充伪数据库、不称已经生成预播种修复包。

备选A：若必须实验缩范围，切点应在用户态LSDatabaseBuilder::findAndRegisterApplicationBundles/ApplyScanPath的路径集合，而不是getattrlistbulk/lookup对lsd隐藏全局文件系统。可将初始目标集合显式限定/System/Library/CoreServices并保留原生bundle注册/数据库提交，再延后其他注册；必须审计该方法之外的registerLibraries、插件和MI同步，并保留未扫描状态，不能简单提前setSeedingComplete(true)。这不是现有环境变量支持的配置，需要签名和ABI匹配的用户态实现或精确补丁。当前没有证据能保证此改动解除既有同步XPC等待，因此未盲改原cache或构造可刷包。

备选B：现有日志的地址映射和可识别数据已全部提取，目录路径和fd信息没有被捕获。上述条件路径缓冲地址只是下一次取证切点，不是假称现有日志已经包含路径。

5秒验收必须记录同一启动内beginServerDBBootstrap进入/返回及store请求回复，确认时间差≤5秒，并验证loginwindow建立窗口。现在没有这些计时数据，不能将性能目标写成完成结果。

## 复现与参考

执行`python out/nabu-ls-address-audit/seeding-evidence.py`，输出seeding-evidence.json、database-paths.disasm、database-containers.disasm、seeding-phases.disasm、apply-scan-path.disasm、user-directory.disasm。脚本仅对离线展开副本取目录清单和账号uid/UUID；不写原Data。

参考SHENG27.3 dff2765（最新7d85ede指南未改）docs/porting/macos.md的Data seed/UUID/账号保留要求；用户NABUiOS16/HakiOS参考涉及动态持久Data，与当前persistent=false实现不同，不能把它们的持久化能力当成此包已具备，也不移植iOS16的mobile容器路径到macOS25F71。
