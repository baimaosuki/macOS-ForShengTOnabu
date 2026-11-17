# macOS 配置

最新 Nabu 实机观察器已正常输出。294、414、494 秒的 loginwindow 主线程
寄存器、已捕获栈和 CPU 时间相同，停在 `NSApplication init+0x734 →
TCCAccessLaunchDisclosurePromptIfNeeded → _tccd_send_message →
xpc_connection_send_message_with_reply_sync`。AppKit 的
`_showLaunchDisclosurePromptForManagedMacs` 首先查询 `TCC/managedTCC`。
第八条签名文本规则仅在 `0x18a79d0ec` 将此特性查询返回值设为 false，执行
原生关闭分支和函数退出。该共享 AppKit 方法会影响使用此启动披露的应用；
不授予 TCC 权限，不改变通用访问检查。原指令重放覆盖实际 init 调用点和关闭
分支；实机继续推进及桌面显示尚待验证。同期 lsd 继续建库注册及文件系统工作；
稍后 usermanagerd 出现 persona-manager 断言。内核已编译 persona 函数，
此特性开关不修复这两个后端。

Nabu 观察器每次启动都以排他创建方式保留新的带序号日志；早期系统秒数和 PID
可能重复，已有日志不能导致抓栈停止或被覆盖。落盘准备前先报告启动状态，磁盘
写入和 ESP 控制台镜像互不阻断，磁盘满也继续镜像。用户栈上限为 64 帧。
观察目标增加 `trustd`、`securityd` 和 `amfid`，跟进 lsd 建库期间已捕获的代码
签名信任请求。此项是只读诊断修复，不替换服务，也不代表黑屏已解决。

后一轮 Nabu 实机日志确认 SafeAperture 规则已命中，loginwindow 随后在
325、418、498 秒停在 `NSApplication init+0x720 → _NSMagnifyMode →
_HIMagnifiedMode → CFURLCopyResourcePropertyForKey → _LSCopyServerStore`。
新增 `0x18f0f7304` 签名文本规则仅将这处可选 `kCFURLIsApplicationKey` 查询
报告为不可用，保留原生 URL 释放、默认分支与 `AppleMagnifiedMode` 偏好读取。
该分支也跳过 bundle／`NSHighResolutionCapable` 元数据推断，不改变通用 URL 或
信任评估。lsd 的栈曾在注册必需 bundle 时执行代码签名信任评估，后一轮又继续
运行；后端问题与桌面显示仍未验收。七条页面规则保留既有 CPU 渲染路径。

Nabu 的 26C5035r 实机栈在 374 至 499 秒三次捕获 loginwindow 卡在
`NSApplication init → SafeAperture → URL 属性查询 → _LSCopyServerStore`。
设备签名文本规则在 `0x18acbb658` 仅将这处可选 SafeAperture URL 查询转为
“不可用”，由预先清零的结果走原生失败分支，保留调用者模式位与对象清理。
该规则不禁用 LaunchServices、不修复其数据库后端，也不代表桌面已经显示。
同期 lsd 栈显示数据库清理／扩展点注册的持续 CPU 工作，不能称为已证实的单点死循环。

Nabu 的 26C5035r CPU 模式可用 `nabu-session-observer=1`，由已签名的第二阶段
启动 shim 异步启动只读观察器。在发现目标进程后 0、120、240、320 秒，记录
线程状态、CPU 时间、原始栈和共享缓存 slide；完整记录写入
`/private/var/log/nabu-session27/`，同时镜像到现有 ESP 控制台日志。
挂载任务不等待观察器，观察器不暂停目标或改变 XPC／渲染策略。
这用于定位 BGRA 分配修复后仍然输出黑内容的现场，不能视作桌面已点亮。

`macos-27.3-26C5035r` 构建面向 macOS 27.3 用户空间的内核。它属于
[arm64e profile](/zh/porting/arm64e)，设备必须满足 arm64e 要求。同一个设备仓库同时服务
iPadOS 和 macOS profile：macOS 的值放在 `board.toml` 的
`[os_overrides."macos-27.3-26C5035r"]` 下，iPadOS 构建不读取它们。

本页沿[移植设备](/zh/porting/steps)的顺序说明 macOS 的差异。未提到的步骤与 iPadOS 相同。

## profile 提供的内容 {#profile}

UniXNU 的 `profiles/os/macos-27.3-26C5035r/profile.toml`：

