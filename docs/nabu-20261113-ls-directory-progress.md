# LS seq9081 全文复核与目录枚举进展修复

本轮仍未验证登录桌面。可确认的阻塞是 loginwindow 的同步 LaunchServices store 请求未返回；新定位的 lsd 第二轮操作是 libc 目录枚举。已修复并本地回放一个该路径上的真实 union 进展缺陷，但没有捕获实机目录名或该分支命中，不能宣称唯一根因。

## 输入、完整性及全文覆盖

从原始 `out/nabu-logs-20261113/XNU2LOG-ls.BIN` 重新按 header/payload CRC 验证，输出 `verified-ls.txt` 与 `verified-ls-info.json`。两个槽有效，选 slot0 seq9081；正文1,099,071字节，SHA256 `0b46581d16b9b897a3b8e31c922dc21ca4f2cde441bf52fdd7baa8a704aab6bf`，5706个LF，未覆写，捕获锁竞争丢1130字节。原TXT有解码器头和CR，不能用splitlines的5852行替代原物理LF编号；正文下文行号按LF计。max t_ns=378115985520。不是完整无损启动记录，也不是4MB写满。

逐行统计/IPC配对/全部错误行在 `ls-full-census.json`；其余行逐行编号在 `ls-remaining-lines-numbered.txt`。主前缀分类覆盖5707项（含末尾空项）；进一步扫描交错前缀识别2866条SESSION、1145条RAWSTACK、252条OD-WAIT、82条DISPLAY、39条PROC事件。12个进程/轮次快照都有结束标记，其中10个有匹配cache UUID，2个早期快照缺cache身份，保留未解析，不借用其他轮slide。

内核版本 Fri Nov13 11:50:18 UTC2026 对应已交付CI43418644720；v7和新per-thread IPC epoch实际出现。用户态包含cpus=8。完整提交/原始源hash与上轮成品二进制校验记录保存在build-plan及delivery-verification；本日志无runtime bulk guard命中计数，不能证明该guard被执行。

## 显示与其他启动路径

230.293129791s display on；271.952427395s首swap。272.259、272.586、277.887s三个1600x2560 BGRA帧都source-copy/scanout-copy/native-complete成功，但4,096,000个像素RGB全0、alpha均非0，centre=0xff000000。Layer1/2明确state=clear、surface_id=0；没有丢失一个已存在overlay的证据。

280.402s第4个请求被原dropNoOpSwaps_gated退休，queue_head=0、last_displayed_id=4（正文4238–4242）。4 submitted /3 pixel copies不代表第三帧耗尽后不能提交4。WindowServer106继续IPC至378s；315.419s主线程570栈在SkyLight CGX服务接收循环，不在native framebuffer等待。type3/type5/held均0，不能强造通知或改current_request指针。

28条kauth-result均status0；126次OD-WAIT return均result0，最大5.405399062s。未见NABU-PANIC-1或本轮lsd高CPU警报。仍有endpointsecurityd45在209.394s exit35并重启、digest-service在217.261/321.981s exit1、contextstored AppleKeyStore MACF拒绝、dirs_cleaner rename/rmdir EPERM；这些分别列在census，现有栈没有把它们连接到当前FileCache/LS阻塞。

## loginwindow 的确定等待及证据边界

305.484776197s loginwindow110主线程541：

`___ulock_wait2 → __os_unfair_lock_lock_slow → _FileCacheLockTransitionToPreparer → _FSURLCopyResourcePropertyForKeyInternal → CFURL/NSURL → AppKit NSSafeAperture → NSApplication init`。

x1/x22=0x102b81e58是实际等待锁地址，x2/x23=0x4e02是带状态的owner word，可归一为目标进程命名空间内的thread port name0x4e03。RAWSTACK打印的port2307/2563是**取栈工具命名空间内**的Mach权利名，当前日志未给出目标loginwindow里0x4e03到tid1294的映射。不能把它直接称为已证实的tid1294。worker的FileCache对象地址也没有从深层x19等寄存器直接获得；它们已被中间调用保存/复用。

worker1294的精确栈为mach_msg2→dispatch_mach_send_and_wait_for_reply→libxpc同步回复→Foundation NSXPC→__LSCopyServerStore+0x320→LSContext→URLPropertyProvider prepareIsApplicationValue/prepareValues→prepareValuesForBitmap→FSURL+0xe8。方法/默认服务静态路由仍是_LSDReadService / getServerStoreNonBlockingWithCompletionHandler: / com.apple.lsd.mapdb。

这轮确实补齐了最后的请求配对（正文4069–4075）：6544请求launchd并成功；6545发送launchd且成功；6546向remote7b07发送XPC并返回0；6547在259.866235104s开始等receive7d03，未记录完成。305.485s的同tid trap参数x5=0x7d0300000000对应同一receive。可确认在该快照时仍等待，距入口45.618541093s；不能外推到347s或永久等待。整个loginwindow共264个已见call，只有6547未配到完成。

