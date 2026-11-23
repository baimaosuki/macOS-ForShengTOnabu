# nabu / macOS 27.3 从源码到 PAC ROOT 与 ESP

本指南针对 **Xiaomi Pad 5 / nabu / SM8150-AC / Adreno 640、macOS 27.3 / 26C5035r**。编译 SDK 为 **26.5**，不把26.5系统素材用于27.3。阅读 [STATUS.md](STATUS.md) 了解实机限制；当前不承诺USB鼠标可用或稳定GPU桌面。

2026-11-23重新从公开 `65f158b` 克隆审计。本次补充的是可检查的步骤、缺失的host工具和失败条件，**没有在干净环境重新完成真实27.3 ROOT制作、macOS编译及平板启动验收**。具体已验证/尚未验证范围见 [REPRODUCIBILITY.md](REPRODUCIBILITY.md)。不要把下文的命令清单当成已经通过的新系统镜像。

## 1. 主机、空间和输入

以下代码块分别注明主机；每次开新终端，重新设置本节变量。命令从仓库根目录运行。使用同一源码提交，记录 `git rev-parse HEAD`。本指南不会自动分区、清Data或刷机。

| 输入 | 准备方式 | 为什么需要 |
| --- | --- | --- |
| Intel macOS、Xcode、macOS/iPhoneOS SDK26.5 | 对照 `.github/workflows/bringup.yml`；历史CI使用 `macos-26-intel` / Xcode26.6 | 原生XNU、owner arm64工具、Darwin GPU用户态及AEA解密 |
| Linux x86_64、KVM | 本指南APFS构建在隔离guest内进行 | 保留受保护BSD inode flags，不把实验APFS写驱动装进host |
| Apple27.3 IPSW | `profiles/os/macos-27.3-26C5035r/profile.toml` 固定Apple URL/版本/BuildIdentity；也可传本地IPSW | OS、SystemOS、App、trust cache、原始kernelcache |
| 设备固件 | 自己的nabu原厂素材；见第4节 | A640 GMU/SQE/ZAP、NT36523及元数据 |
| nabu UEFI | 已有设备上能用的版本，或按第9节重新编译 | 引导链；不是系统ROOT的一部分 |
| 自己生成的密钥和账号 | 第3节；owner root key由第7节生成 | 不需要维护者私钥，也不能用仓库公钥解密维护者旧运行时 |

建议完整制作保留 **200GiB以上工作空间**，具体由解压后的素材与缓存决定；此前保留的IPSW、assets、两份ROOT合计约92.7GiB，构建和多份APFS导出还会增加占用。共享缓存证明/改写需要较多内存；不要在未核对空间时展开所有阶段。

```sh
# 任一主机
git clone --filter=blob:none https://github.com/baimaosuki/macOS-ForShengTOnabu.git
cd macOS-ForShengTOnabu
git submodule update --init --recursive
./unixnu defconfig nabu_macos27_defconfig -D devices/nabu --os macos-27
export REPO="$PWD"
export WORK="$REPO/out/repro27"
mkdir -p "$WORK"
export TARGET="$REPO/out/nabu/macos-27.3-26C5035r/nabu_macos27"
```

不要跟踪上游 `main` 替代固定提交，不运行 `fetch --update`。设备driver副本、gitlink、feeds和GPU锁文件分别有自己的来源。

macOS安装依赖（先自行准备所需Xcode；不要假设其他版本SDK等价）：

```sh
xcrun --sdk macosx --show-sdk-version
xcrun --sdk iphoneos --show-sdk-version
# 两项应为26.5；uname -m应为x86_64
brew install cmake ninja unifdef llvm lld dtc openssl@3 bison glslang shaderc rustup mtools dosfstools
python3 -m pip install numpy==2.2.6 capstone==5.0.7 unicorn==2.1.4 cryptography pytest
export PATH="$(brew --prefix llvm)/bin:$(brew --prefix bison)/bin:$HOME/.cargo/bin:$PATH"
python3 devices/nabu/scripts/install-runtime-tools.py
export PATH="$REPO/.ci/bin:$PATH"
```

`install-runtime-tools.py` 校验固定 `ipsw` 工具下载SHA256；在本地终端也可使用，不再强制依赖 `GITHUB_PATH`。

## 2. 提取原始27.3输入（macOS）

