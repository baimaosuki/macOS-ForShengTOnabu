# ShengUfs

NABU 在 `IOKitBSDInit` 发布真实 `IOBSD` 资源后启动源码存储，入口会拒绝提前调用。
原生媒体 BSD 客户端在匹配时需要该资源。随后加载原生 APFS 模块，再发布根卷窗口。
等待快照记录 `IOBSD`、拒绝请求数、完成回调进入数和返回数；每个窗口前 16 次读取
记录 LBA、长度、返回码及回调返回。该时序修正仍需实机验证根卷挂载。
等待签名卷时会记录
UFS 读取和失败计数，以及服务子树的 provider、Content、容量、Whole、Removable、
Ejectable 属性，并分别标明遍历是否完整和有效。SystemOS 是从窗口偏移零开始的
原始 APFS 容器，不新增 GPT 或替换媒体 UUID。签名卷门控保持启用；根卷挂载仍待实机验证。

先按[适配流程](/zh/porting/ufs)准备板级输入、修改源码、接入构建并逐步验证。

ShengUfs 提供 SM8550 UFS 存储路径，管理主机控制器、SMMU 映射、
命令队列和根卷读取。板级集成包含时钟资源、持久 Data 区域和日志区域。

| 字段 | 值 |
| --- | --- |
| 仓库 | [UniXNU/ShengUfs](https://github.com/UniXNU/ShengUfs) |
| 硬件 | SM8550 UFS 主机、PHY、时钟和 SMMU 资源 |
| 板级依赖 | 板级电源策略和存储布局 |
| 已验证设备 | [参考原生 UFS 根卷](/zh/devices/sheng#storage) |
| 运行时类 | `NativeUfs` |

组件目录提供内核 archive 构建命令。设备配置启用 `CONFIG_XNU_NATIVE_UFS` 并提供板级输入。

已校验的 userdata 窗口发布为内部虚拟存储。Disk Arbitration 可在控制台用户登录前
保留启动挂载，包括 `/System/Volumes/Preboot/Cryptexes/App` 下的 App cryptex。

```sh
./unixnu build kernel -D /path/to/device
```

## 移植与记录

ShengUfs 由设备仓库作为子模块提供。阅读 [UFS 移植指南](/zh/porting/ufs)，
按[设备存储记录](/zh/devices/sheng#storage)维护控制器、时钟、PHY、SMMU、DMA、
分区和完成条件。

arm64e 的公开源码构建方法及当前链接、运行时限制见[公开源码驱动构建](/zh/porting/migration#source-drivers)。历史 iOS 17 真机结果不代表该路径已通过验证。

## 命令路径

NABU 的板级适配使用 SID `0x300`，会独立发现固件已有的精确映射，
不受顶部八个 bank 分配窗口限制。复用前检查 stream 和 bank 独占、
stage 1 关闭、stage 2 bypass 和 VA64，保留所有上下文寄存器并重新校验。
SMMU 操作前停止传输和任务管理两个请求列表。这些路径已覆盖主机故障测试；
NABU 27.3 根卷交接仍需实机验证。

最多同时执行 32 条命令，每条命令占一个槽。请求列表位于单独的写合并页。
命令完成使用控制器中断加混合轮询：等待先轮询 200 微秒，再睡眠。页对齐缓冲区
走零拷贝 DMA，每条命令最多 1 MiB。在参考板型上，每项读取测试都不低于主线
Linux 驱动，见 [UFS 性能](/zh/porting/ufs#performance)。

## Wi-Fi DMA 窗口

WCN7850 DMA 窗口为 128 MiB，IOVA 从 0x10000000 起，使用独立
SMMU 上下文中的 64 个 2 MiB 块。设备验证已达到 Wi-Fi 接口就绪和
网络传输，DMA 分配失败为零；板级 `scripts/check-wifi-dma.py` 检查
容量和分配边界。
