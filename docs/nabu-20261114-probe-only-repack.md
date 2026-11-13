# Probe-only 重打包

`scripts/repack_nabu_probe.py`只读取文件并创建新目录，不签名、不构建、不触发CI、不访问设备。
本次任务只实现脚本和host tests，没有运行真实交付包重打包。

```sh
python3 scripts/repack_nabu_probe.py \
  --base-zip out/nabu-cpu-desktop-repair/nabu-25F71-cpu-desktop-8core-session-evidence-43394823334.zip \
  --base-ci-metadata out/nabu-service-startup/cpu-compositor/run43394823334/run-metadata.json \
  --shim-dir /absolute/path/to/new-boot-tools/boot-shim \
  --shim-ci-metadata /absolute/path/to/new-boot-tools/run-metadata.json \
  --original-trust /absolute/path/to/original-three-module.trustcache \
  --source-root /home/baimao/nabu_macos \
  --output /absolute/path/to/new-probe-only-output
```

预期base SHA-256固定为
`cc69824947a693a7b69bce8eb4b03e06812cbc5959472a10d500b9141c33b773`，
CI run固定43394823334。`--base-sha256`只允许重复这个值，不能用它接受任意其他base。
新shim必须来自另一成功完成CI，metadata的完整`head_sha`必须存在于source-root Git历史。
脚本用只读`git show`逐个比较该commit的源码、probe script、两份header、entitlements和
builder script，要求与本地输入及build.json六个SHA完全一致。dirty但碰巧更新了build.json
的输入也会被拒绝。无需checkout或修改index。

base内`delivery.json`四个payload hash、kernel bytes/hash和TOML hash会重新核对。
base addon本身必须签名页完整、与base trust配对，且只含bootshim，拒绝OD overlay。
传入original trust必须恰好三个模块，并与base trust前三个模块逐字节相同。

新shim要求ordinary ARM64可执行Mach-O、预期签名identifier/target、精确binary hash。
不把build.json的`signature_valid`等布尔声称当作签名证据：直接解析实际签名字节，核对
每个主/替代CodeDirectory、每个code page和签名special slot，拒绝零页覆盖、scatter、
缺失XML/DER entitlements、额外未覆盖尾数据和重叠signature blob。
实际XML entitlement dictionary必须与源plist一致；XML和DER blob均须由每个CodeDirectory
的special slot哈希覆盖。DER blob在此验证完整性，不声称独立解析其语义或验证Apple CMS身份。
本项目ad-hoc工具通过新owner trust模块显式授权。

probe_policy必须精确匹配v6 raw-only、symbols单独`nabu-session-symbols=1`、UPTIME_RAW、
三轮0/150/300秒及相关资源界限；还核对实际二进制内v6、默认跳过sample、mach-uptime、
三轮和opt-in标记。代码行为的来源是成功CI head中逐字节验证的源码，marker本身不替代源码审查。

输出ZIP保留原成员集合。只允许四个成员变化：

- `EFI/UniXNU/Payloads/kernel.macho.gpuaddon`：新签名shim；
- `EFI/UniXNU/Payloads/kernel.macho.trustcache`：原三个模块原字节加上只含新shim CDHash的owner模块；
- `delivery.json`：probe-only说明、原base delivery完整嵌套、kernel/shim分别记录CI和commit；
- `README.txt`：明确默认raw-only、单独symbol opt-in、未修复桌面。

所有其他EFI及非EFI成员重新读取并验证SHA不变，含kernel、ADT、TOML、prelink报告和工具。
ZIP的压缩容器字节会重建，保留要求是每个不可变成员的解压字节，不是旧ZIP的压缩流偏移。
禁止Data/root/log payload、路径逃逸、duplicate names、symlink、加密成员。

输出目录还含manifest.json、base-delivery.json、shim-build.json及两份原始CI metadata。
manifest记录每个成员before/after hash、最终archive SHA、source hashes及签名/trust证据。
旧kernel commit始终位于kernel_provenance，不能用shim commit覆盖。所有验证在临时目录
完成，最终成功才改名到原先不存在的output，拒绝覆盖任何旧输出或原物。

```sh
python3 -m pytest -q tests/test_repack_nabu_probe.py tests/test_nabu_boot_addon.py
```

host tests构造临时fixture Git仓库、真实SHA256签名页/XML/DER special slots、三模块trust和
fixture ZIP，覆盖成功保存每个不可变成员及两份来源、重复输出拒绝、base hash/delivery、
CI失败、commit源码不符、缺builder hash、签名页/entitlement篡改、original trust不符、
Data注入和策略不符拒绝。fixture从不执行或交付，也不替代对真实macOS codesign构建的验包。