首先不要设置 `UNIXNU_ROOT_PROFILE`；这里校验的是原始Apple磁盘映像的散列。

```sh
unset UNIXNU_ROOT_PROFILE
python3 devices/nabu/scripts/prepare-nabu-inputs27.py --output "$WORK/original"
# 已有完整IPSW时，用 --ipsw /absolute/path/UniversalMac_27.3_26C5035r_Restore.ipsw
```

输出 `original/inputs.json`、三份 `NAME.original.raw`；原始解密DMG、trust cache、kernelcache保存在 `out/ipsw/26C5035r/`。JSON记录每个实际路径、原始散列、APFS容器起始偏移和卷UUID。工具通过现有IPSW代码验证27.3/26C5035r、Mac17,11/k114cap；AEA密钥由 `ipsw fw aea --key` 获取并交给macOS `aea`，不写入本教程或打印密钥。

这是**未经PAC改写的原始输入**，不能与普通指针内核配对刷入。`./unixnu build userdata` 同样只封装原始磁盘，不是PAC转换命令。

## 3. Data、账号和boot-shim运行时（Intel macOS）

创建自己的RSA接收方。私钥留在本机，CI只需要公钥。不要使用维护者的 `runtime-recipient.pem` 来生成只有维护者可解密的包。

```sh
mkdir -p private
chmod 700 private
umask 077
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out private/runtime-recipient.pem
openssl pkey -in private/runtime-recipient.pem -pubout -out private/runtime-recipient-public.pem
python3 devices/nabu/scripts/build-fresh-runtime.py --recipient private/runtime-recipient-public.pem
python3 devices/nabu/scripts/unpack-fresh-runtime27.py \
  --encrypted out/fresh-runtime/runtime.enc \
  --envelope out/fresh-runtime/runtime-envelope.json \
  --private-key private/runtime-recipient.pem --output "$WORK/runtime"
```

构建需要本机 `sudo` 权限创建全新的Data seed；当前脚本使用 `sudo -n`，需先在自己的macOS终端完成 `sudo -v`。它不读取平板Data。`runtime/account.json` 中是新随机密码；只在本机查看，不上传。输出还包括 `data.dbr`、`gpuaddon.bin`（此阶段包含启动工具，不代表GPU已接入）、`owner-trust.bundle` 和核验报告。实际文件名以 `runtime-verification.json` 为准。

Data卷UUID必须和编译配置匹配；不能把别人的seed换进已有内核。以后更新保留同一seed；只有明确重新安装时才生成新Data并选择菜单确认初始化。

若用CI，在自己的fork提交**公钥**，手动工作流选择 `runtime` 并填写 `runtime_recipient` 为公钥的仓库相对路径。下载两个加密产物后执行相同解包命令。旧私钥丢失时重新生成运行时，不能恢复旧随机密码。

## 4. 板级固件与触控元数据

GPU目录必须包含以下与当前驱动配对的文件；ADT生成器和内核分别校验一次，不接受A740固件。

| 文件 | 字节数 | SHA256 |
| --- | ---: | --- |
| `a640_gmu.bin` | 37680 | `3ff0c02708bbe78641db887fa62f3a7f9337934d0c2ce0b961ef7c43172591d2` |
| `a630_sqe.fw` | 32136 | `36298bd53ea3666413d288a3a3f5266f67fcb6db038a0a481af5869d1c17859c` |
| `a640_zap.mdt` | 6860 | `5c5a956febabc8c8f3fc7b2cc48ae22bcab9ae25a9853c7cd0baadc9ec406882` |
| `a640_zap.b02` | 1968 | `e24a4f483b71ea19715910f01e1abf94280756df731cad05df48fd7714e34198` |

触控目录需要 `novatek_nt36523_k82_fw01.bin`（196608B，SHA256 `d66d15f3dff35292e42e0d301bf53a7fcea6b6fc1b93bca90bb66d765bcfba9b`）、`partitions.bin`（48B，4个小端 `<target,offset,length>`）、`crc-bank.bin`（24B，6个小端u32）。后两个从精确固件头解析，不是填零的占位文件：

```sh
python3 devices/nabu/scripts/prepare-nabu-touch-firmware27.py \
  --firmware /absolute/path/to/original/novatek_nt36523_k82_fw01.bin \
  --output "$WORK/firmware/nabu-touch"
```

