# GopFramebuffer

先按[适配流程](/zh/porting/display)准备板级输入、修改源码、接入构建并逐步验证。

使用固件 GOP 交接的 framebuffer 呈现。

[源码仓库](https://github.com/UniXNU/GopFramebuffer).

实现通过 PE_current_console 读取固件 framebuffer，并集成所选 MobileFramebuffer ABI。板级接口提供显示策略和 GPU scanout 集成。

直接显示路径在首次成功查询 GPU fence 时固定该次交换的等待目标。锁忙或查询不可用时，间隔 200 微秒重试。等待达到 250 毫秒时记录慢帧并继续等待。目标完成或交换取消前，屏幕保留上一帧；取消交换会清理重试定时器和等待目标。

## 集成

组件目录记录实现范围、硬件与 OS 要求、板级依赖、源码版本、构建阶段、输出和运行时标识符。所选配置按依赖顺序运行构建命令。

```sh
./unixnu build kernel -D /path/to/device
```

[组件目录](/zh/reference/components)

帧诊断保留分配、命令、完成处理及 GPU、显示时间线，像素抓取关闭。使用方法见 [GPU 帧诊断](/zh/porting/gpu-diagnostics)。

帧诊断从已准备好的直接显示槽位读取物理地址。验证阶段尚未映射时记录零地址，提交切换时补充映射地址。诊断观察代码不准备描述符，也不查询其物理段。

NABU 的 macOS 26C5035r 显示容器子节点使用原生具体类 `AppleDisplay`。
`IODisplay` 为抽象类，其元类分配函数直接返回空；不能用它实例化 EDID 节点。
该子节点仅挂接 EDID/container 信息，不启动或注册第二套显示驱动。
此修正已核查原始分配函数，尚未完成新内核编译与实机出图验证。

NABU CPU 日志记录前十帧、每五十帧、状态变化及首个非黑帧。请求掩码与槽位和
保留的活动图层分别记录。源采样覆盖 BGRA 与 RGhA；FP16 同时记录原始位模式
非零及转换后的采样值。不支持的十位格式继续拒绝，并输出原因。

NABU macOS 26C5035r: native `isSwapLayerEnabled_gated` reads the bit at
request +0x478. +0x47c is accumulated by
`update_swap_completed_state_gated`; it is not a disable mask. Updated nonnull
IOSurface slots enter CPU composition; updated null slots clear the plane.
The CPU source log names the latter word `completed`, including independent
sampling of mapped incoming sources that are not selected for scanout.

For this firmware-owned primary panel, the owned override of native slot 345
(`setAPIPowerState`) rejects API power-off. The PM callback acknowledges while
keeping scanout. An all-null swap following rejected sleep keeps an existing
nonblack scene; ordinary explicit clears remain supported. This hold does not
claim support for physical panel power cycling. The v36 observer no longer
starts repeatedly failing caffeinate helpers. These source/replay checks still
require hardware validation of visible desktop content.
