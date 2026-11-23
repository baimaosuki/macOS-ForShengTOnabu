# 公开仓库复现审计：缺项、修补与验证边界

日期：2026-11-23。起点：重新克隆公开 `main@65f158b`，未恢复已删除的维护者 `out/`。操作教程见 [REPRODUCING.md](REPRODUCING.md)。

## 结论

PAC核心算法、内核fallback、macOS签名适配和A640源码已公开；不是只有二进制。但原公开树**不能按原BUILDING直接从零复现整套可启动ROOT/ESP**。旧PAC文章是ABI说明，未串好系统提取、APFS写入、签名策略、信任缓存和最终交付。

本轮补齐下表中的源码/工具缺口，同时把未能验证的阶段明确留下。**不将文档和Host测试通过描述为完成新的实机复现。**

## 已发现并处理

| 缺项/缺陷 | 原位置/触发条件 | 本轮补充 |
| --- | --- | --- |
| PAC教程没有完整命令链 | `docs/zh/nabu-27.3-zeta-pac.md` 只有原理 | `REPRODUCING.md` 记录主机、输入、命令、输出和失败条件，原文章互链 |
| 原始27.3提取路径靠本地脚本 | 已删除 `make-root-image.py` 等 | `prepare-nabu-inputs27.py` 使用公开IPSW/UDIF实现，输出三卷原始raw、APFS偏移、trust和kernelcache清单 |
| 缓存改写要求 `--input-manifest`，却没有生成入口 | `rewrite-nabu-zeta-cache.py` | `make-nabu-zeta-cache-manifest.py` 按真实Cache.files顺序计算完整输入哈希 |
| 原始volume到guest写入plan未连接 | 本地volume导出与改写覆盖步骤缺失 | `prepare-nabu-volume-plan27.py`：只读导出、owner/mode/symlink记录、普通Mach-O及共享缓存overlay合并 |
| 新ROOT没有公共组装/策略汇总入口 | prelink要求 `NABU_ZETA_RUNTIME_REPORT` | `assemble-nabu-root27.py`：实际write报告/镜像哈希绑定、卷UUID核查、C/RSA逐块验证、SHNGROOT v2封装和readback、trust汇总 |
| 转换后卷哈希仍使用原始profile | 内核storage校验与新ROOT不配对 | 本地 `root.local.toml`、CLI `--root-profile` / `UNIXNU_ROOT_PROFILE`；只允许三卷hash/固定UUID及text-fixups覆盖 |
| signed-text preimage与离线PAC转换可能冲突 | 原修复输入中包含PAC序言 | 生成本地fixups前先核对原始字节，再读取转换后的实际preimage；replacement不变，仍走signed-text机制 |
| 新root owner anchor难以送入CI | 公开策略仅留JSON，未公开维护者原`.bin`输入 | `NABU_ROOT_POLICY_DIR` 显式输入；native CI要求自己的公开策略目录和匹配root profile，不默认复用维护者anchor |
| 运行时固定维护者接收方 | `build-fresh-runtime.py` | 强制 `--recipient` 指定自己的RSA公钥；CI新增相应输入，空值失败 |
| 没有公开运行时解密入口 | 只生成 `runtime.enc` / envelope | `unpack-fresh-runtime27.py`：RSA recipient匹配、GCM认证、私有权限、archive路径限制及全部文件哈希校验 |
| 运行时工具安装器只能在CI用 | 直接访问 `GITHUB_PATH` | 本地可运行并输出PATH设置方式 |
| Linux制作后报告内路径无法在macOS使用 | prelink按root_image路径读文件 | `relocate-nabu-root-report27.py` 重新哈希完整镜像，只重绑定本机路径 |
| 触控输入清单漏元数据 | ADT实际还读 `partitions.bin` / `crc-bank.bin` | 补文件清单；`prepare-nabu-touch-firmware27.py` 按固定Xiaomi/Novatek格式解析，严格限定精确固件hash |
| ADT没有实际写入GPU固件 | DTS `nabu,private-gpu-firmware` 未被Python消费 | ADT生成器写入四份固件并匹配内核既有长度/SHA256校验 |
| 通用CLI拒绝传入已签名启动addon | `gpu_addon=false`，但boot_shims=true | 保留禁用自动GPU插件的含义，允许显式boot-shim addon/trust作为payload输入 |
| 首次无旧ESP/token不能安装菜单 | 对齐菜单只允许保留既有token | 明确 `--without-persistent-log` 首次构建选项，令ESP journal关闭；默认更新仍要求原token，不造日志分配 |
| 最终编包依赖旧ESP ZIP和本地Pxxx脚本 | P047打包器不在公开树 | 新 `pack-nabu-esp27.py` 从新EFI文件树生成包，验证输入配对、菜单、AARCH64 PE、FAT读回、全部hash和ZIP CRC |