解析规则对照固定提交的Xiaomi/Novatek Android驱动：ILM/DLM、info/overlay头、`size+1`真实写入长度及原始CRC bank字段。仅接受上述散列的固件，解析结果必须符合4分区；本轮只运行合成格式测试，尚未用已删除的实机固件重做对照。失败时停止，不用其他固件或手造元数据绕过。

```sh
export NABU_GPU_FIRMWARE_DIR=/absolute/path/to/firmware/adreno640
export NABU_TOUCH_FIRMWARE_DIR=/absolute/path/to/firmware/nabu-touch
```

从自己的原厂ROM提取或从仍保存的工作ESP中恢复；不同ROM的目录可能是 `/vendor/firmware_mnt/image`、`/vendor/firmware`，不能仅按文件名认定版本正确。不要将固件、账号或设备整份日志提交Git。

## 5. APFS host工具（Linux x86_64）

把第2节原始文件及第3节运行时安全复制到Linux，保持 `inputs.json` 内路径有效。路径不同则重新生成本地清单或明确修改路径，不能修改原始哈希。下列是本次核对的公开工具提交；**不是旧P047 host工具版本的追溯证明**。

```sh
# Ubuntu；只安装host依赖，不向host加载APFS写模块
sudo apt-get install build-essential cmake libfuse3-dev zlib1g-dev libbz2-dev \
  libssl-dev qemu-system-x86 busybox-static cpio zstd kmod gcc-13 \
  python3-pytest python3-numpy python3-capstone python3-cryptography
git clone https://github.com/linux-apfs/apfsprogs.git "$WORK/apfsprogs"
git -C "$WORK/apfsprogs" switch --detach b9c4cb404f88ffab6c4f260bbab29a8fb4784907
make -C "$WORK/apfsprogs/mkapfs"
git clone https://github.com/sgan81/apfs-fuse.git "$WORK/apfs-fuse"
git -C "$WORK/apfs-fuse" switch --detach 66b86bd525e8cb90f9012543be89b1f092b75cf3
git -C "$WORK/apfs-fuse" submodule update --init --recursive
cmake -S "$WORK/apfs-fuse" -B "$WORK/apfs-fuse/build"
cmake --build "$WORK/apfs-fuse/build" -j4
make -C "$WORK/apfs-fuse/3rdparty/lzfse"
export PATH="$WORK/apfs-fuse/3rdparty/lzfse/build/bin:$PATH"
c++ -std=c++11 -I "$WORK/apfs-fuse" devices/nabu/scripts/nabu-apfs-bsd-flags.cpp \
  -L "$WORK/apfs-fuse/build" -lapfs -llzfse -lcrypto -lz -lbz2 -o "$WORK/apfs-bsd-flags"
git clone https://github.com/linux-apfs/linux-apfs-rw.git "$WORK/linux-apfs-rw"
git -C "$WORK/linux-apfs-rw" switch --detach 6cf6de8ad6fa4cb634a700b5d0436d73ee01942c
git -C "$WORK/linux-apfs-rw" apply --check "$REPO/devices/nabu/scripts/zeta-guest-bsd-flags.patch"
git -C "$WORK/linux-apfs-rw" apply "$REPO/devices/nabu/scripts/zeta-guest-bsd-flags.patch"
```

guest内核、headers、modules必须完全配对。旧writer默认 `6.8.0-106-generic`，下面显式传参，不要求所有人恰有此版本：

```sh
export GUEST_RELEASE=6.8.0-106-generic
export GUEST_KERNEL=/absolute/path/to/readable/vmlinuz-6.8.0-106-generic
# 本机须另行准备此版本的headers及/lib/modules/$GUEST_RELEASE/中的9p/CRC依赖。
make -C "$WORK/linux-apfs-rw" KERNEL_DIR="/lib/modules/$GUEST_RELEASE/build" \
  CROSS_COMPILE= CC=x86_64-linux-gnu-gcc-13 LD=ld APFS_CONFIG=-DNABU_GUEST_BSD_FLAGS -j4
test -r "$GUEST_KERNEL"
test -r /dev/kvm
test -w /dev/kvm
```

`write-nabu-zeta-volume.py` 只在KVM guest加载该模块，host只持有普通镜像文件。guest核不匹配、内核不可读、缺少KVM权限或模块依赖时必须先解决，不能改成host直接写平板分区。

