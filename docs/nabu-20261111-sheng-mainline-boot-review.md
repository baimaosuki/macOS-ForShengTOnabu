# NABU 26.5：按 sheng 27.3 主线核对启动链

本次目标保持 NABU / SM8150 / macOS 26.5 / 25F71。参考 sheng 的初始化、根盘和 macOS 启动机制；硬件资源、内核 ABI、IPSW 和卷身份使用 NABU / 25F71 的实际输入。

## 1. 参考版本和必读文档

本次读取的主线快照：

| 仓库 | 固定版本 |
| --- | --- |
| UniXNU | `268bfa1c587db5ee616e955cdfd97a281500a6fb` |
| UniXNU-sheng | `81f43ebaaa3317a5917b76093f7bc44c4949bc02` |
| sheng 引用的 ShengUfs | `2f6c7b0d48a37ffd54943cf13d6709bf66564045` |

原文保存在 `.work/sheng273-reference/`。本地旧 UniXNU checkout 不能代表这些主线版本。

已读 Porting 总入口、steps、device-repository、UFS、macOS、drivers、components、bring-up、records 和 log-capture。指南确实存在；sheng 的 AGENTS.md 明确要求每次移植先读 Porting、设备仓格式和组件目录，存储任务另读 UFS。

关键参考：

