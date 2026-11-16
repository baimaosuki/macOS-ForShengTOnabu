# DarwinBoot

加载器支持 arm64 和 arm64e 的 `MH_EXECUTE` 内核，保留 CPU 子类型中的能力位。
指针认证初始化和链式重定位由 XNU 完成。`MH_FILESET` 会校验成员、入口、映射与权限；
NABU 适配允许 ARM64e 集合中包含普通 ARM64 原生内核。

NABU 可将独立加载器放到 ESP 的 `EFI/UniXNU/Drivers/DarwinBoot.efi`，并配置：

```toml
[[driver]]
path = "Drivers/DarwinBoot.efi"
args = "replace-existing"
required = true
connect = false
```

该显式选项允许替换唯一一个 revision 1 或 2 的固件 loader 接口。
旧驱动保留在内存，卸载 ESP 驱动会恢复原接口；多个提供者或未知版本会被拒绝。
需配套使用支持 `required` 的 UniXNU 程序：加载器启动失败时保留菜单并拒绝启动系统，
避免继续调用旧加载器。QEMU 测试验证协议安装、替换、恢复和必需驱动失败路径，
不代表 NABU 实机启动通过。

先按[适配流程](/zh/porting/platform)准备板级输入、修改源码、接入构建并逐步验证。

UEFI 启动加载器。安装 loader 协议，把内核、ADT、trust cache 和 ramdisk 交给 XNU。

| 仓库 | 维护者 | 范围 |
| --- | --- | --- |
| [DarwinBoot](https://github.com/UniXNU/DarwinBoot) | [UniXNU](https://github.com/UniXNU) | UEFI 启动加载器。安装 loader 协议，把内核、ADT、trust cache 和 ramdisk 交给 XNU。 |

## 状态

不是驱动。每个 XNU 启动路径都使用它。

## 构建

在目标固件 DSC/FDF 中加入 DarwinBoot INF，从 UniXNU 执行 `./unixnu build firmware`。按[固件集成](/zh/porting/uefi-firmware#required-integration)操作，选择 XNU 启动项前确认加载器协议。

新增可选 `kernel.macho.vroot` 侧文件，通过 `/chosen/memory-map/RootPolicies` 传递签名根策略。固件检查封装，内核以编入的独立公钥验证每份签名和哈希表；它与 Data RAMDisk 分开。此新增交接尚未构建或运行。
