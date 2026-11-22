# nabu macOS27.3 进度记录

更新：2026-11-22。设备：小米平板5 / nabu，SM8150-AC / Adreno640。系统：macOS27.3 / 26C5035r，XNU12377.121.6。编译SDK：26.5，普通arm64与Zeta PAC路径。

这是目前的实验源码快照，后续开发暂时停更。当前对外硬件结论仍是：**USB不工作、触控工作、GPU疑似工作、缺多项驱动**。

## 阶段记录

| 阶段 | 已有进展 | 实机/验证边界 |
| --- | --- | --- |
| 系统启动、UFS与Data | 已能启动并出现系统界面；保留持久Data路径 | 尚非日常可用系统，完整休眠/恢复未验收 |
| 无PAuth平台适配 | 保留Zeta PAC与普通arm64编译/预链接契约 | 不将SM8550/arm64e直接套到SM8150 |
| 显示 | 保留NativeLegacyDisplay、CPU回退与A640显示交接 | 实机仍有卡顿，未给出稳定帧率结论 |
| GPU | 保留Metal→AdrenoMetal→M2V→Turnip→A640链路源码；历史实机记录已出现数千次fence完成及渲染 | GPU完整稳定桌面合成与性能仍待确认；源码/CI通过不作为性能验收 |
| NT36523触控 | 当前实机反馈可用 | 多点手势与全部坐标场景未完整验收 |
| USB P043—P045 | 控制器、部分命令、供电、中断和事件环已有进展；修正MMIO与真实DMA分配路径 | 鼠标仍未工作 |
| USB P046 | 真机TRB记录抓到Setup/Data/Status完成目标为1；原生单中断路径使用0号环 | 未取得成功的GET_DESCRIPTOR数据完成及HID鼠标操作 |
| USB P047 | 提交前把输入上下文与各端点TRB目标映射到0号环，保留原生完成处理；覆盖EP0和鼠标中断IN | 新包已构建，但尚无P047鼠标移动/点击成功的实机证据 |
| ESP菜单 | 纳入4,909B对齐配置，默认GPU、UI scale3、GPU初始化确认入口 | 旧包手工覆盖与新版完整校验规则已写明 |
| 其他驱动 | 音频、无线、相机、传感器及完整电源管理等缺失或未完成 | 不列为可用 |

## 最新代码与构建记录

- P047驱动提交：[`bf88431`](https://github.com/baimaosuki/macOS-ForShengTOnabu/commit/bf8843104b08006a94056bc20156926a2c11800b)。
- 原生内核与boot-tools：[CI45012322361](https://github.com/baimaosuki/macOS-ForShengTOnabu/actions/runs/45012322361)，通过。
- GPU用户态复用：[`9488928`](https://github.com/baimaosuki/macOS-ForShengTOnabu/commit/9488928fe17c5d601ed19ea2fad3554244a6c325) / [CI44892556083](https://github.com/baimaosuki/macOS-ForShengTOnabu/actions/runs/44892556083)，本轮未重编。
- P047本地验证：10项ASan/UBSan检查、SDK26.5编译链接无缺失符号、原始27.3完成分发回放、实际产物ARM64路由回放。
- P047最终验证：15项二进制/ABI/PAC/signed-text检查、FAT读回、ZIP CRC通过。
- 菜单更新验证：配置安装/初始化保护测试、真实签名GPU addon集成、完整ESP重打包、FAT读回与全部EFI哈希检查通过；不触发新内核或GPU CI。

## 当前验证包

文件名：`nabu-27.3-26C5035r-esp-usb-event-route-bf88431-aligned.zip`

| 内容 | SHA256 |
| --- | --- |
| 对齐菜单 | `a857f55144484709ca6fc174a25c3eee0705632be28516d485f98e2a746db72b` |
| ESP ZIP | `bf739176bdf3aa22f38414a03dac7c0e0a30e8324716728741d49870a721cd95` |
| 内核 | `2ae94d8dad5efef79327417db11ed96de6e9b3f47b787439f69629e18aa98574` |

菜单更新保持内核、GPU addon与trustcache字节不变。此表记录维护者本地产物标识，不表示GitHub提供系统二进制下载或已完成真机验收。

## 接续工作的验收点

1. P047启动后检查事件目标为0、GET_DESCRIPTOR成功和有效设备描述符，随后确认原生USB HID匹配及鼠标移动/点击/滚轮。
2. 用同一启动记录验证GPU任务持续完成、无stall/进程崩溃，并测量帧率与输入延迟。
3. 对其余硬件逐项建立实机记录。暂不承诺后续时间表。

入口：[当前状态](STATUS.md) · [ESP使用](INSTALLING.md) · [源码范围](OPEN_SOURCE.md) · [构建说明](BUILDING.md) · [上游与许可](UPSTREAM.md)。
