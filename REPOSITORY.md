# 仓库导航

本仓库保留 UniXNU 框架与 nabu 集成，目录位置继续匹配现有构建脚本。当前入口为 [README.md](README.md)，硬件结论为 [STATUS.md](STATUS.md)。

| 路径 | 用途 |
| --- | --- |
| [`config/unixnu.aligned.toml`](config/unixnu.aligned.toml) | 完整 GPU ESP 的默认启动项、菜单顺序、UI和显式Data初始化配置 |
| [`PROGRESS.md`](PROGRESS.md) | 截至P047的进度与验证记录 |
| [`devices/nabu/board.toml`](devices/nabu/board.toml) | macOS27.3 设备、构建、启动及持久 Data 配置 |
| [`devices/nabu/dts/`](devices/nabu/dts/) | nabu 设备树 |
| [`soc/sm8150/`](soc/sm8150/) | SM8150 共享平台、DTSI 和内核补丁 |
| [`devices/nabu/package/`](devices/nabu/package/) | 板级输入、电源、日志与存储等集成 |
| [`devices/nabu/package/gpu/a640/`](devices/nabu/package/gpu/a640/) | A640 内核后端与 macOS 适配 |
| [`devices/nabu/package/gpu/patches/`](devices/nabu/package/gpu/patches/) | Turnip / Darwin 补丁 |
| [`devices/nabu/scripts/`](devices/nabu/scripts/) | SDK 编译、ABI 绑定、PAC、签名与打包工具 |
| [`devices/nabu/tests/`](devices/nabu/tests/) | nabu Host 测试与二进制检查 |
| [`devices/nabu/drivers/`](devices/nabu/drivers/) | 为 nabu 保留的组件源码副本与来源信息 |
| [`devices/nabu/compat/`](devices/nabu/compat/) | 当前目标版本的兼容与 PAC 输入 |
| [`scripts/`](scripts/)、[`profiles/`](profiles/)、[`package/`](package/) | UniXNU 通用构建与引导框架 |
| [`drivers/`](drivers/)、[`kits/`](kits/) | 上游组件子模块；见 [`.gitmodules`](.gitmodules) |
| [`docs/`](docs/) | 上游站点源码及历史适配记录，不是本分支的当前硬件状态页 |
| [`.github/workflows/bringup.yml`](.github/workflows/bringup.yml) | 手动 nabu 原生内核、运行时、boot-tools 与 GPU 用户态构建 |

## 分支与历史

`main` 是当前 macOS27.3 公共快照。整理时以已完成构建的 nabu 开发树为文件基线，同时通过合并父提交保留旧分支历史；没有改写已有提交。

历史定位：

- 旧默认 `26.5` 快照：[`44bb65d`](https://github.com/baimaosuki/macOS-ForShengTOnabu/commit/44bb65d0178f15382b65a837f8fe47078ec0caeb)。
- 旧 `main` 快照：[`90db20a`](https://github.com/baimaosuki/macOS-ForShengTOnabu/commit/90db20aa35ef27d13b1b56ece1ed59f92a30f68b)。
- 当前最新 nabu 驱动快照：[`bf88431`](https://github.com/baimaosuki/macOS-ForShengTOnabu/commit/bf8843104b08006a94056bc20156926a2c11800b)。

旧26.5历史中包含早期构建产物。当前 `main` 文件树不再携带这些产物；本轮整理保留历史，没有执行历史清洗。需要减少首次下载时可使用 `git clone --filter=blob:none`。

## 暂停期间的工作流

保留 nabu 的手动构建入口。移除本分支继承的上游文档定时同步、网站发布和无关 CI 入口，避免在暂时停更期间自动生成上游设备资料或部署上游站点。此次仓库整理不启动新的内核或用户态构建。
