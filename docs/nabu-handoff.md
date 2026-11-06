# nabu macOS 26.5 — 早上接设备一张纸

> 详细过程见 [夜间报告](nabu-night-report-20261107.md)（407 行）。
> 全程未 flash 任何分区，`boot_b` 未动。

## 现在能跑的东西

```
fastboot boot out/nabu/image/nabu-macos26-kernel.img
```

镜像 24,813,568 B，sha256 `63ef91fed07924190cc7b0fd93badf904448082ed9f59b017b5b5f2b110cbda3`。

一键脚本（推荐）：

```bash
cd ~/.gemini/antigravity/scratch/macOS-ForNabu/macOS-ForSheng
./scripts/nabu_device_session.sh check     # 只读：镜像哈希 + slot 状态
./scripts/nabu_device_session.sh backup    # ESP 字节备份
./scripts/nabu_device_session.sh boot      # RAM 启动
./scripts/nabu_device_session.sh collect   # 事后收日志
```

任何子命令加 `--dry-run` 可先看会执行什么。

## 屏幕上应该看到什么

1. `DARWINBOOT NABU SM8150 - FASTBOOT RAM LOADER (macOS 26.5 / 25F71)`
2. `NRAM PA` / `KERNEL BYTES 0x17a3880` / `HANDOFF -> ENTRY PA 0xc059b488` / `ARGS PA 0xc18c0000`
3. XNU 详细输出（`-v debug=0x14e keepsyms=1 cpus=1`）
4. 要么 panic 回溯，要么停在 `Darwin Kernel Version` 之后

60 秒无输出：电源 + 音量下 回 fastboot。

## 已验证的事实（不用再验）

| 项目 | 结果 |
|---|---|
| 内核 Mach-O | `__TEXT_EXEC` 基址 `0xfffffe0007004000`，入口 `0xfffffe000759f488`，span `0x18c0000` |
| DarwinBoot 解析器复刻 | accepted = **True**（与 `ParseKernel` 同规则） |
| ADT 严格契约 | **14/14** 通过，0 警告 |
| stage1 契约（QEMU 实跑） | **PASS**——NRAM `0x81000000`、KERNEL BYTES `0x17a3880`、ENTRY PA `0xc059b488` |
| XNU 在 QEMU 里的停止点 | `_timer_queue_assign.cold.1`（`arm_timer.c:217` 的 assert），说明已跑到 Mach 初始化 |
| 内核 PAC 指令 | 全 0 处，与 A76 匹配 |
| boot 头地址 | `kernel 0x80008000 / ramdisk 0x81000000 / tags 0x80000100`（SM8150 合法值） |
| 内核 ↔ ESP 载荷 | 同一颗 `83ec42f2…`；ADT 同一份 `0d262564…` |
| 用户态 PAC 降级 | dyld `585d721b…`、launchd `c9bf83af…`（arm64e→ARMv8.2，指令改写记录在报告 §7） |

## 带回来的东西

1. panic 屏幕照片（或“停在某一行”的照片）
2. `fastboot getvar all 2>&1 | grep -E 'slot-|current-slot'`
3. `./scripts/nabu_device_session.sh collect` 抓到的 `private/nabu-backup/dmesg.txt`

## 已知做不到的（原因）

- **Apple kext 重链**：链式修复表在 Apple 构建时已解成绝对地址（`imports_count = 0`），
  槽位与符号名的对应已不存在；符号层面对齐度其实有 81.6%（apfs 86%），但无法反推引用。
- **BaseSystem.dmg**：`022-21678-092.dmg.aea` 是 `Personalize: true` 的个性化 AEA，
  需要 Apple TSS 返回的密钥，离线无解。
- **完整 dyld shared cache**：同上，随 BaseSystem 一起被锁。