## 6. 导出、PAC改写和生成新APFS卷（Linux）

### 6.1 三个原始卷只读挂载并导出

对 `OS`、`SystemOS`、`App` 各运行一次。`RAW`、`OFFSET`、`UUID` 均取自 `original/inputs.json` 相应window。不要猜GPT起始偏移；以下是单卷模板：

```sh
export NAME=OS
export RAW="$(python3 -c 'import json,os;print(json.load(open(os.environ["WORK"]+"/original/inputs.json"))["windows"][os.environ["NAME"]]["raw"])')"
export OFFSET="$(python3 -c 'import json,os;print(json.load(open(os.environ["WORK"]+"/original/inputs.json"))["windows"][os.environ["NAME"]]["apfs_offset"])')"
mkdir -p "$WORK/mounts/$NAME" "$WORK/plans" "$WORK/reports"
sudo "$WORK/apfs-fuse/build/apfs-fuse" -s "$OFFSET" -o ro,allow_other "$RAW" "$WORK/mounts/$NAME"
python3 devices/nabu/scripts/prepare-nabu-volume-plan27.py export \
  --root "$WORK/mounts/$NAME" --output "$WORK/export-$NAME" --plan "$WORK/plans/$NAME-original.json"
```

不要使用FUSE的 `uid=` / `gid=` 伪装选项；必须读出原始owner。导出工具保留文件内容、owner/mode及符号链接，原生BSD flags在6.3读取。**ACL和其他xattr尚未由新导出工具复制**；它们的范围和实系统影响尚未完成新验收，见审计表，不能声称元数据完全等价。

### 6.2 普通Mach-O和完整共享缓存分别改写

三份Apple trust cache路径取 `inputs.json` 的 `trust_caches`，以下用shell数组 `TRUST_ARGS` 表示：

```sh
mapfile -t TRUST_FILES < <(python3 -c 'import json,os;print("\n".join(json.load(open(os.environ["WORK"]+"/original/inputs.json"))["trust_caches"]))')
TRUST_ARGS=()
for file in "${TRUST_FILES[@]}"; do TRUST_ARGS+=(--apple-trust-cache "$file"); done
for NAME in OS SystemOS App; do
  python3 devices/nabu/scripts/rewrite-nabu-zeta-tree.py \
    --root "$WORK/mounts/$NAME" --output "$WORK/rewritten-$NAME" \
    --report "$WORK/reports/$NAME-tree.json" --file-list "$WORK/plans/$NAME-rewritten.json" \
    --jobs 2 "${TRUST_ARGS[@]}"
done
```

每份tree报告必须 `complete=true`、`failures=0`，不能跳过失败文件。然后在原始SystemOS挂载卷中查找**主缓存**：

```sh
find "$WORK/mounts/SystemOS" -type f -name dyld_shared_cache_arm64e
# 必须确认恰好一个结果；将完整路径设为CACHE。
export CACHE=/absolute/path/from/the/previous/find/dyld_shared_cache_arm64e
python3 devices/nabu/scripts/make-nabu-zeta-cache-manifest.py \
  --cache "$CACHE" --output "$WORK/cache-inputs.json"
python3 devices/nabu/scripts/zeta_pac/prove-zeta-dyld-cache.py \
  --cache "$CACHE" --work "$WORK/cache-proof" --jobs 4
python3 devices/nabu/scripts/rewrite-nabu-zeta-cache.py --cache "$CACHE" \
  --proof "$WORK/cache-proof" --input-manifest "$WORK/cache-inputs.json" \
  --output "$WORK/cache-rewritten" --report "$WORK/reports/cache-rewrite.json" "${TRUST_ARGS[@]}"
python3 devices/nabu/scripts/verify-nabu-zeta-cache.py \
  --report "$WORK/reports/cache-rewrite.json" --output "$WORK/reports/cache-verification.json"
```

不能对完整系统证明使用 `--only` 的局部结果冒充全缓存证明。工具保留签名特殊blob、重建4KiB ad-hoc页签名、重新封corecrypto完整性字段，并记录新cdhash。剩余未证明指令使用同语义内核fallback，PAC并没有恢复为硬件安全功能。

### 6.3 合并写入计划、恢复BSD flags、在guest写新卷

