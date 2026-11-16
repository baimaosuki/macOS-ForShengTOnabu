# NABU macOS 27.3 shared-cache compatibility

The board uses 4 KiB kernel pages with the original 26C5035r arm64e shared
cache's 16 KiB v5 slide pages. The selected upstream
`native-4k-shared-region-slide.patch` pages each slide page in four parts.
NABU's no-PAuth policy must decode authenticated pointers in
`vm_shared_region_slide_part_v5`, as it already does in the full-page v3/v5
routines. Otherwise the pager reports success while authenticated slots retain
their encoded chain words. Chain-only peeks must leave the source unchanged.

`devices/nabu/tests/test_cache_split.py` compiles the fully prepared production
routines. With `--cache` pointing at the original cache directory, it compares
all populated pages against the full-page routine at three slides and fault
orders. `--previous-xnu` requires a mismatch from the previous production
source. ASan/UBSan additionally cover boundaries and refusal paths.
`verify_cache_split_native.py` executes the linked ARM64 function and requires
calls from the actual pager. These are host checks; they do not demonstrate
successful device startup or a visible desktop.

## 中文

NABU 的内核页为 4 KiB，原版 26C5035r arm64e 共享缓存的 v5 重定位页为
16 KiB。上游分页器将它拆为四部分处理，PAC 兼容分支也必须在
`vm_shared_region_slide_part_v5` 写回解码后的认证指针。遗漏这个写回时，
分页器仍返回成功，但内存中保留的是磁盘链编码。只读遍历不得改写源页。

主机测试使用完整准备后的生产函数，对原版缓存所有含链的页面按三种
slide 和缺页顺序与整页实现比较，并用旧源码重现反例。另以 ASan/UBSan
检查 4 KiB 边界，用 Unicorn 执行最终 ARM64 机器码。它们不等于实机
启动成功或桌面已显示。Data 初始化不是本项修复，不应重复重建 Data。
