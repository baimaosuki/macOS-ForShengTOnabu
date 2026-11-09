# 跳转表漏绑定导致的回退：纠正、最小修复与扫描器补丁（uniform19 / uniform20）

日期：2026-11-10。承接 `docs/nabu-20261110-sha256-branchfix.md`，该文第 3 节第 2 条
（"273 个数据字被保护"）**作废**，本文件给出实测结论与纠正。

## 1. 回退事实

uniform18（递归下降版）启动时 PC 落在 `0xfffffe000d6963bc`，该地址在镜像里全为零，
所以报 `instr=0`。溯源：

| 项 | 地址 |
| --- | --- |
| Apple 内核 `_kfree_data` | `0xfffffe000b6963bc` |
| ＋本包滑移 `0x2000000` | `0xfffffe000d6963bc` = panic PC |
| 本内核 `_kfree_data` | `0xfffffe00073c39e8` |
| 本内核 `_IOMallocTypeImpl` | `0xfffffe0007aaea38` |

`AppleMatch::_matchUnpack` 里两条调用在三个包中的实际目标（本机实测解码）：

| 站点 | uniform17 / uniform19 | uniform18（回退版） |
| --- | --- | --- |
| `0xfffffe000b63d008` | `0xfffffe00073c39e8` `_kfree_data` | `0xfffffe000d6963bc`（Apple＋滑移，零区） |
| `0xfffffe000b63d014` | `0xfffffe0007aaea38` `_IOMallocTypeImpl` | `0xfffffe000de011d0`（Apple＋滑移，零区） |

## 2. 原因（我上一轮的判断错误）

`instruction_sites()` 的递归下降在 `br xN` 处终止，且不解析跳转表，于是跳转表后面的
真实代码属于"未到达区域"。上一轮的实现把这片未到达区域的 B/BL 字样**当作数据跳过**，
导致真实调用没有重定位。实测该区域的 273 个 B/BL 字**全部是旧包已重定向、目标精确命中
Apple 内核符号的调用**（示例：`_memcpy`、`_kfree_data`、`_IOMallocTypeImpl`），
不是常量表。因此：

- "273 个数据字被保护"不成立，改为"273 处跳转表后的真实调用被漏改"；
- 未到达 ≠ 数据，不能作为跳过重定位的理由。

## 3. 立即修复：最小候选 uniform19

按"以能跑到 dyld 的 uniform17 为基底，只改已确认的两个 SHA-256 常量"执行，
`scripts/patch_sha256_constants.py` 带断言写入（旧值不符即拒绝）：

| 文件偏移 | 旧值 | 新值 |
| --- | --- | --- |
| `0x500654c` | `0x169b283c` | `0x14292967` |
| `0x500d48c` | `0x169b1414` | `0x14292967` |

产物 `out/nabu-uniform19.sha256fix.nabu`：112,398,336 B，
md5 `f8238cd37a8e5a3dbdf8a31ace8eb179`，
sha256 `6bc4fe81002bab40b2b2e4de85f0ad82c97dd2fa11282b34bc2d7fa4bacccafb`，
`changed_words = 2`（断言：只允许这两处不同）。

## 4. 扫描器补丁

`scripts/prelink_fileset_kernel.py`：

- 未到达区域重新定义为**未知**，并统计 `code_gap_bytes`（本包 `57664` 字节）；
- 未知区域内的 B/BL 候选**只有在目标精确命中本内核符号**时才重定向，绝不使用
  "最近符号＋偏移"；每条都记录进报告（`branch_unknown_sample`，本包 273 条，
  例如 `0xfffffe0009197f8c -> _memcpy`）；
- 诊断措辞去掉"data"，改为"walk did not reach / exactly-bindable"；
- 常量表闸门仍是硬后置条件（`audit_kernel_crypto`），数据即使形似分支也无法带着
  损坏的常量表交付。

重建成 `out/nabu-uniform20.jumptable.nabu`：

```
branch pass: 20243 rerouted (273 behind jump tables), 49564 intra-component kept,
             6 outside kernel text, 4 unresolved, 2 non-exact refused
uncovered code bytes in __TEXT_EXEC: 57664 (unknown, not data)
unknown-exact sites: 273
branch pass diff: 20243 word(s) changed, all recorded
```

20,243 = 19,970（递归下降证明的）+ 273（跳转表后精确命中）；与旧包 20,245 相差的正是
被恢复的两个常量字。**与 uniform19 逐字节一致（0 个不同字，md5 相同）**，即设备上现在
跑的就是这份内容。

## 5. 验证

| 项 | 结果 |
| --- | --- |
| 常量审计 | `sha256_k` 6 份全 INTACT（`0x49840 0x57220 0x35ffc0 0x24425f0 0x50064d0 0x500d410`）、`sha256_h` 3、`sha1_h` 2、`aes_sbox` 1；`sha512_k/sha1_k/md5_t` ABSENT |
| 镜像自身代码执行 SHA-256 | 10/10 通过（0/3/55/56/64/256/43/1024 B + dyld 页 `0x1c000`、`0x20000`），K[31] native 与 placed 均为 `0x14292967` |
| dyld 页 `0x1c000` | `7234465098…6fe68f4a7` = CodeDirectory 槽位 7 |
| 两条 AMFI 调用 | 均指向本内核 `_kfree_data` / `_IOMallocTypeImpl` |

## 6. 仍未做

- 真正的跳转表目标追踪（逐个函数解析 `adr/adrp+ldr+br` 表），现在用"未知区域＋精确
  符号绑定＋常量闸门"兜住；
- 6 处仍保持 Apple 地址的分支：

| 组件 | 站点 | 目标 | 情况 |
| --- | --- | --- | --- |
| corecrypto | `0xfffffe000b42954c` | `0xfffffe000be73ae8` | 最近符号偏移 1188，超出区间（未放置 kext） |
| corecrypto | `0xfffffe000b43048c` | `0xfffffe000be7aa28` | 偏移 988，同上 |
| AppleFSCompressionTypeZlib | `0xfffffe0009197288` | `0xfffffe000191dec8` | 目标不在内核文本 |
| AppleFSCompressionTypeZlib | `0xfffffe000919c228` | `0xfffffe000d721e88` | 目标在未放置组件内 |
| apfs | `0xfffffe000b2d94d8` | `0xfffffe000bde5e94` | Apple `_xts_encrypt` 为文本符号，本内核同名符号在 `__DATA_CONST` |
| apfs | `0xfffffe000b2dd574` | `0xfffffe000bde5fc8` | 同上（`_xts_decrypt`） |

## 7. 设备与回退

- 已刷入：`/esp/EFI/UniXNU/Payloads/kernel.macho` = md5 `f8238cd37a8e5a3dbdf8a31ace8eb179`
  （Windows 备份 `D:\nabu\work\flash\root\art\kernel.uniform19.sha256fix.nabu`）；
  活动槽已切到 **b**。
- 回退件：`D:\nabu\work\flash\root\art\kernel.uniform17.rollback.nabu`
  md5 `bedc76c39c75edb49c2d2ec38dffcc35`（= 设备上一版，能跑到 dyld）。
- 刷入/回退均通过 TWRP：先 `rm` 旧件腾空间（ESP 仅剩 7.1 MB），再 `adb push`，`md5sum`
  核对后 `reboot`。

判据不变：`cs_validate_page … off 0x1c000` 不再报 `INVALID PAGE`；真正的成功仍是 dyld
用户态执行。