OS/App合并普通文件改写；SystemOS还合并共享缓存。`--cache-prefix` 是缓存目录相对于该**卷根**的路径，常见为 `/System/Library/dyld`，应由实际挂载路径确认，不能填macOS union挂载前缀。

```sh
for NAME in OS App; do
  python3 devices/nabu/scripts/prepare-nabu-volume-plan27.py merge \
    --plan "$WORK/plans/$NAME-original.json" --overlay "$WORK/plans/$NAME-rewritten.json" \
    --output "$WORK/plans/$NAME-merged.json"
done
python3 devices/nabu/scripts/prepare-nabu-volume-plan27.py merge \
  --plan "$WORK/plans/SystemOS-original.json" --overlay "$WORK/plans/SystemOS-rewritten.json" \
  --cache-report "$WORK/reports/cache-rewrite.json" --cache-prefix /System/Library/dyld \
  --output "$WORK/plans/SystemOS-merged.json"
```

对每个卷，从原始镜像提取本机inode flags；`SF_COMPRESSED` 会去掉，因为写入的是解压后的完整文件字节；`SF_RESTRICTED` 等保留：

```sh
mkdir -p "$WORK/volumes"
for NAME in OS SystemOS App; do
  RAW="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["windows"][sys.argv[2]]["raw"])' "$WORK/original/inputs.json" "$NAME")"
  OFFSET="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["windows"][sys.argv[2]]["apfs_offset"])' "$WORK/original/inputs.json" "$NAME")"
  UUID="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["windows"][sys.argv[2]]["volume_uuid"])' "$WORK/original/inputs.json" "$NAME")"
  python3 devices/nabu/scripts/make-nabu-zeta-bsd-plan.py --image "$RAW" --offset "$OFFSET" \
    --plan "$WORK/plans/$NAME-merged.json" --reader "$WORK/apfs-bsd-flags" \
    --output "$WORK/plans/$NAME-final.json"
  # 下面是新host文件的容量预算，不是平板分区命令；可按实际解压大小调整。
  case "$NAME" in OS) SIZE=20G;; SystemOS) SIZE=12G;; App) SIZE=2G;; esac
  test ! -e "$WORK/volumes/$NAME.raw"
  truncate -s "$SIZE" "$WORK/volumes/$NAME.raw"
  "$WORK/apfsprogs/mkapfs/mkapfs" -L "$NAME" -u "$UUID" "$WORK/volumes/$NAME.raw"
  python3 devices/nabu/scripts/write-nabu-zeta-volume.py --container "$WORK/volumes/$NAME.raw" \
    --export "$WORK" --plan "$WORK/plans/$NAME-final.json" --kernel "$GUEST_KERNEL" \
    --module "$WORK/linux-apfs-rw/apfs.ko" --kernel-version "$GUEST_RELEASE" \
    --report "$WORK/reports/$NAME-write.json" --work "$WORK/guest-$NAME"
done
```

每份write报告必须 `complete=true`、`clean_unmount=true`，文件哈希、BSD flags核对无失败。工具现在记录最终容器SHA256并与读回文件表绑定。容量不足或guest失败时，不从半成品继续生成 `complete=true`。

## 7. 新owner策略、ROOT、trust bundle与prelink报告（Linux或macOS）

```sh
python3 devices/nabu/scripts/assemble-nabu-root27.py --volumes "$WORK/volumes" \
  --reports "$WORK/reports" --cache-report "$WORK/reports/cache-rewrite.json" \
  --cache-verification "$WORK/reports/cache-verification.json" \
  --base-trust-bundle "$WORK/runtime/owner-trust.bundle" --output "$WORK/root"
# macOS Homebrew OpenSSL另传 --openssl-prefix "$(brew --prefix openssl@3)"
```

这一步创建自己的RSA3072 owner key，逐卷运行真实C签名和每块校验，生成三份策略及hash table。输出：

| 文件 | 用途 |
| --- | --- |
| `root/macosroot-27.3-26C5035r-zeta-pac.img` | SHNGROOT v2：OS/SystemOS/App原始APFS容器＋各自 `.vroot` 块表 |
| `root/policies/`、`root/root-policies.bin` | 配套内核anchor、验证记录和DarwinBoot handoff |
| `root/owner-zeta.trustcache` | 保留原Apple/owner模块，追加普通程序和缓存的新cdhash，保留其属性 |
| `root/root.local.toml` | 转换后卷散列与同一UUID；按转换后的缓存字节重新对齐signed-text preimage |
| `root/runtime-manifest.json` | 供 `NABU_ZETA_RUNTIME_REPORT` 使用的实际输入/验证汇总 |
| `root/owner.pem` | 新私钥，禁止上传；丢失则需重新生成配套策略与内核 |

