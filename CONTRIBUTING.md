# 参与研究

当前开发[暂时停更](STATUS.md)，没有恢复时间，也不承诺问题响应、评审或新版本发布。

继续研究时，请：

1. 从 [`devices/nabu/`](devices/nabu/) 与 [BUILDING.md](BUILDING.md) 了解27.3基线、普通arm64/PAC边界及本地输入。
2. 将 Host 编译/单测、CI 产物验证和真机结果分别记录，附源码提交及必要设备日志。
3. 仅在实际枚举、输入或渲染证据充分时更新硬件状态；不要把编译成功、驱动发布或单个fence当作完整功能可用。
4. 不提交系统镜像、厂商固件、账号、私钥或本机缓存。组件来源、版权与许可证必须保留。
5. 优先将板级改动放在 `devices/nabu/`，SM8150共用平台改动放在 `soc/sm8150/`；不要混入其他设备的硬件常量。

上游模块结构和开发规范参考 [UniXNU contributing](https://unixnu.com/contributing/repository)。本仓库的当前状态与暂停说明以 [STATUS.md](STATUS.md) 为准。