| 表 | 内容 |
| --- | --- |
| `[os]` | XNU `12377.121.6`，`target = "macosx"`。内核以 `XNU_TARGET_OS_OSX` 编译，机器配置、内存布局和链接地址与 iPadOS 内核相同 |
| `[ipsw]` | `UniversalMac_27.3_26C5035r_Restore.ipsw`，产品 `Mac17,11`。`fetch` 列出 kernelcache、设备树以及静态、系统和应用信任缓存 |
| `[kernel.prelink]` | `kernelcache_sha256` 固定值、macOS 启动闭包的 `roots` 和 `early_security_init`。以 `!` 开头的 root 不进入闭包。该内核集合保留符号表和链接地址，因此没有 `slide` |
| `[adt]` | Mac17,11 身份、`/chosen` 启动卷 UUID 和 `asmb` 启动策略 |
| `[userdata]` | 三个窗口：`OS`、`SystemOS`（`Cryptex1,SystemOS`）和 `App`（`Cryptex1,AppOS`） |
| `[root]`、`[payload.adt]` | 三个窗口的卷 UUID 和镜像摘要，以及根卷 UUID |

同目录的 `configs/macos.config` 和 `patches/xnu/series` 提供内核配置和补丁序列：
Darwin 26 系统调用与内核接口、macOS 机器选项、Data firmlink、位于 Data 上的 VM 与
Hardware 卷，以及应用 cryptex 挂载。

签名文本修补按原始字节校验。代码签名检查使用有界快照，APFS 压缩块检查使用私有副本，已驻留的可执行页面保留配置的指令。

## 1. 检查主机和输入 {#inputs}

macOS 构建在装有 Xcode 的 Mac 上进行。除[通用前置条件](/zh/guide/requirements)外还需要：