不能手写一个 `complete=true` JSON，也不能沿用原始DMG散列、旧维护者anchor或旧hash table。镜像、内核、策略和trust必须成套。ROOT封装工具会读回每个window/object、检查原始配置分区容量；实际设备还需独立核对第11节。

## 8. 构建内核、GPU用户态与payload（Intel macOS）

把Linux生成的 `root/` 和Data/boot-shim运行时安全转回macOS；转换过程原始报告仍保留在Linux。macOS上的ROOT路径不同，用工具重新哈希并只重绑定prelink需要的镜像路径：

```sh
python3 devices/nabu/scripts/relocate-nabu-root-report27.py \
  --report "$WORK/root/runtime-manifest.json" \
  --root-image "$WORK/root/macosroot-27.3-26C5035r-zeta-pac.img" \
  --output "$WORK/root/runtime-manifest.local.json"
```

```sh
export UNIXNU_ROOT_PROFILE="$WORK/root/root.local.toml"
export NABU_ROOT_POLICY_DIR="$WORK/root/policies"
export NABU_ZETA_RUNTIME_REPORT="$WORK/root/runtime-manifest.local.json"
export UNIXNU_KERNELCACHE="$(python3 -c 'import json,os;print(json.load(open(os.environ["WORK"]+"/original/inputs.json"))["kernelcache"])')"
./unixnu defconfig nabu_macos27_defconfig -D devices/nabu --os macos-27
python3 devices/nabu/scripts/compile-bootstrap.py --full
```

`--full` 要求真实native内核链接通过，不是允许最终链接失败的bootstrap-only模式。新的root-profile只允许覆盖三份卷散列及经过核对的signed-text fixups，UUID保持该系统配置值。公开源码不需修改来写入自己的散列。

GPU用户态独立构建（第一次需要；只改内核且已有匹配用户态时可复用）：

```sh
python3 devices/nabu/scripts/build-nabu-m2v27.py --phase all --output "$WORK/gpu"
python3 devices/nabu/scripts/package-nabu-m2v27.py --artifacts "$WORK/gpu" \
  --tools-tree "$WORK/gpu/tree" --expected-commit "$(git rev-parse HEAD)" \
  --output "$WORK/gpu-signed"
```

要求普通 `arm64` / `armv8.2-a+crypto`、Rust1.98.0、锁定Mesa/M2V和补丁、4KiB签名校验及PAuth扫描通过。源码中的 `kernel_wire_compatible=false` 是用户态构建阶段未做内核集成的边界；不能手改为true表示硬件已通过。

然后构建prelink与CPU基底payload。平台seed是自己的稳定非零u64，首次生成后本地保存，后续复用：

```sh
python3 -c 'import secrets;f=open("private/platform-seed.txt","x");f.write(f"{secrets.randbits(64) or 1:016x}\n");f.close()'
export UNIXNU_UNIQUE_CHIP_ID="$(cat private/platform-seed.txt)"
./unixnu build prelink payload -D devices/nabu --os macos-27 \
  --uefi /absolute/path/to/your/nabu-uefi \
  --data-ramdisk "$WORK/runtime/data.dbr" \
  --gpu-addon "$WORK/runtime/gpuaddon.bin" \
  --owner-trust-cache "$WORK/root/owner-zeta.trustcache"
```

首次seed命令只运行一次，不覆盖已有安装的身份。`gpu_addon=false` 只禁用自动构建上游GPU插件；现在显式传入匹配trust的boot-shim addon允许走公开CLI，不再依赖旧 `out/Pxxx/assemble-native.py` 绕开runtime准备。

使用native CI而非本地编译时，只上传新生成的**公开**策略header、anchor、verification及root profile，不上传ROOT/块表/私钥：

