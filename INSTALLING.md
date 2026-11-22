# nabu ESP 与对齐启动菜单

适用于现有 nabu macOS27.3 / 26C5035r 环境的 ESP 更新。本文不包含首次分区、ROOT安装或其他设备刷机流程。

## 固定菜单

仓库中的 [`config/unixnu.aligned.toml`](config/unixnu.aligned.toml) 为当前对齐版，4,909字节。

```text
a857f55144484709ca6fc174a25c3eee0705632be28516d485f98e2a746db72b
```

| 设置 | 值 |
| --- | --- |
| 默认项、第一项 | `a640-metal`：macOS 27.3 - A640 Metal GPU |
| 第二项 | `a640-metal-init`：显式确认后初始化 Data 并走 GPU 路径 |
| UI | `[ui] scale = 3` |
| CPU 回退 | `xnu` |
| 其他入口 | `coldstart`、`install`、`reset`、`shutdown` |
| 普通启动 | 保留 `data-persist=2`，不带 `data-reset=1` |
| Data 初始化 | 只在 `install` / `a640-metal-init` 的确认参数中加入 `data-reset=1` |

其余入口参数与对应 P047 包一致。脚本会比较现有包与对齐版的启动参数；若今后内核需要新的参数，必须先更新模板，不能用旧模板静默覆盖。

## 更新已有 ESP

在 Linux 或恢复环境中先挂载已有 ESP 到 `/esp`，确认该目录确实是目标 ESP。以下命令只覆盖 ESP 中的 EFI 文件；正常更新不选择初始化 Data。

```sh
ESP_ZIP=/absolute/path/to/esp-aligned.zip
unzip -o "$ESP_ZIP" 'EFI/*' -d /esp
cp config/unixnu.aligned.toml /esp/EFI/UniXNU/unixnu.toml
sync
```

这里的 `config/` 是本仓库根目录。维护者原本的本机配置位置为 `/home/baimao/nabu_macos/config/unixnu.aligned.toml`；仓库副本与该文件的字节、SHA256相同。

重新启动后选择 **macOS 27.3 - A640 Metal GPU**。只有需要清除并重新初始化 macOS Data 时，才选择初始化项并确认。

## 校验和规则

新 `*-aligned.zip` 已内置同一份对齐菜单，包内 `SHA256SUMS` 已按最终文件重算，全部 EFI 文件应通过：

```sh
unzip -p "$ESP_ZIP" SHA256SUMS > /tmp/nabu-esp-SHA256SUMS
(cd /esp && sha256sum -c /tmp/nabu-esp-SHA256SUMS)
```

旧 ESP 包携带约3,761字节的通用菜单。对旧包执行“解压 EFI、再复制对齐版”的流程后，用旧包 `SHA256SUMS` 检查时，**仅 `EFI/UniXNU/unixnu.toml` 不匹配是预期的**，因为它已被明确替换。应另外核对安装菜单的完整SHA256；其他EFI文件失败仍应查明，不能一概忽略。

```sh
sha256sum /esp/EFI/UniXNU/unixnu.toml
```

## 打包维护

完整 GPU ESP 的 [`integrate-gpu-esp27.py`](devices/nabu/scripts/integrate-gpu-esp27.py) 已接入对齐菜单。先集成 addon / trust，再安装菜单，最后生成清单、FAT映像及ZIP，避免再次把菜单改回通用版。

[`align-esp-package27.py`](devices/nabu/scripts/align-esp-package27.py) 可给已有验证包补入菜单并完成FAT读回、清单重算和ZIP校验，使用见 [BUILDING.md](BUILDING.md#最终-esp-菜单与打包)。

驱动现状见 [STATUS.md](STATUS.md)，最新工程进度见 [PROGRESS.md](PROGRESS.md)。