| 输入 | 来源 |
| --- | --- |
| macOS SDK | Xcode。启动 shim 和使用 macOS family 头文件的设备配方通过 `xcrun --sdk macosx` 编译 |
| IPSW | profile 的 `[ipsw] url`，或 `--ipsw` |
| Kernelcache | 从 IPSW 提取，并按 `kernelcache_sha256` 校验 |
| 信任缓存 | `[payload.runtime] trust_components` 指定进入 owner 信任包的 IPSW 成员 |
| Data seed | 以 root 身份从系统卷制作一次，见[步骤 4](#data-seed) |
| root 权限 | 制作 Data seed 需要 `sudo`，只读枚举 Metal 库需要非交互 `sudo` |

## 2. 在设备仓库加入 macOS 的值 {#device}

为该 profile 添加 defconfig，在 `[package.defconfig]` 中映射，并把所有不同的值放进覆盖表。
[sheng 的 board.toml](https://github.com/UniXNU/UniXNU-sheng/blob/00ba90984e831e4c6ce30ec3d8a56e6434e9f1af/board.toml)
是完整示例。

```toml
[package.defconfig]
"macos-27.3-26C5035r" = "<device>_macos_defconfig"

[os_overrides."macos-27.3-26C5035r"]
remove = ["payload.install", "payload.skip_setup_tool", "payload.runtime.appsync"]

[os_overrides."macos-27.3-26C5035r".kernel]
defconfig = "<device>_macos_defconfig"
config_fragments = ["arch:configs/common.config", "os:configs/macos.config"]
archives = ["<macOS 内核的全部归档配方，按顺序>"]

[os_overrides."macos-27.3-26C5035r".components]
include = ["<macOS 内核的全部组件>"]

[os_overrides."macos-27.3-26C5035r".payload]
size_mib = 200
kernel_args = ["<macOS 镜像的全部内核参数>"]

[os_overrides."macos-27.3-26C5035r".payload.adt]
chip_id = "6050"

[os_overrides."macos-27.3-26C5035r".payload.runtime]
target = "macos27"
build = "26C5035r"
data_seed_tool = "scripts/make-macos-data-seed.py"
data_gib = 210
skip_setup = false
boot_shims = true
```

| 键 | macOS 的值 |
| --- | --- |
| `[package.defconfig]` | `--os` 和打包脚本为该 profile 选择的 defconfig |
| `kernel.config_fragments` | `os:configs/macos.config` |
| `kernel.patch_series` | 板级列表使用 `os:` 前缀时无需覆盖：`os:patches/xnu/series` 解析为 macOS 序列 |
| `kernel.archives`、`components.include`、`payload.kernel_args` | 完整列表。数组会替换板级数组 |
| `kernel.prelink.roots` | 不设置，使用 profile 的列表。其他 profile 的板级列表应放在该 profile 的覆盖表中，因为板级值会替换 profile 列表 |
| `payload.size_mib` | 容纳内核集合、Data seed 和 addon |
| `payload.adt.chip_id` | `6050`，Mac17,11 的芯片 |
| `payload.runtime.target`、`build` | `macos27` 和 `26C5035r` |
| `payload.runtime.data_seed_tool` | 以 root 运行的 seed 脚本 |
| `payload.runtime.data_gib` | Data 卷大小。持久化 Data 存储不能与 `userdata` 中的根镜像重叠 |
| `payload.runtime.skip_setup` | `true` 时在 seed 命令中加入 `--skip-setup` |
| `payload.runtime.boot_shims` | 把 owner `apfs_boot_util` 加入 addon，配合 `apfs-boot-shim=1` |
| `payload.runtime.boot_tool_arch` | 自带工具的 ABI，默认 `arm64e`。Nabu 使用 `arm64`：其无 PAuth 加载器接受 Apple 的 arm64e ABI v0，而 SDK 的 arm64e 目标生成预览 ABI v1。此设置同时用于 APFS 启动工具和 absent-service 工具 |
| `payload.runtime.gpu_addon` | `true` 时构建 macOS 的 Metal 插件。带 `gpu-addon=1` 时内核把它写入 Data 上的 `/Library/GPUBundles` |
| `remove` | 仅适用于 iPadOS 的板级键，例如安装入口、`payload.skip_setup_tool` 和 `payload.runtime.appsync` |

sheng 的 macOS defconfig 与 iPadOS 27 的 defconfig 只有 Data 卷 UUID 不同，构建会根据 seed
写入该值。Mac17,11 身份来自 profile。

macOS 补丁序列增加的内核参数：

| 参数 | 作用 |
| --- | --- |
| `vc-text=1` | 在启动 framebuffer 上以文本绘制内核控制台，并与串口控制台互为镜像 |
| `exec-log=1` | 打印每次镜像激活和每次退出及其 wait status 与原因。诊断用，默认关闭 |
| `apfs-boot-shim=1` | 在 launchd 的挂载启动任务中用 addon 的 `apfs_boot_util` 覆盖系统自带的版本。需要 `boot_shims = true` |
| `timebase-hz=24000000` | 硬件计数器频率不同时，为用户空间提供 24 MHz 时基 |
| `gpu-addon=1` | 把 Metal 插件写入 `/Library/GPUBundles` |

## 3. 选择 profile {#select}

`--os` 只对当前命令生效。macOS 构建的每条命令都要带上它；只构建 macOS 的板子可以设置
`board.os_profile`。`macos-27` 解析为 `macos-27.3-26C5035r`，输出目录为
`out/<device>/macos-27.3-26C5035r/<config>/`。

```sh
./unixnu defconfig <device>_macos_defconfig -D ../UniXNU-<device> --os macos-27
./unixnu fetch --os macos-27
./unixnu ipsw info --os macos-27
./unixnu status --json --os macos-27
```

`ipsw info` 显示 `27.3 (26C5035r)`。`status` 显示 macOS profile 根目录、合并后的覆盖值和补丁顺序。

## 4. 制作 Data seed {#data-seed}

用一条命令构建内核和 payload，使内核构建绑定 seed 的卷 UUID：

```sh
./unixnu build kernel payload --os macos-27
```

没有 seed 时，命令在编译前停止，打印创建 seed 的命令，其中包含 OS 镜像、输出路径和
`data_gib`。

### 创建账户并跳过 Setup {#account}

在 UniXNU 仓库中执行，使用构建命令打印的路径和大小。
交互输入短用户名、显示名称和隐藏密码：

```sh
sudo python3 scripts/make-macos-data-seed.py \
  "<OS image>" "<output>/runtime/data.dbr" --gib "<data_gib>" \
  --skip-setup --interactive-user --auto-login
```

脚本创建本地管理员及其主目录，保留模板文件属主，并跳过首次开机和首次登录时的设置助理。
输出文件必须是新 seed。

提示默认使用 `admin` 和 `Admin`。`sudo` 验证构建主机账户的密码；
交互输入的密码用于新建的 macOS 管理员账户。

自动化时，将 `--interactive-user` 替换为 `--user NAME:PASSWORD --real-name NAME`，
或从私有 JSON 文件读取凭据：

```sh
sudo -v
sudo -n python3 scripts/make-macos-data-seed.py \
  "<OS image>" "<output>/runtime/data.dbr" --gib "<data_gib>" \
  --skip-setup --user-stdin --auto-login < account.json
```

`account.json` 包含字符串字段 `name`、`password` 和可选的 `real_name`。
`--real-name` 优先于 JSON 中的显示名称。提供凭据后不再显示账户输入提示。

使用该 seed 构建并打包：

```sh
./unixnu build kernel payload --os macos-27 \
  --data-ramdisk "<output>/runtime/data.dbr"
./unixnu bootimg --os macos-27 \
  --data-ramdisk "<output>/runtime/data.dbr"
```

sheng 刷入全新 userdata 后，选择 **Initialize macOS Data and boot**，
再确认 **Initialize Data and boot**。这会清空 macOS Data，并从 seed 初始化持久存储。
后续开机选择 **Boot**，保留 Data。账户和设置助理配置来自 seed；
`--skip-setup` 在制作 seed 时传入。

sheng 的 macOS 配置使用固定 SHA-256 的固件模板完成 ABL 封装。
`unixnu bootimg` 只替换本次固件构建生成的 DarwinBoot，保留已验证的 BootShim、
设备固件、DTB 和 Android 头参数。payload 为 187 MiB；启动镜像超过 193 MiB 时停止打包。
菜单等待按键。

模板缓存在 `out/downloads`。离线构建时，将 `UNIXNU_BOOT_TEMPLATE` 指向对应的
`.img.xz`，仍会核对校验值。`boot/boot.json` 记录模板、加载器和实际固件的哈希。

制作 seed 的参数：

| 参数 | 作用 |
| --- | --- |
| `--skip-setup` | 把设置助理标记为已完成 |
| `--user NAME:PASSWORD` | 添加本地管理员及其主目录 |
| `--real-name NAME` | 设置管理员的显示名称，短用户名与 `/Users/NAME` 路径保持不变 |
| `--user-stdin` | 从标准输入的 JSON 对象读取 `name`、`password` 和可选的 `real_name` |
| `--interactive-user` | 交互输入账户名、显示名称和密码 |
| `--auto-login` | 开机自动登录指定账户，供无人值守测试使用 |
| `--keyboard-type PRODUCT-VENDOR-COUNTRY=TYPE` | 记录键盘布局类型，键盘设置助理不再询问 |
| `--volume-uuid UUID` | 为 Data 卷指定固定 UUID，新 seed 可以替换旧 seed 而无需重新构建内核 |

该安装之后的每次构建都使用同一个 `runtime/data.dbr`。seed 的 UUID 与已编译配置不同时，
payload 构建会停止并要求重新构建内核。内核把 seed 挂载到 `/System/Volumes/Data`，并设置
`/usr/share/firmlinks` 中的 firmlink。

## 5. 构建镜像 {#build}

```sh
./unixnu build kernel payload --os macos-27
./unixnu build package --os macos-27
./unixnu build firmware --os macos-27
./unixnu build userdata --os macos-27 --dry-run
./unixnu build userdata --os macos-27
./unixnu bootimg --os macos-27
```

打包脚本执行除 userdata 以外的相同步骤：

```sh
scripts/package.sh --os macos-27 --uefi ../uefi-<device>
```

| 步骤 | macOS 的差异 |
| --- | --- |
| 内核 | `XNU_TARGET_OS_OSX`、macOS 补丁序列，以及针对 macOS 内核集合的预链接 |
| Payload | Mac17,11 ADT、owner 信任包和为 `macos27` 构建的 addon |
| Userdata | 三个窗口。按 `data_gib` 核对 dry-run 报告的容量 |
| Package、固件、启动镜像 | 无 |

链接 owner 根策略的板子（`CONFIG_XNU_SOURCE_STORAGE=y`）需要为每个窗口提供签名策略。
`scripts/make-root-policies.py --tree TREE --output POLICIES --window NAME=IMAGE:VOLUME_UUID`
写出 `POLICIES/NAME/manifest.header.bin`、`trusted-key.bin` 和 `verification.json`，
以及哈希表 `TREE/build/verified-root-NAME/hashes.bin`。`IMAGE` 保存该窗口的字节，其 SHA-256
等于 profile 的 `[root] <window>_image_sha256`。存储归档配方读取其 `--policy-dir`
参数指定的策略目录。使用 `CONFIG_XNU_SOURCE_STORAGE_TABLES="userdata"` 时，用
`--object NAME.vroot=FILE` 把每个哈希表加入 userdata 镜像。

## 6. 适配驱动 {#drivers}

按相同顺序完成[驱动适配](/zh/porting/drivers)。macOS 内核集合有自己的类布局，macOS
用户空间还需要一些 iPadOS 不需要的服务。sheng 移植包含以下各项：

| 范围 | macOS 适配 |
| --- | --- |
| 私有类绑定 | 每个被绑定的类族按构建各有一份 fixture：`scripts/<family>-26C5035r.json`。`scripts/gen-board-abi.py` 根据内核集合的符号生成，显示配方按构建号选择 |
| 加密 | `SoftwareAES` 加入 `components.include` 和 `kernel.archives`，按 26C5035r 的类布局编译。AppleImage4 等待 `IOAESAccelerator` 服务，因此由板级 probe 注册它 |
| 网络 | `en0` 是原始 IONetworkingFamily 的 `IOEthernetController`，由 `ethernet=1` 启动，承载 Wi-Fi 模块的帧。TimeSync 时钟的标识取自以太网或 802.11 接口的地址，WindowServer 等待该时钟。supplicant 为 macOS 构建，其任务是 `/Library/LaunchDaemons` 中的 plist |
| 输入 | HID 设备由 `offline-hid-home=1` 启动。WindowServer 通过服务的 `displayUUID` 把触摸屏与显示器配对，该值是 framebuffer 发布的 `IOMFBUUID` |
| 键盘保护套 | 键盘加指针设备：相对位移、滚动步进和两个按键，不含 Keyboard Power usage |
| GPU | 插件构建为 macOS bundle，可执行文件位于 `Contents/MacOS` |
| 音频 | 音频服务器插件是位于 `/Library/Audio/Plug-Ins/HAL` 的 macOS driver bundle |
| 蓝牙 | 守护进程打开串口设备 `/dev/cu.BLTH`。板级的守护进程、BlueTool 和能力库位于 addon 的 `sbin` 与 `lib`，覆盖显示在 `/usr/sbin` 和 `/usr/lib` 上 |
| 内核内的固件镜像 | 放在 `__DATA,__firmware` |

## 7. 启动并检查 {#bring-up}

按[启动镜像](/zh/guide/boot)刷入 userdata 镜像并启动。
[启动验证顺序](/zh/porting/bring-up)同样适用，macOS 另有以下检查点：

| 阶段 | 检查 |
| --- | --- |
| 存储与根卷 | 分区方案发布 `OS`、`SystemOS` 和 `App`。根卷是只读系统卷，SystemOS 挂载在其上 |
| Data | Data 挂载到 `/System/Volumes/Data`。`/System/Volumes/VM` 和 `/System/Volumes/Hardware` 解析到其中 |
| Cryptex | OS cryptex 视图和 App cryptex 出现在 `/System/Volumes/Preboot/Cryptexes` 下 |
| 用户空间 | launchd 完成启动任务，WindowServer 显示登录窗口或设置助理 |

`vc-text=1` 让内核日志保持显示在面板上。`exec-log=1` 指出失败的启动任务。

## 8. 记录结果 {#record}

`porting.toml` 的 `device.os_profile` 等于 `board.os_profile`，即板子的默认 profile。
服务多个 profile 的板子只保留一份记录：把 macOS 的配置、发现和证据写入相同的范围，
并在每一项中写明 profile 和构建号。见[移植记录](/zh/porting/records)。

Nabu 沿用 SHENG 的 `no-lc=1` 调试选项，使自带可信 daemon 替身能承接 launchd 任务。WindowServer 前三次普通 SIGABRT 退出会在生成 corpse 前将实际退出线程的用户栈和共享缓存 slide 写入现有 ESP 日志；保留原信号与服务重启行为。抓栈本身不表示已定位或消除 abort。

Nabu 的 CPU 路径使 26C5035r SkyLight 的 Metal 能力初始化保留静态无设备状态，原生选择器随后调用 `activate_compositor_sw`，由原生 QuartzCore 软件渲染与 CPU GOP 提交像素。`IOMFBDisplay::current_page_surface` 在所有格式覆盖之后、复用检查和分配之前统一选择非压缩 BGRA，使帧缓冲实际分配与元数据一致。原先只改 `framebuffer_pixel_format` 的单一分支没有覆盖后续格式选择；实机捕获 `WSCompositeDestinationCreateWithIOSurface` 不支持格式的 abort 后，将它替换为上述最终选择点。软件中间缓冲仍为 BGRA8，原生目标格式校验保留。五处签名文本规则均固定原指令和原页哈希。这些是板级 CPU 适配，SHENG 仍使用其 Metal 后端；实机显示验收尚待完成。
