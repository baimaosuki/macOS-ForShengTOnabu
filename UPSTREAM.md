# 上游、来源与许可

本仓库是小米平板5 nabu 的实验集成，包含 UniXNU 通用框架、设备与 SM8150 适配，以及为当前目标保留的组件源码和补丁。

| 来源 | 用途与归属 |
| --- | --- |
| [CrossDarwin/macOS-ForSheng](https://github.com/CrossDarwin/macOS-ForSheng) | 仓库历史源头，旧26.5/sheng工程与早期适配记录 |
| [UniXNU](https://github.com/UniXNU/UniXNU) | UEFI/构建框架、OS 配置和公共组件体系 |
| [UniXNU-sheng](https://github.com/UniXNU/UniXNU-sheng) | macOS27.3 架构与集成参考；其 SM8550/A740 硬件配置与 nabu 不同 |
| [UniXNU-zeta](https://github.com/none24smu/UniXNU-zeta) | 无硬件 PAuth 平台的 Zeta PAC 路径参考与保留实现 |
| [AdrenoMetal](https://github.com/UniXNU/AdrenoMetal) | Metal 前端和 GPU 用户态集成 |
| [metal2vulkan](https://github.com/steelbrain/metal2vulkan) | Metal 到 Vulkan 转换 |
| [Mesa](https://gitlab.freedesktop.org/mesa/mesa) | Turnip Vulkan 驱动 |
| [macOS-DroidVM](https://github.com/Droid-VM/macOS-DroidVM) | macOS 平台适配参考，详细来源保留在上游文档和补丁中 |
| [UniXNU-nabu](https://github.com/UniXNU/UniXNU-nabu)、[nabu-usb-cdc](https://github.com/Lux12138/nabu-usb-cdc) | nabu 硬件与触控/USB 实现参考；USB device 模式不等于当前 macOS Host 已可用 |
| [小米原厂内核](https://github.com/MiCode/Xiaomi_Kernel_OpenSource/tree/nabu-r-oss) | nabu DTS、时钟、PHY、USB 与其他硬件定义 |
| [Apple OSS XNU](https://github.com/apple-oss-distributions/xnu) | XNU 公开源码 |

具体来源版本见 [`devices/nabu/ORIGIN.json`](devices/nabu/ORIGIN.json)、[`devices/nabu/feeds.toml`](devices/nabu/feeds.toml)、[`devices/nabu/package/gpu/sources.lock.json`](devices/nabu/package/gpu/sources.lock.json)、各 `PROVENANCE` / `LICENSE` 文件及 Git 历史。

根目录保留上游 [LICENSE](LICENSE)。各目录和文件自身的 BSD、GPL、APSL 或其他许可继续适用；根许可证不替代组件许可证。保留原作者、版权声明、来源与补丁归属。

当前默认文件树只发布源码、补丁、文档和必要公开元数据。板级固件与 Apple 系统镜像由构建者单独准备；`runtime-recipient.pem` 是公开接收方公钥，策略头与 trusted-key 文件是构建所需的公开验证元数据。
