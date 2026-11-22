# 构建说明

当前项目[暂时停更](STATUS.md)。以下记录已有源码入口与构建边界，供继续研究使用。

## 版本与工具链

| 项目 | 当前基线 |
| --- | --- |
| 设备 | Xiaomi Pad5 / nabu / SM8150-AC / Adreno640 |
| 系统 | macOS27.3 / 26C5035r |
| XNU | 12377.121.6 |
| 内核与自建工具架构 | 普通 arm64；Zeta PAC 适配，SM8150 无硬件 PAuth |
| 已使用的构建环境 | Intel macOS runner，Xcode26.6，SDK26.5 |
| 版本边界 | SDK26.5 是编译工具版本，目标系统仍是27.3 |

先读 [`devices/nabu/board.toml`](devices/nabu/board.toml) 与 [`.github/workflows/bringup.yml`](.github/workflows/bringup.yml)。依赖版本由设备 feeds、组件清单及 GPU sources.lock 共同确定，不应把 A740/SM8550 的寄存器、固件或 ABI 表当作 A640/nabu 使用。

## 源码与原生内核

```sh
git clone --filter=blob:none https://github.com/baimaosuki/macOS-ForShengTOnabu.git
cd macOS-ForShengTOnabu
git submodule update --init --recursive
./unixnu defconfig nabu_macos27_defconfig -D devices/nabu --os macos-27
python3 devices/nabu/scripts/compile-bootstrap.py --full
```

原生构建还需要工作流中列出的工具和 Python 依赖。GitHub Actions 的 `NABU fresh macOS 27.3` 保留手动触发，`stage` 包含：

| stage | 产物范围 |
| --- | --- |
| `native` | 原生内核及 boot-tools |
| `boot-tools` | arm64 引导工具 |
| `gpu-userspace` | Turnip / M2V / Metal bundle 的构建与签名检查 |
| `runtime` | 为本地接收方加密的运行时输入 |

`runtime` 的公钥接收方由维护者配置，现有工作流不是面向所有使用者的通用运行时下载入口。继续构建者需要配置自己的接收方和本地输入；私钥、账号与解密后产物不进 Git。

## 固件与系统输入

[`devices/nabu/proprietary-files.txt`](devices/nabu/proprietary-files.txt) 列出当前板级固件文件。由构建者自行从其设备/合法来源准备，当前源码快照不提供这些文件。

生成生产 ADT 时使用：

```sh
export NABU_GPU_FIRMWARE_DIR=/absolute/path/to/local/adreno640
export NABU_TOUCH_FIRMWARE_DIR=/absolute/path/to/local/nabu-touch
```

脚本会核对固件大小和散列。Apple IPSW、27.3 kernelcache、系统卷、Zeta ROOT 与 Data seed 也是独立本地输入。来源和布局参见设备脚本及上游文档，不能用“内核 CI 成功”代替这些输入的准备。

## 本地检查

已准备原始27.3 kernelcache和所需编译器/依赖时，相关入口包括：

```sh
python3 -m pytest -q devices/nabu/tests/test_input27.py
python3 devices/nabu/tests/check-usb-allocations27.py   --kernelcache /absolute/path/to/kernelcache.release.mac17j.decompressed   --output /absolute/path/to/usb-allocation-verification.json
```

GPU、SMMU、PAC 与 signed-text 检查位于 [`devices/nabu/tests/`](devices/nabu/tests/)。其中依赖 SDK、原始二进制或设备输入的检查，需要先准备对应条件。

## 构建边界

当前集成仍需要本地预链接、原始系统素材、固件、签名/信任输入、UEFI 和 ESP 合成。仓库保留这些阶段的源码入口，尚未整理成面向新用户的一键安装发行流程。没有新的安装验证保证，USB 和 GPU 状态以 [STATUS.md](STATUS.md) 为准。

普通启动使用持久 Data。`data-reset=1` 对应显式初始化 Data 的菜单/流程，不是普通启动参数；继续研究时应区分已有系统与初始化步骤。
