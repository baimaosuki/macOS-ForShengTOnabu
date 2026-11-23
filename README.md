# macOS on Xiaomi Pad 5 · nabu

[从源码制作PAC ROOT与ESP](REPRODUCING.md) · [复现审计与仍缺的条件](REPRODUCIBILITY.md)

[English](README.en.md) · [当前状态](STATUS.md) · [进度记录](PROGRESS.md) · [ESP 使用](INSTALLING.md) · [开源范围](OPEN_SOURCE.md) · [脱敏记录](PRIVACY.md) · [源码导航](REPOSITORY.md) · [构建说明](BUILDING.md) · [上游与许可](UPSTREAM.md)

基于 UniXNU 的小米平板 5（**nabu / SM8150-AC / Adreno 640**）macOS 实验移植。当前目标为 **macOS 27.3 / 26C5035r**。本仓库是 nabu 的独立实验分支，由 baimaosuki 整理维护；上游项目见[致谢](UPSTREAM.md)。

> **暂时停更 · 2026-11-22**
> USB 不工作，触控工作，GPU 疑似在工作，仍缺多项驱动。最新补丁已发布源码，后续开发暂无恢复时间。

## 当前实机状态

| 项目 | 状态 |
| --- | --- |
| 系统启动与显示 | 已能启动并出现系统界面，仍属实验状态 |
| NT36523 触控 | **工作**，依据当前实机反馈；完整多点手势兼容性未确认 |
| USB Host / 外接键鼠 | **不工作**；外设亮灯不能视为输入可用 |
| Adreno 640 GPU | **疑似在工作**；稳定硬件加速、完整桌面合成和性能尚未确认 |
| 其他驱动 | **多项缺失或未完成验证**，包括音频、无线、相机、传感器及完整电源管理等 |
| 开发状态 | **暂时停更**，暂无恢复时间或下一版计划 |

[STATUS.md](STATUS.md) 是当前状态入口。CI 成功、驱动发布、fence 完成或出现画面，都不能单独证明 GPU 桌面加速或 USB 输入已经可用。

## 仓库内容

- [`devices/nabu/`](devices/nabu/)：设备配置、驱动集成、PAC 适配、GPU/输入代码、构建脚本和测试。
- [`soc/sm8150/`](soc/sm8150/)：SM8150 平台与设备树。
- [`scripts/`](scripts/)、[`profiles/`](profiles/)、[`package/`](package/)：保留的 UniXNU 构建框架、OS 配置和引导支持。
- [`docs/`](docs/)：上游文档和历史工程记录；硬件现状以本页和 [STATUS.md](STATUS.md) 为准。

`main` 是当前公开快照入口。旧 26.5 与实验分支历史仍可通过提交记录查看，参见[仓库说明](REPOSITORY.md)。

## 构建与验证

目标系统是 **27.3**；最近使用的 **SDK 26.5** 是编译工具版本，二者不是同一概念。SM8150 没有硬件 PAuth，当前路径使用普通 `arm64` 与 Zeta PAC 适配。详情见 [BUILDING.md](BUILDING.md)。

最新 USB 事件路由修正已纳入 `main`，实机鼠标操作仍待验收。最近一次驱动代码快照为 [`bf88431`](https://github.com/baimaosuki/macOS-ForShengTOnabu/commit/bf8843104b08006a94056bc20156926a2c11800b)，对应[内核 CI 45012322361](https://github.com/baimaosuki/macOS-ForShengTOnabu/actions/runs/45012322361) 已通过。该结果属于构建验证，**不代表 USB 已修复或 GPU 已完成实机验收**。

ESP 菜单采用 [`config/unixnu.aligned.toml`](config/unixnu.aligned.toml)：默认 A640 Metal GPU、UI scale=3、GPU 初始化项须确认。新版 ESP 包校验和已包含这份配置；旧包覆盖规则见 [INSTALLING.md](INSTALLING.md)。

本仓库保留源码和构建记录。Apple 系统素材、ROOT/Data 镜像、设备固件、私钥和本机账号文件由构建者自行准备，不纳入当前源码快照。现有构建流程仍有本地输入和集成步骤，参见[构建边界](BUILDING.md#构建边界)。

## 许可与致谢

参见 [LICENSE](LICENSE)、各组件自身许可证及 [UPSTREAM.md](UPSTREAM.md)。保留 UniXNU、SHENG、Zeta、AdrenoMetal、metal2vulkan、Mesa 与其他组件的原作者归属。