6546入口的peer=unknown、peer_pid=-1、peer_receive=0，原生ipc_port_get_receiver_task_locked对未处在普通接收space的port无法给PID。默认服务与lsd138的root服务身份使它成为明确候选，但该行没有直接捕获receiver_pid138。6544/6545也未记录bootstrap消息体，不能从msgid单独声称已经解码了服务字符串。

主tid541在253.956s确实达到epoch-byte-bound；它此前196个入口全部配到结果，worker1294有独立预算且未耗尽。主线程随后在305s ulock等待，所以没有新的Mach入口触发新epoch是合理情况。未放开成无限日志洪水。

## lsd 两轮精确符号及目录读取链

完整地址/符号/原始寄存器在 `ls-exact-stacks.json` 和 `ls-exact-stacks.txt`，两轮都UUID46e0097ff38536c884a9a40d315a32d1，slide0xf5d0000。

196.661061614s，lsd138主tid821，pc1802eae90=malloc_zone_free，lr180682268=__CFRelease+0x4c4。保留的canonical父帧依次是CSStore2::Store::enumerateUnits/enumerateTables/getTable→CSStoreAddTable→CSMapAddMapTable→_LSCreateEmptyStore→_LSDatabaseCreateFromPersistentStoreIgnoringRecoveryFile→_LSDatabaseCreateFromPersistentStore→LSDatabaseBuilder createAndSeedLocalDatabase→seedDatabase block→LSDBExecutionContext beginServerDBBootstrap→_LSServerMain。这是数据库启动/建表链。首个PAC原始返回未猜掩码，标记无法解析。

347.035772343s，同tid821，pc18031e0a8=__malloc_zone_malloc+0x70，lr18031eee8=reallocf+0x20；记录的parent FP链为__filldir+0x254→__opendir_common+0x164→__opendir2+0x2c→__CFIterateDirectory+0x58，下一返回0x7无效。live-unsuspended帧读取会竞争，不能忽略这处截断或把parent链当作完整原子栈。

精确原cache __filldir机器码在 `ls-filldir.disasm`：1803d0f38经stub1803f900c调用原___getdirentries64(1804af9bc)，syscall344。它在读到非零目录字节时继续填充/必要时reallocf，直到真实EOF；这是**普通目录读取**而不是getattrlistbulk。因此上一包仅bulk的进展修复没有覆盖该API。两个快照落在不同操作上，不证明150秒同一指令死循环；本轮没有该时段连续CPU统计。16KB分配也不证明无限内存膨胀。

## 已复现缺陷及最小功能修复

匹配CI源码getdirentries_common在EOF且MNT_UNION时调用lookup_traverse_union，若后者跟随lower symlink回相同mount，仍会ref/open新vnode、close旧vnode、fg_offset=0、重试unionread。空目录可在单syscall内循环；非空目录可每次EOF重读首批目录项，令libc填充永不抵达EOF。getdirentriesattr也共享该helper。它们不走bulk-progress.patch。

新增 `platform/nabu/macos26/desktop/directory-progress.patch` 在helper的non-root namei成功分支检查相同v_mount，沿用原非目录拒绝的vnode_put/ENOENT路径；与SHENG普通lookup已有同mount保护一致。既有root->covered branch、真正不同mount lower目录、lookup错误、名称过长/缺失、vnode引用错误保持原语义。补丁独立追加，不改固定历史manifest或iOS ABI；原bulk guard保留。

`tests/test_nabu_directory_progress.py` 从固定XNU对象ac9718f加真实lower-directory/integrated/session-filesystem补丁重建，抽取完整helper与getdirentries_common，在修前/修后各执行10场景并启用ASan/UBSan。修前复现same vnode/same mount不同vnode的空目录重复；非空重复首批。修后一次EOF且不ref/open/close替换。正常lower、namei错误、非目录、root covered成功/失败和缺失name均检查。backend第9次返回ELOOP仅是fixture防失控，不是生产内核新增重试上限。

完整source prepare通过；源码及元数据hash匹配。此修复针对已复现的代码缺陷；**仍需真实目录路径/回退命中，才能把它认定为这次lsd慢初始化的原因**。不会用符号化快照假装捕获了syscall或死锁环。

参考：SHENG UniXNU dff276535db3ac394f628cec58a718cd46c20ac2 docs/porting/{macos,display,gpu-diagnostics}.md及已导入的普通lookup保护；用户NABUiOS16 a7148554052f42159207245e2070b96492a046da/HakiOS96ffbc532e970f3540885755d457e401e04d4707。图形地址/ABI严格按25F71，当前修改属于macOS union VFS，未把iOS16显示接口移植到此处。
