# NABU macOS 27.3 的 zeta 普通指针 ABI

来源固定为 UniXNU-zeta `eb489285a1d9153872f00c93ebb519a269b249b5`，许可及原始文件 SHA-256 位于 `devices/nabu/scripts/zeta_pac/`。

生产策略是离线识别代码并改写 PAC 指令：PAC/AUT/XPAC 保持普通指针不变，PACGA 返回 0，鉴权跳转/返回转普通跳转/返回，支持的鉴权加载转 LDR/LDUR。无法证明是代码的数据不改写，剩余代码使用语义相同的内核异常兼容路径。没有硬件 PAC 密钥或密码学鉴权保护。SHA3/DIT/SB 等非 PAC 兼容保留。

`nabu_zeta_pac.py` 在原始 trust cache 中确认输入 CodeDirectory 并验证其页哈希，从 universal 文件选 arm64e 切片，调用固定上游的代码证明及指令转换器。macOS CMS 被新的 ad-hoc SHA-256/4 KiB 签名替代，保留 entitlement、约束和平台值。输出的新 cdhash 必须纳入交付 trust cache。

`rewrite-nabu-zeta-cache.py` 校验完整输入清单，调用同一转换器；内核 v3/v5/分片页的 fixup 必须生成普通地址，保留 ASLR。原 corecrypto HMAC 匹配的字段在代码变更后重封。`nabu_zeta_fileset.py` 在原生分支重定位之后、组件 FIPS 重封之前改写保留 kext 的已证明代码。旧 XOR 热点规则不再装载。

普通指针内核不能与旧系统镜像混用。prelink 要求 `NABU_ZETA_RUNTIME_REPORT` 指向 complete=true、ABI 一致、根镜像 SHA-256 相符的运行时报告。持久 Data、CPU 显示及用户日志不因迁移而清除。host 测试和 CI 不代表平板已经成功进入桌面。

验证：`test_zeta_pac27.py --xnu PREPARED --output OUTPUT` 比较真实内核 C 与 Unicorn 执行的上游替换指令；`test_zeta_macos_sign27.py` 覆盖切片、签名、entitlement 和篡改失败路径。Intel CI 会执行上述兼容对照。
