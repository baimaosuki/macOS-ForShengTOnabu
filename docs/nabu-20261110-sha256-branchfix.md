# SHA-256 常量被改写成分支：根因、修复与验证（uniform18）

日期：2026-11-10。产物：`out/nabu-uniform18.branchfix.nabu`（112,398,336 B，
md5 `e7eb37062af52d104071dac915287950`，
sha256 `0b8aad3a83cb5d032677c6d2bc35bad329c3106c3e099c5940f61fe67787789b`）。
上一版（设备现役）：`out/nabu-uniform17.zlib.nabu`，md5 `bedc76c39c75edb49c2d2ec38dffcc35`。

## 1. 判据

设备侧现象：`cs_validate_page ... off 0x1c000 size 0x4000`、`vm_fault_enter ...
*** INVALID PAGE ***`、`pid 1 exited -- exit reason namespace 3 subcode 0x2`、
`CS_KILLED initproc failed to start`。内核计算出的页摘要与嵌入 CodeDirectory
的槽位不一致，因此重新签名 dyld、放宽 AMFI 都不可能影响这一判据。

## 2. 根因

`scripts/prelink_fileset_kernel.py` 的原分支迁移按 4 字节步长扫描 `__TEXT_EXEC`，
凡 `word & 0xFC000000 in (0x94000000, 0x14000000)` 即当作 B/BL 改写，并用
`bind_kernel()` 的"精确优先、否则最近符号＋偏移"把目标映射到本内核。
SHA-256 的 `K[31] = 0x14292967` 恰好落在 `0x14......`（B 编码），于是数学常量被写成
分支：`67 29 29 14` -> `3c 28 9b 16`（0x169b283c）。

本镜像内共有 6 份 SHA-256 K 表，被改坏的两份位于放置后的 kext 文本
（`__PLK_TEXT_EXEC`，file 0x50064d0 / 0x500d410 -> VA 0xfffffe000d4294d0 /
0xfffffe000d42c410）。0xfffffe000d4294d0 + 31*4 = **0xfffffe000d42954c**，
正是现场指向的地址。

运行期使用哪一份：加载的 `com.apple.kec.corecrypto` 自带一份 K 表，内核符号
`_ccsha256_K`（0xfffffe0007363fc0）只是另一份拷贝。设备打印的 `actual` 与用
放置副本（K[31] 被改坏）执行的摘要逐字节相符，见第 3 节。

## 3. 证据（全部来自镜像与现场，不依赖"最近符号"推断）

1. 常量审计（`scripts/audit_kernel_crypto.py`）：旧镜像 `sha256_k BROKEN`，
   两处损坏 `0x50064d0`、`0x500d410`；新镜像 6 份全部 INTACT。
2. 数据字统计（打包器内置诊断）：`__TEXT_EXEC` 内 1,652 个"形似 B/BL"的字，
   其中 273 个的"目标"会绑定进内核文本——这正是旧扫描会改写的**数据字**。
3. 新旧镜像逐字比较：**275** 个 4 字节字不同 = 273 个数据字 + 2 个被拒绝的
   非精确分支（旧版给它们编造了地址）。
4. 用镜像自身代码执行 SHA-256（`scripts/verify_kernel_sha256.py`，Unicorn，
   调用 `_ccsha256_ltc_compress`，x0=state、x1=nblocks、x2=data，常量取自镜像）：
   - 旧镜像 + 放置副本 K 表（K[31]=0x169b283c）对 dyld 页 0x1c000 得
     `77485dc1b97da8c1dafb0f26bf89310d03c16827a775668d1a91f39a72c0d52c`；
     现场打印 `actual c15d4877 c1a87db9 260ffbda 0d3189bf 2768c103`（LE 下逐字
     字节序反转）-> `77485dc1 b97da8c1 dafb0f26 bf89310d 03c16827`，**完全一致**。
   - 新镜像同一调用得
     `72344650985022fc45d8a0e10bc270206cd4299f75380548b6b01546fe68f4a7`，
     等于 CodeDirectory 槽位 7，也等于现场打印的 `expected`
     `50463472 fc225098 e1a0d845 2070c20b 9f29d46c`（字节序反转）。
   - 8 个消息向量（0/3/55/56/64/256/43/1024 B）与 2 个 dyld 页（0x1c000、0x20000，
     各 16 KiB）全部与 hashlib 相符。

## 4. 修复

`scripts/prelink_fileset_kernel.py`：

1. `segment_64` 解析保留 section `flags`，代码区间取自镜像自身的
   `S_ATTR_PURE_INSTRUCTIONS|S_ATTR_SOME_INSTRUCTIONS`，不再靠段名猜测。