```sh
mkdir -p devices/nabu/policies.local/26C5035r
for NAME in OS SystemOS App; do
  mkdir -p "devices/nabu/policies.local/26C5035r/$NAME"
  for FILE in manifest.header.bin trusted-key.bin verification.json; do
    cp "$WORK/root/policies/$NAME/$FILE" "devices/nabu/policies.local/26C5035r/$NAME/$FILE"
  done
done
cp "$WORK/root/root.local.toml" devices/nabu/policies.local/root.local.toml
# 只在你自己的fork准备构建提交；明确add文件，因为.bin/local.toml默认忽略。
git add -f devices/nabu/policies.local/26C5035r/OS/manifest.header.bin \
  devices/nabu/policies.local/26C5035r/OS/trusted-key.bin \
  devices/nabu/policies.local/26C5035r/SystemOS/manifest.header.bin \
  devices/nabu/policies.local/26C5035r/SystemOS/trusted-key.bin \
  devices/nabu/policies.local/26C5035r/App/manifest.header.bin \
  devices/nabu/policies.local/26C5035r/App/trusted-key.bin devices/nabu/policies.local/root.local.toml
git add devices/nabu/policies.local/26C5035r/*/verification.json
```

审核后提交/推送到自己的fork，工作流选择native，`root_policy_dir=devices/nabu/policies.local/26C5035r`、`root_profile=devices/nabu/policies.local/root.local.toml`。CI只产native/boot-tools，仍需下载后放到当前 `$TARGET/tree/build/xnu/...` 的对应位置再本地prelink；不能把CI native Mach-O当最终可刷内核。CI cache应随公开anchor/镜像输入提交更新，切勿复用别人的RootAnchor目标文件。

## 9. UEFI与菜单

有已经工作的nabu UEFI时先保留，普通ESP更新不需要重刷boot。全新设备则需要包含UniXNU/DarwinBoot的nabu固件，不能用SHENG/SM8550固件替代。

公开构建入口是：

```sh
./unixnu build package firmware -D devices/nabu --os macos-27 --uefi /absolute/path/to/nabu-uefi
./unixnu bootimg -D devices/nabu --os macos-27 --uefi /absolute/path/to/nabu-uefi \
  --ramdisk none -o "$WORK/nabu-uefi-boot.img"
```

