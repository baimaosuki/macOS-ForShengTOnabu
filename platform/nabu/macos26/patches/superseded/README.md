# 已作废的板级补丁

`nabu-managed-rt-normal.patch` 的上下文来自 iPadOS 17 的
`osfmk/arm/pmap/pmap.c`（`pmap_panic_dev_wimg_on_managed` 附近），
与 macOS 26 的行号与邻域不符，无法应用。

同一机制已由基座序列提供：
`platform/sheng/macos26/patches/0063-native-managed-ram-writecombine.patch`。
nabu 只需在 boot-arg 里保留 `t733-managed-rt-normal=1`。
