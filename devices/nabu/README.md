# nabu 设备集成

**暂时停更（2026-11-22）：USB 不工作，触控工作，GPU 疑似在工作，仍缺多项驱动。**

目标：小米平板5 / SM8150-AC / Adreno640，macOS27.3 / 26C5035r，普通arm64与Zeta PAC适配。硬件现状统一见 [STATUS.md](../../STATUS.md)。

| 内容 | 入口 |
| --- | --- |
| 设备与启动配置 | [board.toml](board.toml) |
| 板级设备树 | [dts/nabu.dts](dts/nabu.dts) |
| 外部/本地组件 | [feeds.toml](feeds.toml)、[drivers/](drivers/) |
| 板级驱动与集成 | [package/](package/) |
| GPU 后端与用户态补丁 | [package/gpu/](package/gpu/) |
| 构建、ABI、PAC 与打包 | [scripts/](scripts/) |
| Host 与二进制检查 | [tests/](tests/) |
| 本地固件输入清单 | [proprietary-files.txt](proprietary-files.txt) |

[构建说明](../../BUILDING.md) · [仓库导航](../../REPOSITORY.md) · [来源与许可](../../UPSTREAM.md)

最新进度：[PROGRESS.md](../../PROGRESS.md)。ESP 对齐菜单与刷入规则：[INSTALLING.md](../../INSTALLING.md)。