UEFI源码采用[Mu-Silicium](https://github.com/Project-Silicium/Mu-Silicium)的nabu平台；`import-nabu-uefi.py`接入本仓库菜单与DarwinBoot。**历史P047 UEFI的精确提交没有保存在公开锁文件中**；上述构建入口本轮没有编译/上机复验，不能承诺与历史固件相同。重建者需记录实际UEFI/submodule提交并验证其固件窗口、DTB和boot传输格式。见审计中仍未验收项。

`build package` 的菜单输出位于 `$TARGET/package/Build/Out/`，不是payload FAT里自动包含的文件。选择该次构建的AARCH64 UniXNU EFI，核对PE Machine=0xaa64后放入最终ESP两个位置。loader为board配置的 `firmware`，因此要用包含匹配DarwinBoot的UEFI；不能仅复制旧通用菜单就假设引导接口一致。

## 10. GPU集成、最终ESP ZIP（Linux或macOS）

payload阶段的文件树在 `$TARGET/payload/stage/`。不要拿较早的 `payload.img` 直接交付GPU启动；GPU合并后必须重建最终FAT/ZIP。

```sh
export STAGE="$TARGET/payload/stage"
mkdir -p "$STAGE/EFI/BOOT"
# MENU_EFI设为第9节本次build package实际输出的UniXNU EFI文件。
export MENU_EFI=/absolute/path/to/the/built/AARCH64/UniXNU.efi
cp "$MENU_EFI" "$STAGE/EFI/BOOT/BOOTAA64.EFI"
cp "$MENU_EFI" "$STAGE/EFI/UniXNU/UniXNU.efi"
python3 devices/nabu/scripts/integrate-gpu-esp27.py --esp "$STAGE" \
  --signed-userspace "$WORK/gpu-signed" --output "$WORK/gpu-integration.json" \
  --without-persistent-log
python3 devices/nabu/scripts/pack-nabu-esp27.py --esp "$STAGE" \
  --prelink-report "$TARGET/kernel/kernel.json" --payload-report "$TARGET/payload/payload.json" \
  --root-report "$NABU_ZETA_RUNTIME_REPORT" \
  --output "$WORK/nabu-27.3-26C5035r-esp.zip"
```

首次没有预分配ESP日志文件时，显式 `--without-persistent-log` 令 `nabu-log=0`，保留 `log=1`；不会假造token，也不表示日志落盘已配置。已有设备日志分配时，设置 `NABU_LOCAL_BOOT_CONFIG` 指向自己原ESP菜单，**去掉**此选项以保留原token。完整日志分配/读取工具不在这份源码树中，不能给任意随机token就宣称能保存日志。

集成器开启GPU所需参数、发布marker、Shader Cache app、CPU回退入口、`scale=3`及Data初始化确认项。27.3 BGRA/signed-text规则由第7节生成的root-profile和payload ADT承载；不写运行中的已签名代码页。打包器检查ROOT/prelink配对、菜单、AARCH64菜单PE、FAT写回/读回、每份EFI哈希和ZIP CRC；它不会把这些结果称为真机GPU或USB验收。

## 11. 刷入与第一次启动（设备操作，逐步手动执行）

先备份设备boot/GPT和需要的数据。此仓库board中的LBA是维护者历史布局，**不是所有nabu都应照抄的分区方案**。需要 `macosroot` 和ESP；持久Data由 `NativeUfs.cpp` 在 **macosroot尾部** 分配，默认再保留1GiB日志空间，并与ROOT隔开4MiB，不是凭空假定有独立 `macosdata` 分区。实际容量至少容纳ROOT＋seed/overlay＋reserve/guard。首次分区前按真实设备容量、扇区和现有GPT规划，通用 `scripts/dual_sys/partition.py` 的unixnu布局不能直接当作本分支macosroot安装器。

已存在正确分区时，在fastboot先读信息：

```sh
fastboot getvar product
fastboot getvar current-slot
fastboot getvar partition-size:macosroot
# product必须是nabu；核对ROOT字节数不超过上述实际容量。
sha256sum "$WORK/root/macosroot-27.3-26C5035r-zeta-pac.img"
fastboot flash macosroot "$WORK/root/macosroot-27.3-26C5035r-zeta-pac.img"
```

这是有正确 `macosroot` 分区时的ROOT刷入命令，**不要改成userdata，也不要在找不到分区时执行format/erase**。持久Data在该分区尾部，重新flash是否擦除尾部取决于设备bootloader，首次重装应视为会丢失原Data，先备份；已有ROOT且只更新ESP时不重刷ROOT。

在恢复环境将实际ESP挂到 `/esp`，备份旧EFI，将新ZIP的EFI文件合并覆盖：

```sh
unzip -o /path/to/nabu-27.3-26C5035r-esp.zip 'EFI/*' -d /esp
unzip -p /path/to/nabu-27.3-26C5035r-esp.zip SHA256SUMS > /tmp/nabu-checksums
(cd /esp && sha256sum -c /tmp/nabu-checksums)
sync
```

ZIP是文件更新包，不能 `fastboot flash esp xxx.zip`。如重新生成了菜单，应使用最终新清单；不要笼统忽略哈希失败。

全新设备需要第9节验证过的UEFI boot镜像。先用设备支持的临时启动方式验证，例如 `fastboot boot /path/to/nabu-uefi-boot.img`；boot槽位持久写入需按实际恢复方案决定，本指南不替用户选择或覆盖双槽。已有工作UEFI直接进入其菜单。

首次确实要初始化macOS Data时，选择 `a640-metal-init` 并确认；它会清除/重建macOS Data。以后选 `a640-metal`，普通启动不得带 `data-reset=1`。GPU异常时保留 `xnu` CPU回退入口。

## 12. 必须保存的验收记录

保存源码与子模块提交、原始IPSW/DMG/kernelcache散列、三个tree/write报告、缓存独立验证、新owner anchor标识、ROOT/prelink/ESP哈希及真实启动日志。私钥和账号放私有备份。

Host可先运行：

```sh
python3 -m pytest -q devices/nabu/tests/test_reproduction27.py \
  devices/nabu/tests/test_boot_menu27.py devices/nabu/tests/test_public_privacy27.py \
  devices/nabu/tests/test_zeta_macos_sign27.py
python3 devices/nabu/scripts/check-public-privacy27.py
```

只有实机记录才能确认APFS/launchd/WindowServer启动、触摸输入、GPU fence连续完成和USB HID输入。没有新实机日志时，继续沿用 [STATUS.md](STATUS.md)，不能因为生成ZIP就改成“GPU桌面已稳定/USB已修好”。