- [移植总入口](https://unixnu.com/porting/)
- [UFS 指南](https://unixnu.com/porting/ufs)
- [macOS 指南](https://unixnu.com/porting/macos)
- [驱动初始化与 ABI 集成](https://unixnu.com/porting/drivers#build-integration)
- [sheng SourceStorage.cpp 固定版本](https://github.com/UniXNU/UniXNU-sheng/blob/81f43ebaaa3317a5917b76093f7bc44c4949bc02/package/SourceStorage.cpp)
- [vfsinit 后调用补丁](https://github.com/UniXNU/UniXNU-sheng/blob/81f43ebaaa3317a5917b76093f7bc44c4949bc02/patches/xnu-12377/source-storage-runtime.patch)
- [27.3 profile 固定版本](https://github.com/UniXNU/UniXNU/blob/268bfa1c587db5ee616e955cdfd97a281500a6fb/profiles/os/macos-27.3-26C5035r/profile.toml)

主线某条代码已提交，不等于该条代码已有真机验证。下文分别标明源码、宿主产物和设备证据。

## 2. 先纠正根盘和 UUID 判断

2026-11-11 重新直接读取 `out/nabu-desktop-root/root_image.bin`，不是只读 build.json。

| 窗口 | +0x20 | NXSB Fletcher 校验 | 容器块大小 × 数量 | APSB 中的卷 UUID |
| --- | --- | --- | --- | --- |
| OS | NXSB | 通过 | 4096 × 3191296 | 47719F50-5527-44A3-9F59-8170528D8C14 |
| SystemOS | NXSB | 通过 | 4096 × 1732608 | C7DFE43C-0A23-43F5-B3E2-471BEAC4539D |
| App | NXSB | 通过 | 4096 × 12800 | F894E20A-CE25-41F4-ABE2-C00E9E922B83 |

各容器声明长度与窗口大小一致。窗口末尾未检测到 UDIF 的 koly 尾标。记录保存在 `out/nabu-apfs-stage-trace/root-format-recheck.json`，卷身份另存于 `on-disk-volume-identities.json`。

APFS 对象头前 8 字节是校验值，NXSB 在 +0x20。因此 `0a16fe47` 等首四字节不能证明窗口装入了压缩 UDIF。三窗口已经包含可验证的 APFS 容器头；这不证明所有元数据都完好或一定能挂载，但排除了此前那条“只因首四字节不是 NXSB 就必须重新解包”的推论。

主线 27.3 的 OS UUID 是 `84581E99-85BE-4957-AC53-2947E887B3E4`，SystemOS 是 `6678E466-2F88-4C56-9408-DC7361BAA0BA`，本来也不同。profile 的 boot-uuid/root_match_volume_uuid 指向 OS。NABU 的 `47719F50-…` 和 `C7DFE43C-…` 对应不同卷，不能为了相等而把 boot-uuid 改成 SystemOS UUID。

## 3. 主线真实启动顺序

源码链条是：

```text
bsd_init
  vfsinit()
  native_source_storage_start()
    加载原版 IOStorageFamily
    检查 IOBlockStorageDevice 基类大小
    验证 owner root policies
    加载原版 APFS
    注册源码存储类
    启动 UFS，派发独立根窗发布线程
    启动 RAM Data 盘
  BSD 继续执行
  bsd_autoconf / IOKitBSDInit 发布 IOBSD

独立发布线程：SystemOS → 等真实卷 → App → 等真实卷 → OS
```

`SourceStorage.cpp` 的对应位置为 264（入口）、270（family）、276（基类大小）、282（policy）、284（APFS）、287–293（类注册）、299–307（UFS/Data）。补丁明确将入口放在 vfsinit 返回后。

NABU 当前实际产物采用预链接 kext 和 IOKit 自动匹配：`scripts/link_nabu_desktop_storage.py:47`、`:50` 生成 NativeUfs、NativeRamData 两条 personality，未声明 VFS-ready 门控。CI wrapper 的 kmod start 只注册 RAM seed/addon 回调。它没有建立主线那个明确的“vfsinit 后才启动存储服务”的边界。

这证明实现时序约束不同；尚未测到 NABU 两个 start 相对于 vfsinit 的实际执行先后，不能直接宣布时序就是本次停机根因。

## 4. 当前故障范围

设备证据已确认：控制器命令完成、GPT、SHNGROOT 清单和窗口读取成功。`manifest=0`、`prepared=3`、`published=1`、`reads=5 failures=0` 表示已发布首个 SystemOS 块设备，等待其真实卷超时。

服务树显示 SystemOS 和 RAM Data 两路都有 AppleAPFSContainerScheme，却没有后续 APFS 卷。APFS 的 cryptoAlloc 初始化信息出现两次。这将当前问题限制在 APFS 初始化/派生媒体阶段，而不是 launchd 执行阶段。

不得从这些信息进一步断言某把锁、某次分配或某个资源等待已被定位。尤其：

- scheme 的 busy=0 不能证明其 start 已返回。
- BSD 的 `Waiting on [IOBSD, boot-uuid-media]` 不是 APFS 依赖链回溯。
- `bytes=3200000` 是旧诊断字段保留的最后一次准备窗口大小，不代表 SystemOS 实际大小或整盘大小。
- 0045 给原始媒体补写 UUID，不能产生真实 APFS 卷，不作为解决方案。
- 0046 修正查询和诊断盲区，但当前树里本来没有卷，不能单独承诺解决此次启动。

## 5. 再次核对后的编码方案：显式晚启动协调

本节替代此前共同 IOResourceMatch gate 方案。`IOService.cpp:4186–4231` 对同 provider 的匹配 category 逐一调用 startCandidate；NativeRamData 挂在平台根节点。将它停在 checkResources 会延迟该 provider 的后续 category。显式晚启动避免增加这种耦合，并能保留主线 UFS start 返回后再启动 Data 的顺序。

### 5.1 内核入口与模块注册桥必须一并接入

- 在 25F71 `bsd_init.c` 的 vfsinit 返回后调用一次性的 `nabu_source_storage_start()`。补丁进入 `scripts/build_macos26_xnu.py` 实际编译序列。
- 在现有 `platform/nabu/macos26/storage/NabuRootAuthorization.cpp` 的注册桥基础上增加存储启动 callback。它已通过 build_macos26_xnu.py 编入内核，可复用既有原子注册方式；拒绝空指针、替换 callback 和重复启动。
- 内核入口加载 `com.apple.iokit.IOStorageFamily`、`com.apple.filesystems.apfs`、`org.blu-sharky.NativeUfs`，逐项检查返回值。NativeUfs 的 kmod start 仅注册 callback。必须在 OSKextLoad 返回后再调用 callback，不能在模块加载/类初始化锁内启动存储。
- 不能让本体内核直接链接动态预链接模块里的新函数。由内核导出的注册桥连接模块 callback，避免新增 undefined symbol。

### 5.2 协调 callback 的明确行为

1. 核验实际 IOBlockStorageDevice metaclass 的 classSize 与本次编译头文件 sizeof 一致。保留已有两套 326-slot vtable 审计。
2. 查找实际携带 NABU MMIO、SMMU 与时钟资源的 IOPlatformDevice，以 nabuRootProvider 的准入检查为准。不能用 sheng 的 IOPlatformExpert 父对象替代。
3. 使用已注册的 NativeUfs 类，按 init → attach → start 启动。其现有 startRoot 在发布线程创建前完成窗口边界、签名策略和 UUID 验证，保留这条授权链。
4. UFS start 成功返回后，使用已注册的 NativeRamBlock 类，在 IOPlatformExpertDevice 上 init → attach → start，接入现有 Data seed。
5. 两者启动完成后立即返回 BSD。根窗发布仍由独立线程按 SystemOS → App → OS 执行；入口不等待 IOBSD 或真实 APFS 卷。
6. 失败记录具体步骤。已经 start 成功的对象可能被异步客户端持有，不能在后续失败时直接释放。

### 5.3 同一变更里关闭重复入口

`scripts/build_macos26_storage.py` 和 `scripts/link_nabu_desktop_storage.py` 两个 Info.plist 生成路径，都移除 NativeUfs/NativeRamData 的自动启动 personality。保留 bundle、依赖和现有构造函数；OSKext.cpp 已通过 OSRuntimeInitializeCPP 注册类，不能照抄 sheng 的手动 source_storage_*_init/preModLoad。

这三部分必须作为一个配套版本：内核晚启动入口、模块 callback、关闭自动入口。任何一项缺失都不得交付启动包。

### 5.4 一次构建的验收契约

- 核验最终镜像确实有晚启动调用、注册桥、模块 callback，Info.plist 不再保留这两个自动入口。
- 验证调用顺序与失败路径：vfsinit 后才进入协调器；UFS start 返回后才启动 Data；只启动一次；缺 callback/模块/合法 provider 明确拒绝；BSD 不等待卷。
- 启动日志至少标记 VFS_READY、FAMILY_READY、APFS_LOADED、UFS_STARTED、DATA_STARTED。最终成功条件是实际 APFS 卷出现、三窗口发布完成、OS 根挂载、launchd 执行，不能用这些阶段标签代替成功。
- 当前只知道 APFS 已进入 start、未发布后继媒体。若此版本仍停在 cryptoAlloc 后，必须记录 25F71 APFS 的锁分配、上下文池、buffer pool、media init、attach/register 的进入/返回位置，定位首个不返回的调用。当前静态差异本身不能替代这份执行证据。

这是一份可实施的主线时序对齐方案，未获设备验证，不能把它标成已经定位并修复当前 APFS 卡点。

## 6. 主线另外两项启动前置

macOS 指南明确说明 SoftwareAES 要按目标 KC 的 IOAESAccelerator ABI 编译和注册，AppleImage4 等待该服务。NABU 需要核查的是实际服务是否存在、corecrypto 初始化和调用绑定是否一致；链接了 corecrypto 不能替代 IOAESAccelerator 服务。现有证据尚未证明它就是本次 APFS 停点。

Data seed 应来自目标系统的 `/System/Library/Templates/Data`，保留所有者，并使 seed 的真实卷 UUID 与内核配置一致。容器头有效只证明格式，不证明目录内容足够启动桌面。

主线 apfs-boot-shim 用于根和 Data 可用后的 launchd 挂载任务，不能解决当前尚无 APFS 卷的问题。27.3 profile 本身是 arm64e；SM8150 的无 PAC 执行兼容仍需使用 26.5 的既有实现和后续用户态验证，不能通过选用 27.3 配置自动解决。

本轮只做源码研究、磁盘字节复核和方案记录，未修改内核/驱动、触发 CI 或刷写设备。

## 7. 再次核对后，已有功能与实际缺口

实际 CI build.json 的 desktop_kernel 和实际 kernel map 已确认 SystemOS union、Data mount、firmlinks、App cryptex 被编入当前内核。map 有 `_native_owner_data_firmlinks`、`_native_owner_macos_app_cryptex`，build.json 有对应阶段变量地址。不能把这些描述为尚未实现再重做；它们尚未通过当前启动实机验收。

当前实际模块闭包只有 13 个组件，包含 IOCryptoAcceleratorFamily 和 corecrypto，但没有 SoftwareAES/NativeAES 模块；本体 kernel map 也未发现 NativeAES。需对照 25F71 IOAESAccelerator ABI 确认并补齐服务提供者，不能将 family 或 AES 算法库等同于服务已注册。此项是启动依赖差异，不是当前 cryptoAlloc 停点已证实的原因。

现有 desktop 集成补丁中未见 `native_owner_macos_boot_shims`/`apfs-boot-shim` 接入。按主线接入此功能时，必须同时构建并打包对应 shim 可执行文件及其信任输入；只加启动参数无效。它在根和 Data 可用后执行，不能替代当前 APFS 初始化修复。

源码核对截至 UniXNU `268bfa1c` / sheng `81f43eba`。本次复核没有得到新的设备执行轨迹，没有宣称设备已启动。
