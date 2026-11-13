# 25F71：loginwindow / lsd 精确地址与 IPC 状态审计

实际执行 `python out/nabu-ls-address-audit/audit.py`，从本地原始 dyld cache 的 LC_SYMTAB 取符号，用 Capstone ARM64 反汇编，按 cache slide-info v5 解码导入指针、CFString 和 selector。脚本逐次验证指针映射的 version=5/value_add=0x180000000。cache UUID 46e0097ff38536c884a9a40d315a32d1 与 LS、DIR 两份原始快照一致。这里列出的地址已经 unslid，不再减 slide。完整逐函数指令及导入绑定位于 out/nabu-ls-address-audit/*.disasm；所有请求地址均在 symbols.json。

## 1. loginwindow 的实际调用与服务

| unslid 地址 | 精确函数 | 偏移 |
|---|---|---|
| `0x1847cb010` | `_FSURLCopyResourcePropertyForKeyInternal(__CFURL const*, __CFString const*, void*, void*, __CFError**, unsigned char)` | `0xe8` |
| `0x1847cc964` | `prepareValuesForBitmap(__CFURL const*, __FileCache*, _FilePropertyBitmap*, __CFError**)` | `0x1bc` |
| `0x180a9e200` | `LaunchServices::URLPropertyProvider::prepareValues(__CFURL const*, __FileCache*, __CFString const* const*, void const**, long, void const*, __CFError**)` | `0x248` |
| `0x180c8aa74` | `LaunchServices::URLPropertyProvider::prepareIsApplicationValue(LaunchServices::Database::Context&, objc_object objcproto23FSNodePropertyProviding*, __FileCache*, __CFString const*, LaunchServices::URLPropertyProvider::State*, NSError* __autoreleasing*)` | `0x68` |
| `0x180aee1d8` | `LaunchServices::Database::Context::_get(_LSDServiceDomain*, unsigned long)` | `0x6c` |
| `0x180b828d8` | `_LSContextInitCommon(LSContext*, _LSDServiceDomain*, unsigned long, NSError* __autoreleasing*)` | `0x29c` |
| `0x180a9f824` | `__LSCopyServerStore` | `0x320` |
| `0x181dc0ba8` | `__NSXPCDistantObjectSimpleMessageSend1` | `0x3c` |
| `0x181dc0c74` | `-[NSXPCConnection _sendSelector:withProxy:arg1:]` | `0x74` |
| `0x181db9634` | `-[NSXPCConnection _sendInvocation:orArguments:count:methodSignature:selector:withProxy:]` | `0x83c` |
| `0x181dbb5fc` | `___NSXPCCONNECTION_IS_WAITING_FOR_A_SYNCHRONOUS_REPLY__` | `0x10` |

`0x181dbb5fc` 是 Foundation 的同步 XPC 等待包装器，不是 LaunchServices 的 service-name 构造函数。其前一指令 0x181dbb5f8 调 `_xpc_connection_send_message_with_reply_sync`。

默认连接路径：`__LSCopyServerStore` 在 0x180a9f790 取 `_LSDReadService`，在 0x180a9f7a0 调 `__LSDServiceGetXPCConnection`；0x180a9f800 取 synchronousRemoteObjectProxyWithErrorHandler:，0x180a9f820 调 getServerStoreNonBlockingWithCompletionHandler:。connectionType=0；构造块 0x180bbfe7c 用 lightweightSystemService=0 调 serviceNameForConnectionType:lightweightSystemService:，再在 0x180bbfee4 调 initWithMachServiceName:options:。

名称方法 0x180b9d8c4 检查 getenv("LSD_SERVICE_BASE")，type0 经跳表到 0x180b9d918；无覆盖时走 0x180b9dd2c，lightweight=0 选择 CFString 0x1ede0a3b0。其磁盘指针经 slide-info 解码为字符地址 0x180cf4f5d，19 字节：`com.apple.lsd.mapdb`。lightweight=1 对应 `com.apple.lsd.system.mapdb`；本构造块显式传 0。本地 shim/config 未检出 LSD_SERVICE_BASE 设置，不能用静态结果声称读取了设备运行时环境。

原始 lsd daemon plist 的 Label=com.apple.lsd、ProgramArguments=[/usr/libexec/lsd,runAsRoot]，发布 com.apple.lsd.mapdb；agent 在用户域也发布同名服务。因此默认服务身份已确定，但同名不同 bootstrap 域加上当时无 receiver PID，不能仅凭名称把 port7703 的最终接收任务直接认定为 pid137。

DIR 实际请求：257.196692239s call5914→remote5d03/launchd(pid1)，成功；257.196890104s call5915→remote7703/local5a03，peer unknown/-1/0，发送返回0；257.196960156s call5916 等 receive5a03，无已记录完成。310.366s同tid1141仍在对应同步回复等待。不是 send-invalid-destination。

## 2. lsd 早期初始化

### lsd-outer

| unslid 地址 | 精确函数 | 偏移 |
|---|---|---|
| `0x180c72354` | `__LSServerMain` | `0x1d8` |
| `0x180c979e8` | `-[LSDBExecutionContext beginServerDBBootstrap:]` | `0x68` |
| `0x180c7273c` | `___seedDatabase_block_invoke` | `0x64` |
| `0x180b88a1c` | `-[LSDatabaseBuilder createAndSeedLocalDatabase:]` | `0x60` |
| `0x180b7f4a4` | `__LSDatabaseCreateFromPersistentStore` | `0x2cc` |
| `0x180b7f7ec` | `_LSDatabaseCreateFromPersistentStoreIgnoringRecoveryFile(int, bool, unsigned short*, NSError* __autoreleasing*)` | `0x110` |
| `0x180b7ffc4` | `_LSCreateEmptyStore(NSError* __autoreleasing*)` | `0x110` |
| `0x180b80004` | `_LSCreateEmptyStore(NSError* __autoreleasing*)` | `0x150` |
### lsd-dir-inner

| unslid 地址 | 精确函数 | 偏移 |
|---|---|---|
| `0x184831434` | `__CSGetConstStringForCharacters` | `0x70` |
| `0x18482dfc8` | `CSStore2::String::Create(CSStore2::Store&, char const*, unsigned int)` | `0x1a8` |
| `0x18482e784` | `CSStore2::HashMap<unsigned int, CSStore2::_StringCacheEntry, CSStore2::_StringFunctions, 0ull>::Create(CSStore2::Store const&, std::__1::unordered_map<unsigned int, CSStore2::_StringCacheEntry, std::__1::hash<unsigned int>, std::__1::equal_to<unsigned int>, std::__1::allocator<std::__1::pair<unsigned int const, CSStore2::_StringCacheEntry> > > const&, unsigned int, unsigned int, unsigned int*)` | `0x170` |
| `0x18481d6f0` | `std::__1::vector<CSStore2::HashMap<unsigned int, unsigned int, CSStore2::_IdentifierFunctions, 1ull>::KeyValuePair, std::__1::allocator<CSStore2::HashMap<unsigned int, unsigned int, CSStore2::_IdentifierFunctions, 1ull>::KeyValuePair> >::push_back[abi:nqn210106](CSStore2::HashMap<unsigned int, unsigned int, CSStore2::_IdentifierFunctions, 1ull>::KeyValuePair const&)` | `0x8c` |
| `0x1804a74a8` | `$_0::operator()() const::{lambda(unsigned long, std::__type_descriptor_t)#2}::__invoke(unsigned long, std::__type_descriptor_t)` | `0x10` |
### lsd-ls-inner

| unslid 地址 | 精确函数 | 偏移 |
|---|---|---|
| `0x1848336f4` | `_CSMapAddMapTable` | `0x78` |
| `0x18482750c` | `__CSStoreAddTable` | `0x94` |
| `0x18480d7f0` | `CSStore2::Store::getTable(NSString*) const` | `0xcc` |
| `0x18480ce2c` | `CSStore2::Store::enumerateTables(void ( block_pointer)(CSStore2::Table const*, bool*)) const` | `0x9c` |
| `0x18480cf68` | `CSStore2::Store::enumerateUnits(CSStore2::Table const*, void ( block_pointer)(CSStore2::Unit const*, bool*)) const` | `0xfc` |
| `0x180682268` | `__CFRelease` | `0x4c4` |
| `0x1802eae90` | `_malloc_zone_free` | `0x0` |

主干是服务器 DB bootstrap → 创建并播种本地数据库 → 从持久存储创建上下文 → `_LSCreateEmptyStore`。观察到 empty-store 分支只说明本次正在创建新 store；不能直接等价为磁盘损坏或读失败，入口也允许显式创建。

0x180b7ffc0 调 `_CSStringBindingStoreAddTable`，返回地址 0x180b7ffc4；0x180b80000 调 `__CSGetConstStringForCharacters`，返回地址 0x180b80004。它们遍历的是 cache 中固定的表定义和常量，不是直接在这两条指令里遍历 APFS 目录。

普通表（13）：Bundle, Claim, Service, Type, HandlerPref, Container, Alias, Plugin, ExtensionPoint, BindingList, PropertyList, LocalizedString, CanonicalString。

字符串绑定表（15）：UTIBinding, ExtensionBinding, OSTypeBinding, MIMEBinding, NSPasteboardBinding, DeviceModelCodeBinding, BluetoothVendorProductIDBinding, URLSchemeBinding, BundleIDBinding, BundleNameBinding, ActivityTypeBinding, PluginBundleIDBinding, PluginProtocolBinding, PluginUUIDBinding, ExtensionPointIDBinding。

播种常量（8）：com.apple.uniform-type-identifier, public.filename-extension, com.apple.ostype, public.mime-type, com.apple.nspboard-type, com.apple.device-model-code, public.bluetooth-vendor-product-id, public.url-scheme。

普通表循环 x21每次+0x10，与0xd0比较；绑定表 x24每次+0x20，与0x1e0比较；常量定义扫描至0x1e8，只对前8条非空字符串执行。`CSStore2::String::Create` 首次建立常量字符串 hash cache，`HashMap::Create` 将桶数限制在[16,8192]，预填10000个空槽。本次 x28=0x2000，实际匹配8192桶：每桶1槽，前1808桶补第2槽，共10000。用户给的0x18482e784正是 push_back 返回后的循环减计数指令。这里存在明确的有限工作量，不是从快照就能证明的无限重试。

本次 PC0x1804a74a8=`cbz x0,0x1804a74b4`。x0=0x5b6017c50非零，说明该次 operator_new_impl 已返回一个成功分配结果；不走 bad_alloc 分支。内层 operator_new_impl 的确在分配失败且有 new_handler 时会重试，但该寄存器现场没有停在重试分支。x1/x2/x16为调用后易失寄存器，不能把x2=0x3ff800解释成分配大小，也不能把x16=1解释成exit系统调用。

上次 malloc_zone_free 入口、LR __CFRelease+0x4c4 是表查找/枚举临时对象释放路径，记录并未显示某个 allocator 锁无限等待。

时间核对：LS lsd exec194.274445104s，第一栈196.661061614s（exec后2.387s）；DIR lsd exec198.524656614s，第一栈200.711089114s（exec后2.186s）。两次新进程在相近启动阶段命中同一初始化函数是复现证据，不是该函数持续150秒的计时。

原 native beginServerDBBootstrap 在0x180c979ac获取上下文+0x10锁，调用播种块，0x180c97a14释放。read-side invokeServiceInvocation:在0x180bbc078调用syncRead:，后者0x180c97214取同一上下文偏移的锁。默认 getServerStoreNonBlocking selector 跳过 willHandleInvocation: 中的 seed dispatch-group wait，但仍受这个读上下文入口保护。由此可明确 DB 初始化与读请求回复存在序列依赖；当前没有同一时刻服务端等待这个具体锁的栈，不能把结构性依赖当作已观测死锁环，也不能跳过读锁返回伪数据库。

## 3. 旧 LS round2 347.035772343s

| unslid 地址 | 精确函数 | 偏移 |
|---|---|---|
| `0x18031e0a8` | `__malloc_zone_malloc` | `0x70` |
| `0x18031eee8` | `_reallocf` | `0x20` |
| `0x18031a11c` | `_xzm_malloc_zone_malloc_type_malloc` | `0x0` |
| `0x1803d10a0` | `__filldir` | `0x254` |
| `0x18038682c` | `___opendir_common` | `0x164` |
| `0x1803d0d50` | `___opendir2` | `0x2c` |
| `0x180548130` | `__CFIterateDirectory` | `0x58` |

0x18031e09c从zone+0xa0加载typed allocator回调到x3；0x18031e0a8从LR生成type信息，0x18031e0b0 braa x3。现场 x3 unslid0x18031a11c恰是_xzm_malloc_zone_malloc_type_malloc，x0是zone、x1=0x4000是该次16384B请求。它不是RPC、轮询等待或未知系统调用。0x18031eee8是reallocf调用realloc后的返回地址。

父FP链记录为__filldir → __opendir_common → __opendir2 → __CFIterateDirectory；下一帧return=7终止。工具使用live-unsuspended，寄存器和后续FP读取不能视为原子一致：__filldir+0x254实际上是free返回点，不是reallocf返回点；必须保留这一竞争/截断限制。

__filldir在0x1803d0efc调用reallocf增加目录读取缓冲，在0x1803d0f38调用___getdirentries64，0x1803d0f4c在读取非零字节时继续循环，直到真正EOF。347s仍在目录枚举相关工作是已见事实；不能从相隔150.375s的两个不同PC推导出malloc或_LSCreateEmptyStore执行了150秒，无法据此定位重复目录、错误cookie或特定APFS/shim分支。

此前已修的共享union fallback同mount进展缺陷，位于platform/nabu/macos26/desktop/directory-progress.patch；成品CI43426129929已包含其真实机器码。但用户DIR实机仍为黑屏，且无分支命中记录，所以它不是已验证的桌面根因修复。不得再次把它原样重新打包称为新修复。

## 4. peer unknown源码与本轮补丁

`nabu_session_peer_snapshot()`先查当前任务IPC entry，接受SEND/SEND_ONCE/RECEIVE，非阻塞持有space/port锁调用原生ipc_port_get_receiver_task_locked。没有msgid过滤，也不按XPC消息类型跳过。未找到right输出invalid-name；锁忙输出space-busy/port-busy，均不是unknown。

原生helper只对普通receiver space给PID；ip_get_receiver_name只对in-space状态返回名字。按XNU状态不变量，合法active端口上的unknown/-1/0表明receive right当时不在space中，候选为in-transit或in-limbo。原探针把它们都折叠为unknown，现有记录不能再区分两者。对launchd的5d03与对XPC endpoint的7703是不同端口，因此前者有pid1而后者无PID是接收权利状态差异，不是1999646836和1073741824两个msgid的解析分支不同。

本轮修改session_ipc.h：在已有port锁内分别记录in-transit/in-limbo/kernel-space/reply-space/receiver-task-unavailable，不额外追逐ip_destination、不增加等待锁、不伪造PID。tests/nabu_session_ipc.c覆盖这些实际分支；执行python -m pytest -q tests/test_nabu_session_ipc.py，3项通过，含ASan/UBSan真实头文件harness、旧EVI调用回放及原XNU patch应用检查。这是观测缺陷修复，未提交新CI、未生成新的ESP、没有宣称桌面修复。

## 交付边界

本轮完成所列全部地址的精确符号化和逐函数反汇编，补齐静态服务名、建表循环边界、allocator现场、目录读取循环、端口状态解释。确定的是loginwindow同步LS store回复未返回；尚未确定令服务端长期不能完成的实际运行分支。现有证据不足以推出唯一功能补丁，强行改malloc循环、无锁返回LS store或强迫swap成功会改变正确的原生语义，不能作为已验证修复交付。