## 仍需明确保留的缺项/验证条件

| 项目 | 当前证据 | 不能冒称完成的部分 |
| --- | --- | --- |
| 完整27.3三卷转换 | 已有转换算法；新汇总工具有Host测试 | 本轮没有下载Apple IPSW、完整转换真实OS/SystemOS/App并引导；原始素材仍需构建者准备 |
| APFS文件元数据 | 新导出记录uid/gid/mode/symlink，独立reader读取BSD flags；guest回读 | 新导出暂不复制ACL及全部xattr；不能据此声称与原系统完整元数据等价，必须进一步实系统核对 |
| 精确触控固件 | hash和既有驱动配对；元数据解析对照固定公开Android源码并作合成测试 | 本机旧固件已删，本轮未对该真实196608B blob运行解析/比较旧48B、24B输出 |
| UEFI历史复现 | 有nabu build/bootimg/import入口 | P047使用的UEFI精确git/submodule提交未写入公开锁；新构建与旧固件是否等价仍需实测 |
| macOS内核/GPU编译 | 工作流和现有源码入口保留；历史CI曾通过 | 本轮Linux环境没有SDK26.5/xcrun，未运行新macOS构建，未触发新CI |
| 首次分区 | board保存旧nabu实际几何；通用工具可检查GPT | 没有经本轮验证的nabu首次分区安装器；不同容量不可照抄旧LBA或泛用unixnu布局 |
| ESP落盘日志初始化/读取 | 内核格式和校验源码公开；已有token可保留 | 独立preallocate/read工具未随本树公开；新日志分配流程尚需补齐，首次可显式关闭日志文件写入 |
| 硬件功能 | 沿用 `STATUS.md` | USB仍不工作、触控有旧实机反馈、GPU疑似工作；教程/打包成功不改变这些结论 |

“能够继续构建”和“全新设备从零成功启动”是两项不同验收。上述未验收项要有新的系统素材、macOS构建产物和真机记录才能关闭，不能靠编辑报告字段消除。

## 本轮验证方法

本地结果：**52项测试通过、7项subtest通过、0失败、0跳过**；机器记录见 [reproducibility-checks.json](devices/nabu/reproducibility-checks.json)（JUnit总数59包含7项subtest）。VitePress构建通过。测试不包含Apple系统完整转换或SDK构建。

- 从公开仓库重新克隆，使用固定gitlink初始化DarwinBoot/XnuPrelink/VerifiedRoot，运行真实 `defconfig` 和 `status` 解析27.3/arm64配置。
- 合成输入测试覆盖：SHNGROOT布局与完整读回、签名信任模块保留、GCM认证失败和archive越界拒绝、私有账号权限、overlay owner/mode保留、非法目标路径拒绝、root-profile限制、signed-text转换前后字节、精确GPU固件校验、NT36523头解析和ROOT迁移重新哈希。
- 原有菜单、隐私、PAC/macOS签名、IPSW/UDIF、kernel staging回归测试。
- 按固定公开提交编译mkapfs、apfs-fuse核心库/metadata reader及带BSD-flags接口的Linux APFS guest模块。完整FUSE程序编译需系统提供 `libfuse3-dev`；不把缺少依赖说成源码通过。
- 使用隔离KVM guest写入小型自建APFS镜像、通过APFS读回文件与受保护BSD flag，再clean unmount；结果记录在本地 `out/repro-audit/`。这只验证构建工具链路，不代表Apple系统卷或平板启动。
- 文档链接/命令参数、Python语法、公开源脱敏扫描及文档站构建。

原始检查输出保存在本地 `out/repro-audit/`，避免将私钥、账号、内核镜像及测试产物上传源码。需要继续研究时，先从 [REPRODUCING.md](REPRODUCING.md) 顺序操作，在实际失败阶段保留报告，不把晚期步骤“跳过去”。
