# 开源范围与复现边界

2026-11-22整理。仓库：[baimaosuki/macOS-ForShengTOnabu](https://github.com/baimaosuki/macOS-ForShengTOnabu)，默认入口为`main`。

## 本次补齐

之前公开`main`停在P045，最新USB修正仅在开发分支，且对齐菜单仅存在维护者本机。本次将P046/P047源码历史纳入主线，发布对齐菜单、菜单安装/重打包工具、测试、进度与使用说明。

| 内容 | 公开入口 |
| --- | --- |
| 板级驱动、USB/触控/电源键 | [`devices/nabu/package/`](devices/nabu/package/) |
| A640后端、Turnip/Darwin补丁与来源锁定 | [`devices/nabu/package/gpu/`](devices/nabu/package/gpu/) |
| DTS与SM8150平台 | [`devices/nabu/dts/`](devices/nabu/dts/)、[`soc/sm8150/`](soc/sm8150/) |
| PAC、签名与ABI兼容输入 | [`devices/nabu/compat/`](devices/nabu/compat/) |
| 构建、集成、测试 | [`devices/nabu/scripts/`](devices/nabu/scripts/)、[`devices/nabu/tests/`](devices/nabu/tests/) |
| 精确上游依赖 | [ORIGIN.json](devices/nabu/ORIGIN.json)、[feeds.toml](devices/nabu/feeds.toml)、[sources.lock.json](devices/nabu/package/gpu/sources.lock.json)、[.gitmodules](.gitmodules) |
| 完整GPU ESP默认菜单 | [`config/unixnu.aligned.toml`](config/unixnu.aligned.toml) |
| 最终菜单安装与FAT/ZIP重打包 | [align-esp-menu27.py](devices/nabu/scripts/align-esp-menu27.py)、[align-esp-package27.py](devices/nabu/scripts/align-esp-package27.py) |
| CI | [手动nabu工作流](.github/workflows/bringup.yml) |
| 功能状态、进度与说明 | [STATUS.md](STATUS.md)、[PROGRESS.md](PROGRESS.md)、[BUILDING.md](BUILDING.md)、[INSTALLING.md](INSTALLING.md) |

现有内容足以公开研究板级实现、查看补丁、运行相应Host测试，并在准备好SDK和本地输入后继续构建。**目前没有面向全新设备的一键安装发行流程，也未完成在干净环境中从零生成整套ROOT/ESP的复现验收。**

## 必须另外准备的输入

- 原始macOS27.3系统素材、kernelcache、系统卷/ROOT、Data seed。
- 对应设备的GPU/触控固件，文件要求见[proprietary-files.txt](devices/nabu/proprietary-files.txt)。
- nabu UEFI及构建工具链、原生SDK、运行时与签名/信任输入。
- 构建者自己的平台标识、运行时接收方与账号配置。

这些本地输入未随当前源码树分发。`runtime` CI采用维护者接收方，不是所有人可用的运行时下载服务。Host回放依赖的原始Apple二进制需自行准备；大体积产物、原始设备日志和维护者`out/`工作目录不作为源码依赖打包进Git。

维护者历史产物中的机器路径是记录用位置，不能直接当作其他机器的构建路径。实际构建入口及已知边界见[BUILDING.md](BUILDING.md)。

## 许可与历史

根目录[LICENSE](LICENSE)及各组件自己的版权/许可继续适用，详见[UPSTREAM.md](UPSTREAM.md)。本项目包含不同许可的上游源码与修改，未将其统一改授为根目录许可证。

检查范围为当前公开文件树及本次新提交；旧26.5历史仍保留，没有改写/清洗历史。首次下载建议使用`git clone --filter=blob:none`。当前树检查未发现账号凭据、私钥、系统磁盘映像或构建缓存；这不等同于对全部历史作保密或法律认证。

## 对外状态

USB鼠标尚未验收可用，触控据实机反馈工作，GPU仍待稳定性/性能确认，多个驱动缺失。后续开发暂时停更，不作完成时间承诺。发布时请保留这组状态，避免把实验源码描述为已完成移植。