2. 分支候选改为**递归下降**：种子 = `LC_FUNCTION_STARTS` + 符号表中的代码符号，
   沿 B/BL/B.cond/CBZ/TBZ 目标扩散，遇 B/BR/RET/BRAA/RETAA 停止；伪指令池只有被
   真实代码跳入才可能进入候选集，因此常量表不会被改写。
3. 绑定必须留下证据：精确命中符号；或内部偏移满足"目标仍在 Apple 同一符号的
   区间内（小于下一个符号）且映射后落在本内核可执行文本"。其余一律拒绝并列出，
   需要时用 `--branch-manifest` 逐个登记（源文件不再提供任何"最近符号＋偏移"
   静默兜底）。
4. 事后断言：整镜像快照对比，改动集合必须等于已记录的分支站点集合。
5. 常量闸门：打包末尾调用 `audit_kernel_crypto.audit()`，任何**存在但损坏**的常量
   表直接失败（ABSENT 不算失败）。默认严格模式；`--allow-unresolved-branches`
   只把拒绝项写入报告，不改变"未验证就不改写"的行为。

统计（uniform18）：`branch_rewrites_kernel 19970`、`branch_skipped_local 48186`、
`branch_outside_kernel_text 5`、`unproven_branch_words 1652`、
`branch_data_words_refused 273`、`branch_diff_words 275`、
`pointer_rewrites_external 13740`（全部 exact）。

## 5. 残留阻断（与 SHA-256 无关，均已在报告 `branch_refused` 中列明）

| 组件 | 站点 | 目标 | 情况 |
| --- | --- | --- | --- |
| com.apple.kec.corecrypto | 0xfffffe000b42954c | 0xfffffe000be73ae8 | 最近符号偏移 1188，超出该符号区间（属于未放置的 kext） |
| com.apple.kec.corecrypto | 0xfffffe000b43048c | 0xfffffe000be7aa28 | 最近符号偏移 988，同上 |
| AppleFSCompressionTypeZlib | 0xfffffe000919c228 | 0xfffffe000d721e88 | 目标在未放置组件内，无法按名绑定 |
| com.apple.filesystems.apfs | 0xfffffe000b2d94d8 | 0xfffffe000bde5e94 | Apple `_xts_encrypt` 是文本符号，本内核同名符号在 `__DATA_CONST` |
| com.apple.filesystems.apfs | 0xfffffe000b2dd574 | 0xfffffe000bde5fc8 | 同上（`_xts_decrypt`） |

这 5 个站点保持 Apple 地址；只有真正执行到才会触发异常。旧版对前两个"编造"了
地址（最近符号＋偏移），那正是本次被删除的兜底行为。

## 6. 刷入与回退

产物已复制到 Windows：`D:\nabu\work\flash\root\art\kernel.uniform18.branchfix.nabu`
（回退件 `kernel.uniform17.rollback.nabu`，md5 `bedc76c39c75edb49c2d2ec38dffcc35`）。

```powershell
# 设备：音量下 + 电源进入 fastboot
D:\nabu\platform-tools\fastboot.exe boot D:\nabu\dl\twrp\recovery-win.img
D:\nabu\platform-tools\adb.exe wait-for-device
D:\nabu\platform-tools\adb.exe shell "mkdir -p /esp; mount -t vfat /dev/block/sda31 /esp"
D:\nabu\platform-tools\adb.exe push D:\nabu\work\flash\root\art\kernel.uniform18.branchfix.nabu /esp/kernel.macho
D:\nabu\platform-tools\adb.exe shell "sync; md5sum /esp/kernel.macho"   # 期望 e7eb37062af52d104071dac915287950
D:\nabu\platform-tools\adb.exe shell "umount /esp; reboot"
```

回退：把 `kernel.uniform17.rollback.nabu` 推到同一路径，md5 应为
`bedc76c39c75edb49c2d2ec38dffcc35`。`unixnu.toml`（md5 `ff444861d5ad7fab1b56cbe20cd11516`）
与 `kernel.macho.adt`（md5 `7bd4febe164ef29b6122d71ba9104cc4`）本次不变。

## 7. 验收口径

本轮的判据是 dyld 页不再报 `INVALID PAGE`，即内核算出的页摘要等于 CodeDirectory
槽位；`exec: launchd pid=1` 只是激活标记，仍不算成功。真正的成功线是 dyld 用户态
（launchd 进入 `__dyld_start` 之后的用户态执行）。
