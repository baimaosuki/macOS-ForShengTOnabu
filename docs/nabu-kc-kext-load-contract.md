# macOS 26 XNU 的 KC kext 加载契约与 Nabu 落地策略

本文回答四个问题：macOS 26（XNU 12377.x）从 kernel collection（KC）安装并启动 kext 的真实
boot 路径是什么、当前 Nabu 链路缺哪些输入、在 4 个目标 bundle 上应该选哪条路、以及 arm64e
专属要求在 Kryo 485 上如何不成立。

所有 `file:line` 引用均相对 `<repo>/sources/macos26/xnu`；命名前缀 `[repo]` 表示相对仓库根
`/home/baimao/.gemini/antigravity/scratch/macOS-ForNabu/macOS-ForSheng`。

分析对象（实测，只读）：

| 对象 | 路径 | 实测 |
|---|---|---|
| Apple KC（arm64e 原件） | `private/apple/macos26/kernelcache.release.mac13g.decoded` | MH_FILESET，`cpusubtype=0x80000002`，346 个 `LC_FILESET_ENTRY`，基址 `0xfffffe0007004000`，顶端 `0xfffffe000df30000` |
| Apple KC（本仓 arm64 版） | `out/macos26-assets/kc.mac13g.arm64.macho` | 同上，`cpusubtype=0x0`，链已去认证 |
| 自构建内核 | `/tmp/k8/out/kernel.ci.nabu`（= `out/macos26-xnu-nabu/kernel.development.nabu`） | MH_EXECUTE，`0x100000c/0x0`，基址 `0xfffffe0007004000`，18 个段，`LC_SYMTAB` nsyms=348874，`__TEXT,__thread_starts` 长度 **0** |

---

## 一、真实 boot 路径

### 1.1 结论先行

> **macOS 26 的 arm64 XNU 没有任何 `kc_*` boot_args 字段，也没有任何"内核按名字查符号表"
> 的 fixup 解析。** kext 在 KC 内部彼此之间、以及与内核之间的每一个引用，都在**构建 KC 时**
> 由 kext 链接器解析成了**相对 KC 基址的位移**；boot 时内核只做"rebase"，不做"bind"。

因此所谓 "boot contract" 不是一组 boot_args，而是三件事：

1. 内核镜像自身的 **Mach-O 段布局**（`__PRELINK_TEXT` / `__PLK_TEXT_EXEC` /
   `__PLK_DATA_CONST` / `__PRELINK_DATA` / `__PRELINK_INFO`）必须描述出 kext 代码与数据；
2. **`__PRELINK_INFO,__info`** 里必须有一份序列化 XML plist，逐个描述 kext；
3. 上述内存在物理上必须落在 booter 交给内核的 kernelcache 连续区间内，且页已建立映射。

### 1.2 `boot_args`：没有 KC 字段

`pexpert/pexpert/arm64/boot.h:50-64` 给出完整结构：

```c
typedef struct boot_args {
    uint16_t Revision, Version;
    uint64_t virtBase, physBase, memSize, topOfKernelData;
    Boot_Video Video;
    uint32_t machineType;
    void    *deviceTreeP;
    uint32_t deviceTreeLength;
    char     CommandLine[BOOT_LINE_LENGTH];
    uint64_t bootFlags;
    uint64_t memSizeActual;
} boot_args;
```

| 字段 | KC 路径是否使用 | 依据 |
|---|---|---|
| `virtBase` / `physBase` | 是（`gVirtBase`/`gPhysBase`，决定了 KC 的 phys↔virt 换算） | `osfmk/arm64/arm_vm_init.c:2202-2203` |
| `memSize` / `memSizeActual` | 是（`real_avail_end`、页表规模） | `osfmk/arm64/arm_vm_init.c:1601-1640` |
| `topOfKernelData` | 是（内核数据区物理顶，决定 `static_memory_end` 与 physmap 起点） | `osfmk/arm64/arm_vm_init.c:1636`、`1861`、`1873` |
| `deviceTreeP` / `deviceTreeLength` | 是（`/chosen`、`/chosen/memory-map`） | `osfmk/arm64/arm_vm_init.c:1318` |
| `machineType`、`CommandLine`、`bootFlags` | 是（平台与 boot-arg） | `pexpert/gen/*` |
| `kc_hdr` / `kc_vbase` / 任意 `kc_*` | **不存在** | 全树 grep 无匹配；`boot.h` 无该字段 |

也就是说：**"把 KC 头指针塞进 boot_args" 这条路在本版本里根本不存在**，不要去实现它。

### 1.3 KC 头与 base pointer 的发现：完全由镜像自描述

`osfmk/arm/arm_init.c:229-266`（`arm_slide_rebase_and_sign_image`）：

```c
k_mh = &_mh_execute_header;
if (kernel_mach_header_is_in_fileset(k_mh)) {           // arm_init.c:237
    seg = (kernel_segment_command_t *)((uintptr_t)k_mh + sizeof(*k_mh));
    slide = (uintptr_t)k_mh - seg->vmaddr;             // arm_init.c:245
    kc_mh = (kernel_mach_header_t*)(VM_KERNEL_LINK_ADDRESS + slide);  // :252
    const void *collection_base_pointers[KCNumKinds] = {[0] = kc_mh, };
    kernel_collection_slide((struct mach_header_64 *)kc_mh, collection_base_pointers); // :264
    PE_set_kc_header(KCKindPrimary, kc_mh, slide);     // :266
    kernel_collection_adjust_mh_addrs(...);            // :273
}
```

要点：

- `kernel_mach_header_is_in_fileset(_mh)` 就是 `_mh->flags & MH_DYLIB_IN_CACHE`
  （`libkern/libkern/kernel_mach_header.h:91`）。我们内核 `flags=0x218001`，**该位为 0**，
  所以走的是"非 fileset"分支：KC 头 == 内核头，`collection_base_pointers[0] == kc_mh`。
- `VM_KERNEL_LINK_ADDRESS` 对 arm64 由 `makedefs/MakeInc.def:471` 的 `ARM64_LINK_ADDR`
  注入；本仓构建值为 `0xfffffe0007004000`（与 KC 基址一致）。
- `PE_set_kc_header`（`pexpert/gen/kcformat.c:78-105`）自己遍历 `LC_SEGMENT_64` 取最小
  `vmaddr` 作为 `collection_base_pointers[i]`，说明 **KC 的"基址"就是它最低段的 vmaddr**。

KC 格式判定在 `pexpert/gen/kcformat.c:189-211`：

```
mh && mh->filetype == MH_FILESET  -> KCFormatFileset
arm64 && static_kernelcache       -> KCFormatStatic
否则                              -> KCFormatKCGEN
```

`static_kernelcache` 由 `osfmk/arm/arm_init.c:288` 决定，判据是
`__TEXT,__thread_starts` 段非空。**本仓内核该 section 长度实测为 0**，因此
`PE_get_primary_kc_format()` 对我们返回 **`KCFormatKCGEN`**。这一点后面（1.6、三）非常关键。

### 1.4 fixup 解析：只有 rebase，没有 bind

真正的应用点在 `osfmk/mach/dyld_kernel_fixups.h`：

- `fixup_value()`（`:102-140`）只接受两种 `pointer_format`

  ```c
  case DYLD_CHAINED_PTR_64_KERNEL_CACHE:
  case DYLD_CHAINED_PTR_X86_64_KERNEL_CACHE: {
      const void* baseAddress = basePointers[fixupLoc->fixup64.cacheLevel];
      uintptr_t slidValue = (uintptr_t)baseAddress + fixupLoc->fixup64.target;
      ...
  ```

  即 **`目标地址 = collection_base_pointers[cacheLevel] + target`**。`cacheLevel` 是链编码
  里的 2 bit 字段，`target` 是 30 bit 位移。没有符号名、没有 string table、没有 import 表。
- `kernel_collection_slide()`（`:211-325`）只是遍历 `dyld_chained_starts_in_image` 的
  `seg_info_offset[]`（**u32 数组**）、按 `page_start[]` 走链、调用 `fixup_value`。
- 文件里**完全没有**读 `imports_format` / `symbols_offset` 的代码；`fixupsHeader->imports_offset`
  未被使用。

实测证据（`kc_walk8.py`，见附录）：

| 项目 | 原件 `kernelcache.release.mac13g.decoded` | 本仓 `kc.mac13g.arm64.macho` |
|---|---|---|
| `imports_count` | **0** | **0** |
| 顶层 `LC_DYLD_CHAINED_FIXUPS` 覆盖段 | `__DATA_CONST`（757×16K，segoff `0xcc8000`）、`__DATA`（233×16K，segoff `0x53e0000`） | 同 |
| 链槽总数 | 960,721 | 960,721 |
| `isAuth=1` 槽 | 867,748（key IA 958,392 / DA 2,329） | **0** |
| `cacheLevel` 分布 | 全为 0（= primary KC 基址） | 同 |
| 每个 kext 自带 `LC_DYLD_CHAINED_FIXUPS` | **0/346 个** | 0/346 |

所以第 1 问里"如何把 import 名解析成内核地址"的答案是：**没有这一步**。
`__PRELINK_INFO` 不是符号表，它只是 kext 的 plist 元数据（`_PrelinkInfoDictionary`）。
跨模块引用的解析在 KC 构建时（ld64+kext linker）就已固化成 `KC_base + offset`。

### 1.5 注册：`__PRELINK_INFO,__info` 是唯一入口

`libsa/bootstrap.cpp:182-223`（`KLDBootstrap::readStartupExtensions`）：

```c
kernel_mach_header_t *mh = &_mh_execute_header;
if (PE_get_primary_kc_format(&kc_format) && kc_format == KCFormatFileset)
    mh = (kernel_mach_header_t *)PE_get_kc_header(KCKindPrimary);     // :195-197
prelinkInfoSect = getsectbynamefromheader(mh, "__PRELINK_INFO", "__info"); // :204
if (prelinkInfoSect->size) readPrelinkedExtensions(mh, KCKindPrimary);     // :205-206
else                       readBooterExtensions();                         // :208
```

`readPrelinkedExtensions`（`:228-292`）：`OSUnserializeXML(__info)` → 必须解出
`OSDictionary` → `OSKext::addKextsFromKextCollection(mh, infoDict, "__PRELINK_TEXT",
(mh->filetype == MH_FILESET) ? type : KCKindUnknown)`（`:291-292`）。
**非 fileset 时 `kc_type = KCKindUnknown`**，于是 `PE_get_kc_slide(KCKindUnknown)`
返回 `vm_kernel_slide`（`pexpert/gen/kcformat.c:180-187`）——Nabu 上该值为 0，一切绝对寻址。

`addKextsFromKextCollection`（`libkern/c++/OSKext.cpp:10887-11006`）读取的键：

| 键（`libkern/libkern/prelink.h:50-63`） | 作用 | 缺失后果 |
|---|---|---|
| `_PrelinkInfoDictionary`（数组） | 每个元素 = 一个 kext 的 Info.plist | 无 kext 注册（`OSKext.cpp:10911-10916`） |
| `_PrelinkKCID`（16 字节 data） | KC UUID，长度必须 == `sizeof(uuid_t)`，否则 **panic** | `OSKext.cpp:10902-10907` |
| `_PrelinkLinkKASLROffsets` | 仅老 kxld 布局；存在时走 `__slideOldKaslrOffsets` 合并滑动 | 可省略 |
| 每个 kext 的 `_PrelinkBundlePath` | `initWithPrelinkedInfoDict` 必需 | `OSKext.cpp:2079-2084` |
| `_PrelinkExecutableLoadAddr` | kext Mach-O 头 VA（**未滑动值**） | 无 `OSData` 包装 → 无 executable |
| `_PrelinkExecutableSize` | 同上长度 | 同上 |
| `_PrelinkKmodInfo` | kext 内 `kmod_info_t` 的 VA | "can't find prelinked kext kmod_info address"（`OSKext.cpp:2209`） |
| `OSKernelResource` | `true` 的条目是伪 kext（`com.apple.kpi.*`），只作 interface，不执行 | 依赖解析失败 |

`initWithPrelinkedInfoDict`（`OSKext.cpp:2055-2265`）随后调用
`slidePrelinkedExecutable(doCoalescedSlides)`（`:2261`），后者在 `KCFormatKCGEN` 下只做
`seg->vmaddr = ml_static_slide(seg->vmaddr)`（`OSKext.cpp:6963-6996`）——`vm_kernel_slide=0`
时是恒等变换。**它不做任何符号重定位。**

`OSKext::loadExecutable` 对 prelinked kext 直接
`if (isPrelinked()) goto register_kmod;`（`OSKext.cpp:7245-7247`），
即**不存在运行期链接器**（kxld 只服务非 prelinked kext）。这正是第二节"缺什么"的根本：
所有重定位必须在镜像生成阶段完成。

### 1.6 启动顺序：`img4if` panic 的确切位置

`osfmk/kern/startup.c`（`kernel_bootstrap`）：

| 行 | 事件 |
|---|---|
| `811` | `kernel_startup_initialize_upto(STARTUP_SUB_EARLY_BOOT)` —— **此区间内已包含 `STARTUP(ZALLOC, ..., kalloc_init)`（`kalloc.c:2088`），早于任何 kext 加载** |
| `832` | `mac_policy_initmach()` |
| `864-867` | `kernel_bootstrap_log("trust_cache_init"); trust_cache_runtime_init();` |
| `870` | `load_static_trust_cache()` |
| `880-881` | `OSKextRemoveKextBootstrap()` |
| `891-892` | `machine_lockdown()` |

`mac_policy_initmach`（`security/mac_base.c:364-378`）调用
`load_security_extensions_function()` → `bootstrapLoadSecurityExtensions`
（`libsa/bootstrap.cpp:504-558`）：遍历已注册的 kext，凡是
`AppleSecurityExtension == true` 的就 `OSKext::loadKextWithIdentifier(id, false)`
（`:546-552`）。**这就是 `AppleImage4` 与 `AMFI` 的唯一早期加载点。**

紧接着 `trust_cache_runtime_init()`（`bsd/kern/kern_trustcache.c`，三个配置变体同构）：

```c
if (img4if == NULL) panic("image4 interface not available");   // :582-585（默认变体）
if (amfi  == NULL)  panic("amfi interface not available");     // :587-589
else if (amfi->TrustCache.version < 2) panic("amfi interface is stale: %u", ...);
```

- `img4if` 定义在 `libkern/img4/interface.c:6-8`，唯一赋值点是 `:17` 的
  `img4if = i4;`，即 `img4_interface_register()`（声明见
  `libkern/libkern/img4/interface.h:399-414`）。
- `amfi` 同理：`libkern/libkern/amfi/amfi.h:312`（`extern const amfi_t * amfi;`）、
  `:321-333`（`amfi_interface_register`）。

**这两个寄存器只能由那两个 kext 的 `_start` 写。** 所以在"0 个预链接 kext"的镜像上，
走到这里必然 panic。

> **2026-11-09 更正**：该前提已不成立——镜像现在带预链接 kext，pointer slots 26,504 个、
> 其中 13,358 个指向拷贝集合外（12,940 `__TEXT_EXEC` / 336 `__DATA` / 82 `__DATA_CONST`）
> 均已按常量 slide `0x2000000` 重绑，跨模块 `BL` 重编码 19,860 处。
> 而且执行根本还没走到这里：当前实机停在 **stage 2 `IOKit entry`** 且是死等（非 panic）。
> 原先引用的 `nabu-kernel-bringup-lessons.md:157`「当前边界」一节已作废，
> 线性现状见 [`docs/nabu-20261109-status.md`](nabu-20261109-status.md)。

### 1.7 内存/pmap 契约

`osfmk/arm64/arm_vm_init.c:1994-2023` 从**内核自己的头**取段地址；
`:2025-2075` 在 fileset 情形下用**KC 头**覆盖并断言内核侧占位段必须为空：

```c
assert(segPRELINKTEXTB && !segSizePRELINKTEXT);   // :2070
assert(segPRELINKINFOB && !segSizePRELINKINFO);   // :2072
assert(segPRELINKDATAB && !segSizePRELINKDATA);   // :2064
```

非 fileset 情形下这些断言**不适用**，但同一套变量直接取自内核头：

| 变量 | 来源 | 行 |
|---|---|---|
| `segPRELINKTEXTB/segSizePRELINKTEXT` | `__PRELINK_TEXT` | `:1994` |
| `segPLKTEXTEXECB` / `segPLKDATACONSTB` | `__PLK_TEXT_EXEC` / `__PLK_DATA_CONST` | `:1996`、`:1995` |
| `segPRELINKINFOB` | `__PRELINK_INFO` | `:2015` |
| `vm_kext_base` / `vm_kext_top` | `:2094-2103` | |
| `vm_prelink_stext/etext/sinfo/einfo` | `:2106-2117` | |
| `end_kern` | `segHIGHESTKC ? segHIGHESTKC : getlastkerneladdr()` | `:2088` |
| `arm_vm_kernelcache_phys_start/end` | `segLOWEST`..`end_kern` 经 `gVirtBase/gPhysBase` 换算 | `:2202-2203` |

硬性约束：`assert(segSizePRELINKTEXT < 0x03000000)`（`:2078`，48 MB 上限）。
页保护细化在 `:1402`（`__PRELINK_TEXT` 先 RW，随后由
`OSKext::readPrelinkedExtensions` 收紧）与 `:1684`（收尾 RNX）。

---

## 二、当前链路缺什么

### 2.1 实测现状

`prelinkInfoSect->size == 0` → 走 `readBooterExtensions()`；而
`[repo]scripts/native_devtree.py:370` 生成的 `/chosen/memory-map` 是空节点，
没有 `Driver-*` 条目，因此 booter kext 路径同样为空 ⇒ **0 个 kext 注册** ⇒
`img4if == NULL`。

对 `/tmp/k8/out/kernel.ci.nabu` 的段表实测（`filetype=2`）：

```
__PRELINK_TEXT   va=0xfffffe0004004000 vmsize=0x0      sects=['__text']
__PRELINK_INFO   va=0xfffffe0007f30000 vmsize=0x0      sects=['__info']
__PLK_TEXT_EXEC  va=0xfffffe0007f30000 vmsize=0x0      sects=['__text']
__PRELINK_DATA   va=0xfffffe0007f30000 vmsize=0x0      sects=['__data']
__PLK_DATA_CONST va=0xfffffe0007f30000 vmsize=0x0      sects=['__data']
__PLK_LINKEDIT   va=0xfffffe0007f30000 vmsize=0x0      sects=['__data']
```

占位段齐备但全为空 —— 构建侧已经准备好，缺的是"内容 + 元数据 + 重定位"。

### 2.2 缺失清单

| # | 缺失项 | 归属 | 证据 / 后果 |
|---|---|---|---|
| 1 | `__PRELINK_INFO,__info` 的 XML | 镜像 | `bootstrap.cpp:204-209`；无它 → `readBooterExtensions()` |
| 2 | 每 kext 的 `_PrelinkInfoDictionary` 条目（含 `_PrelinkBundlePath` / `_PrelinkExecutableLoadAddr` / `_PrelinkExecutableSize` / `_PrelinkKmodInfo`） | 镜像 | `OSKext.cpp:2079-2084`、`2207-2221` |
| 3 | kext 段实体（`__TEXT`→`__PRELINK_TEXT`，`__TEXT_EXEC`→`__PLK_TEXT_EXEC`，`__DATA_CONST`→`__PLK_DATA_CONST`，`__DATA`→`__PRELINK_DATA`） | 镜像 | `arm_vm_init.c:1994-1996`、`prelink.h:32-41` |
| 4 | **跨模块指针重绑定**：试跑 `[repo]scripts/prelink_fileset_kernel.py` 得到 26,504 个落在被拷贝区间内的槽，其中 **13,358 个指向被拷贝集合之外**（12,940 → `__TEXT_EXEC`，336 → `__DATA`，82 → `__DATA_CONST`） | 镜像 | 全部指向 Apple 内核的函数/全局，若原样保留即跳进 Apple 地址 |
| 5 | **PC 相对分支重定位**：kext 的 `bl/b/adrp/add/ldr-literal` 编码的是 Apple 布局下的相对位移，链修复不覆盖 | 镜像 | `plan_macos26_security.py:52-66` 的解码集合就是这类指令 |
| 6 | root 集合不全：`[repo]UniXNU-nabu/board.toml:90-100` 的 9 个 root 里**没有 AppleImage4**，且 KC 里的真实 entry_id 是 `com.apple.security.AppleImage4`（不是 `com.apple.driver.AppleImage4`） | 配置 | 实测 4 个目标 root 的依赖闭包是 **11 个组件 / 3,730,479 字节代码** |
| 7 | 依赖闭包里的伪 kext（`com.apple.kpi.{bsd,dsep,iokit,libkern,mach,private,unsupported}`）已由工具识别为 `kernel_interfaces`（不进 `__PRELINK_TEXT`），但 `board.toml` 未声明它们 | 配置/镜像 | `OSKext.cpp:7428-7446`（interface 用 fake kmod_info）；`OSKext.cpp:8389-8464`（依赖必须先 started） |
| 8 | `__PRELINK_TEXT` 的**section 名**：现有工具写成 `__macho_list`（`prelink_fileset_kernel.py:231-232`），但内核在 KCGEN 分支里 `getsectbynamefromheader(xnu_mh, "__PRELINK_TEXT", "__text")` 后 `assert(prelink_sect)` | 镜像 | `kalloc.c:1106-1108` → 断言 panic |
| 9 | 每个扁平化 kext 头必须带 `LC_SEGMENT_SPLIT_INFO` | 镜像 | `kalloc.c:1175-1177`：否则 `panic("kalloc_type_view_parse: No support for non-split seg KCs")`；实测 Apple fileset kext **都不带**该 LC（`split_info=False`） |
| 10 | `__PRELINK_TEXT` 必须是 `__TEXT.filesize` 紧密packed 的 kext Mach-O 序列，尾部余量必须全零 | 镜像 | `kalloc.c:1164-1180`（`cur += align(kext_text_sz)`）、`kalloc.c:1121`（`memcmp_zero_ptr_aligned` 断言） |
| 11 | booter 侧装载窗口必须覆盖新增段 | booter | `[repo]UniXNU/drivers/DarwinBoot/DarwinBootPkg/Driver/DarwinBoot.c:574`（`ReadFile(..., SIZE_128MB, ...)`）、`:660-664`（`VirtualBase`/`TopOfKernelData`）、`:425-464`（`AllocateWindow`）——镜像从 ~24.9 MB 涨到 ~29.2 MB，仍在窗口内，但 `TopOfKernelData` 必须按最高段重算 |
| 12 | `boot_args` | **不缺** | 本版本无 `kc_*` 字段；DT 也不需要新增属性（`/chosen/memory-map` 为空不影响 KC 路径） |

结论：**缺的不是 boot_args，而是镜像内容（3/4/5/8/9/10）、元数据（1/2）与配置（6/7）。**
`DarwinBoot` 只需要保证"装载窗口 ≥ 最高段"这一件事（第 11 项）。

---

## 三、两条路线对比与推荐

### 3.1 方案 (a)：保留 Apple 的 chained-fixup 容器，让内核应用

成立条件是内核走 **fileset** 分支，即需要同时满足：

| 要求 | 依据 | 本仓现状 |
|---|---|---|
| 内核头 `flags & MH_DYLIB_IN_CACHE` | `kernel_mach_header.h:91` | ✗（`flags=0x218001`） |
| 顶层头 `filetype == MH_FILESET`，且头本身位于 `VM_KERNEL_LINK_ADDRESS + slide` | `arm_init.c:252-253` | ✗（MH_EXECUTE） |
| 内核侧 `__PRELINK_TEXT/__PRELINK_INFO/__PRELINK_DATA` 必须为空（值非 0、长度 0） | `arm_vm_init.c:2064-2074` | ✓（已是空占位） |
| 需要 `LC_FILESET_ENTRY`（346 条）+ 每 kext 头的段 vmaddr 已经是新基址 | `arm_init.c:273`（`kernel_collection_adjust_mh_addrs`） | ✗ 无 |
| 需要 `segLOWESTKC/segHIGHESTKC/segLOWESTROKC/segHIGHESTROKC` | `arm_init.c:273-276` | 需要 `kernel_collection_adjust_mh_addrs` 计算 |
| 整份 KC（116 MB）必须物理连续装载，`end_kern` 来自 `segHIGHESTKC` | `arm_vm_init.c:2088` | ✗ 当前只装 `kernel.macho` |
| 需要产出 MH_FILESET 的链接器 | ld64 `-new_linker`/`KernelCollectionBuilder` 级能力 | ✗ 构建链只有 `ld64`（`[repo]scripts/build_macos26_xnu.py` 产出 MH_EXECUTE） |
| 链格式必须是 `DYLD_CHAINED_PTR_64_KERNEL_CACHE`(8) 且 `HAS_APPLE_PAC` 下认证链可签 | `dyld_kernel_fixups.h:112-140` | ✓ 格式对得上，但我们编的是 arm64（见第四节） |

工作量本质：**要造一个 KC linker**（把 XNU + 346 kext 合成一个 MH_FILESET 镜像，重排段、
重建链、生成 `LC_FILESET_ENTRY`、算 `__PRELINK_INFO`），再把 DarwinBoot 改成装载 KC。

### 3.2 方案 (b)：把 kext 压平进我们自己的镜像，引用静态改写

成立条件是先**放弃 fileset**，走 `KCFormatKCGEN` 静态预链接布局（内核本来就在这条分支上），
然后把每个 kext 段放到固定 VA，并把两类位置相关引用改写成我们内核的地址：

1. **数据指针**（KC 的 960,721 个链槽中落在被拷贝区间内的 26,504 个）；
2. **PC 相对指令**（`bl/b/adrp/add/...`）——但在 3.3 的等距平移下，闭包内一条都不用改，只有目标落在 `com.apple.kernel` 的才需要重编码，实测越界 veneer 为 0。

需要一张 **Apple 符号 → 我们符号** 的表。这张表不需要 KDK 也能建：Apple KC 的
`__LINKEDIT` 里保留了每个 kext 的 `LC_SYMTAB`（实测 346 个 kext 头都带
`cmd=0x2`，见 `kc.mac13g.arm64.macho` 的 apfs 头 `ncmds=10`，其中
`LC_SEGMENT_64 ×5 + LC_SYMTAB + LC_DYSYMTAB + LC_UUID + LC_SOURCE_VERSION + LC_FUNCTION_STARTS`），
而 `com.apple.kernel` 这一条 entry 就是 Apple 内核的导出全集；我们内核自带 348,874 条
`LC_SYMTAB`。两边按**名字**对齐即可。KDK（`[repo]scripts/fetch_inputs.py` 固定版本）可作为
交叉校验，不是硬依赖。

### 3.3 推荐：Uniform Slide（等距稀疏平移）

> **修订（本次实现落地）**：本节原来的推荐是"把 kext 连续压实（contiguous repack）
> 到 `KEXT_ALIGN_BYTES` 边界，再重写 `adrp/add/ldr` 并为越界分支造 veneer"。
> **该方案已废弃，不得实现**，理由见 3.3.1。下面是替代方案，也是
> `scripts/prelink_kcg_bind.py` 的实际行为。

推荐方案：给**每一个**被选中 kext 的**每一个**段加**同一个常量**

```
slide = (target __PRELINK_TEXT vmaddr) − (bootkc 中所有被平移段的最小 vmaddr)
```

平移是等距变换（isometry），所以对闭包内任意引用恒有逐位相等的不变式

```
new_target − new_site  ==  old_target − old_site
```

直接后果：

* kext 内部**不需要**改写 `adrp/add/ldr`，也**不需要** veneer（实测 0 个）；
* kext 自己的 `LC_SEGMENT_64.vmaddr` **保持 Apple 原值不动**——加载器用
  `kmod_info.address − header_vmaddr` 得到的正是这一个常量 slide，
  `segment.vmaddr + slide` 对所有段同时成立，因此内嵌段表与运行地址自洽；
* 四个段组之间的原始间隙全部保留（本闭包 28.8 MB），代价是真实 RAM，
  换来的是"零指令重编码"的实现复杂度。

于是只剩两类写操作：

1. **绝对指针槽**：`+ slide`（数据段里的链槽，由顶层
   `LC_DYLD_CHAINED_FIXUPS` 解码得到）；
2. **目标落在 `com.apple.kernel` 内的引用**：`Apple VA → Apple 符号名 →
   我们内核 `LC_SYMTAB` 的 VA`。这是唯一需要符号表的地方，实测覆盖率见 3.5。

`vm_kernel_slide == 0`（`UniXNU-nabu/board.toml:89`），所以每个槽都直接写成
裸 8 字节小端绝对地址，不需要任何链结构存活。

#### 3.3.1 为什么拒绝连续压实

1. **相对距离会碎**。压实抹平 `__TEXT` / `__TEXT_EXEC` / `__DATA_CONST` /
   `__DATA` 四组之间的 29 MB 间隙，跨组引用（`adrp` 指向 `__DATA_CONST`、
   跨组 `bl`）的相对距离全部改变，于是每条 `adrp/add` 都要重算并处理
   `imm12` 低位差异，每个越界 `bl` 都要 veneer——也就是 3.2 里
   "越过 ±128 MB 的需要 veneer" 那句话的全部工程成本，全部是自找的。
2. **稀疏索引本来就已经存在**。`platform/nabu/macos26/patches/0008-nabu-sparse-prelink-index.patch`
   把 `kalloc.c` 的 `#if SHENG` 扩成 `#if SHENG || defined(ARM64_BOARD_CONFIG_NABU)`
   （并加 `kalloc_sparse_prelink_index_version = 1`），于是
   `kalloc_type_view_parse()` 走 `__PRELINK_TEXT,__macho_list` 指针数组
   （`out/macos26-xnu-nabu/source/osfmk/kern/kalloc.c:1118-1152`）。
   kext 头**不必**连续、**不必**按 `KEXT_ALIGN_BYTES` 对齐——压实解决的是
   一个我们已经没有的问题。
3. **压实不减少必写量**。被改写的槽数由引用图决定，与排布无关；压实只是把
   "29 MB 的未映射间隙"换成"29 MB 的 `adrp/add` 重编码 + veneer 管理"。

#### 3.3.2 仍然必须遵守的两条硬约束

这两条与排布无关，是内核侧的真实断言/panic 点，压实与平移都必须满足：

* `__PRELINK_TEXT` 必须是**稀疏索引**形态：`__macho_list` 的每个条目都要过
  `kalloc.c:1118-1152` 的检查（`size % 8 == 0`、索引落在段内、
  `previous_header < cur`、`cur` 落在 `__text` 区间、`magic == MH_MAGIC_64`、
  `sizeofcmds ≤ end − cur − 32`）。本节方案额外要求同一个段里**同时**存在
  `__text` 节，因为 `PE_get_primary_kc_format()` 之外还有一处
  `getsectbynamefromheader(xnu_mh, kPrelinkTextSegment, kPrelinkTextSection)`
  的查找（未打补丁的路径）——所以两个节都发，`__text` 覆盖文件区、
  `__macho_list` 紧随其后。
* `__PRELINK_TEXT` 的 `vmsize` 必须按页取整（`arm_vm_init.c:2078`
  `assert(segSizePRELINKTEXT < 0x03000000)`，且 `arm_vm_init.c:1402`
  的 `arm_vm_page_granular_RWNX()` 以页为粒度）。

#### 3.3.3 `LC_SEGMENT_SPLIT_INFO` 仍然必须发出

外部曾指示"不要再发 `LC_SEGMENT_SPLIT_INFO`"。**该指示与代码事实冲突，按代码执行**：

`out/macos26-xnu-nabu/source/osfmk/kern/kalloc.c:1175-1177`

```
if (kext_text_sz != 0 && !isSplitKext) {
        panic("kalloc_type_view_parse: No support for non-split seg KCs");
}
```

这一行在 `#if SHENG || defined(ARM64_BOARD_CONFIG_NABU)` 之外，因此对任何
`__TEXT` 文件大小非 0 的 kext 都会触发。实测 11 个组件**全部**没有
`LC_SEGMENT_SPLIT_INFO`，而头部空隙只有 64–72 字节（足够插入一条 16 字节的
`linkedit_data_command`），所以工具为每一个被摊平的 kext 插入
`LC_SEGMENT_SPLIT_INFO`（`cmd=0x1e`, `cmdsize=16`, `dataoff=0`, `datasize=0`）。

### 3.4 推荐方案的工具规格

保留 `[repo]scripts/prelink_fileset_kernel.py` 作为 **stage 1（闭包 + 几何事实）**，
新增 **stage 2** `[repo]scripts/prelink_kcg_bind.py` 与独立校验器
`[repo]scripts/prelink_verify.py`（不复用 `emit_prelink.py` 的 kcgen 假设，因为它要求
输入本身就是 kcgen prelinked kernelcache，`emit_prelink.py:138-145`）。

**输入（实际实现的 CLI）**

| 参数 | 内容 |
|---|---|
| `--native` | 我们自己的内核镜像，默认 `out/nabu/kernel/kernel.development.nabu`；本次实测用 `/tmp/k8/out/kernel.ci.nabu`（MH_EXECUTE，arm64，4K 页，slide 0，nsyms 348,874） |
| `--kc` | `out/macos26-assets/kc.mac13g.arm64.macho`（**已去 PAC** 的 arm64 版；原件 `private/apple/macos26/kernelcache.release.mac13g.decoded` 仅作核对） |
| `--fileset` | `out/macos26-assets/kc.mac13g.fileset.json`（346 条 entry 的 vmaddr/fileoff） |
| `--root` | 可重复；默认 4 个：`com.apple.security.AppleImage4`、`com.apple.driver.AppleMobileFileIntegrity`、`com.apple.filesystems.apfs`、`com.apple.security.sandbox` |
| `--output` / `--report` | 映像 + JSON 报告 |
| `--allow-unresolved` | 默认**不写映像**；此开关只在明确接受破损映像时使用 |

符号表不通过参数传入：工具直接从 `--native` 的 `LC_SYMTAB` 与 `--kc` 里
`com.apple.kernel` entry 的 `LC_SYMTAB` 现场解析（Apple 6,894 个名字 / 我们 87,229 个
有效名字）。KDK 只作交叉校验，不是硬依赖。

**处理步骤（每一步都能单独硬失败，便于定位）**

1. 依 `OSBundleLibraries` 求闭包；`OSKernelResource == true` 的 7 个 `com.apple.kpi.*`
   收进 `kernel_interfaces`，不产生代码。
2. **一个全局 `slide`**：`slide = (target __PRELINK_TEXT vmaddr) − (所有被平移段的最小
   `vmaddr` 按页向下取整)`。band 顶端紧贴内核 `__TEXT`，从而
   `end_kern = getlastkerneladdr()` 覆盖 kext（非 fileset 路径下 `segHIGHESTKC` 未设）。
   每个段的 `new_va = old_va + slide`，**段间原始间隙保留**。
3. 四个组各自成为一个发射段：`__PRELINK_TEXT`←`__TEXT`、
   `__PLK_TEXT_EXEC`←`__TEXT_EXEC`、`__PLK_DATA_CONST`←`__DATA_CONST`、
   `__PRELINK_DATA`←`__DATA`。组内保持成员相对位置（不压实、不补零、
   不按 `KEXT_ALIGN_BYTES` 对齐）。`__PRELINK_TEXT` 的 `filesize/vmsize`
   额外预留 `8 × 组件数` 的索引空间，并**按页取整**。
4. 给每个扁平化 kext 头**插入**一条 `LC_SEGMENT_SPLIT_INFO`（`cmd=0x1e`,
   `cmdsize=16`），否则 `kalloc.c:1175-1177` panic；插入前必须校验头部空隙
   （实测 64–72 字节）≥ 16 且重写后不越过第一个 section。
5. 指针重绑定：遍历顶层 `LC_DYLD_CHAINED_FIXUPS`
   （`DYLD_CHAINED_PTR_64_KERNEL_CACHE`，format 8，
   `target:30 / cacheLevel:2 / next:12 / isAuth:1`），对每个 site 求
   `old_target = KC_base + target` 并按类别处理：
   - `a` 目标落在**同一**组件内 → 写 `old_target + slide`；
   - `b` 目标落在**其它被选中**组件内 → 写 `old_target + slide`；
   - `c` 目标落在 `com.apple.kernel` 段内（**用 `vmsize` 而非 `filesize`**，否则
     `__DATA`/`__KLDDATA` 的 bss 目标会被误判成"闭包外"）→ 精确符号命中则
     `native_va`；命中不到才退到 nearest-below + 偏移，并单独计入 `approximate`；
   - `d` 其它 → 硬失败（闭包不完整）。
   名字在我们内核里不存在 → 硬失败并列入 `unresolved`，**不要**静默保留旧地址。
6. 分支重写：只对**目标落在 `com.apple.kernel` 内**的 `b/bl/b.cond/cbz/tbz`
   原地重编码（闭包内的分支由等距变换保证正确，不动）。
   注意三个陷阱：`tbz/tbnz` 的 `imm14` 是**原始字节偏移**（不乘 4）；
   `adr` 不需要处理；`ldr`-literal **不是分支**，它的目标是 kext 内的
   字面量池（一个已被 rebind 的指针槽），把它当分支会造出假阳性。
   扫描边界一律用 `LC_FUNCTION_STARTS`，不要扫全段。
   设计上留了 veneer 通道（`adrp x16; add x16; br x16`，放 `__PLK_TEXT_EXEC`
   尾部预留页），实测 **0 个**：最坏情形 `bl` 位移 ≈ 61 MB < ±128 MB。
7. 写 `__PRELINK_INFO,__info`：`plistlib` 序列化
   `{_PrelinkKCID: uuid16, _PrelinkInfoDictionary: [...每 kext + 7 个接口...]}`；
   每个 kext 填 `_PrelinkBundlePath`、`_PrelinkExecutableLoadAddr`（新头 VA）、
   `_PrelinkExecutableSize`、`_PrelinkKmodInfo`（新 VA），并保留原 `Info.plist` 的
   `CFBundleIdentifier` / `OSBundleLibraries` / `AppleSecurityExtension`。
   **不写** `_PrelinkLinkKASLROffsets`（slide=0，写了只是恒等变换）。
   接口字典保持原样，其 `_PrelinkExecutableLoadAddr` 是 Apple 的
   `0x7fffffffffffffff` 哨兵值。
8. 修 `kmod_info`：**按值定位** `address` 字段（实测偏移 **156**，不是 `kmod.h` 的 160），
   写新头 VA；紧随其后的 u64 就是 `size`。不能用 `name` 字段做校验——4 个组件的
   `name` 仍是 ld64 模板名。
9. 回写段命令（`vmaddr/vmsize/fileoff/filesize`、`initprot/maxprot`）：
   `__PRELINK_TEXT` 发**两个** section：`__text`（覆盖文件区，满足未打补丁的
   `getsectbynamefromheader` 查找）与 `__macho_list`（稀疏头索引，
   满足 `kalloc.c:1118-1152`）。**不要**把 `__macho_list` 改名或去掉。
   同时把 cputype/cpusubtype 设为 `CPU_TYPE_ARM64`(0x0100000c)/`0`。

**输出**

| 产物 | 内容 |
|---|---|
| `--output` 映像 | 82,288,640 字节（含 11 个 kext；内核 25 MB + 四个组段 54.6 MB + 索引/信息） |
| `--report` JSON | `prelink_text_layout[]`、`segment_placement`、`pointer_relocations[]`、`branch_relocations[]`、`veneers[]`、`unresolved{}`、`approximate`、`symbol_coverage`、`structural_assertions[]`、`loadable` |

`loadable` 只有在 `unresolved` 为空**且**全部结构断言通过时才为 `true`。

**验收标准（可被 boot log 判定）**

按串口出现顺序（全部满足才算通过）：

1. `OSKext` 报告预链接计数：`%u prelinked kexts`（`OSKext.cpp:10991-10995`），计数 ≥ 11；
2. `Loading security extension com.apple.security.AppleImage4.`
   （`bootstrap.cpp:546-552`）与同名 `... calling module start function.`
   （`OSKext.cpp:8466-8478`）；
3. **不再出现** `panic: image4 interface not available`（`kern_trustcache.c:582-585`）；
4. `kernel_bootstrap_log("trust_cache_init")` 之后出现
   **`OSKextRemoveKextBootstrap`**（`startup.c:880-881`）—— 这一行同时证明
   `trust_cache_runtime_init()` 与 `load_static_trust_cache()` 都返回了，是**最强的单行判据**；
5. 出现 `Kext com.apple.filesystems.apfs executable loaded; N pages at 0x...`
   （`OSKext.cpp:7512-7521`）与 `Kext com.apple.security.sandbox ... Loaded successfully
   from ... KC`（`OSKext.cpp:6312-6318`）。

推荐的观察位：`-v` + `serial=3` 下把 `kOSKextLogProgressLevel` 打开
（`board.toml:121-124` 的 `kernel_args` 追加 `kextlog=0xffff`），否则第 1/2/5 项会被日志级别过滤。

（附注：若只想先验证"panic 点可越过"，在 `libkern/img4/interface.c:17`、amfi 注册处各放一个
`#if NABU_STUB` 的最小实现即可让第 3/4 项变绿——但那**不验证 KC 路径**，只能当诊断开关，
不能当成第二节清单的验收。）

---

### 3.5 实现记录（`scripts/prelink_kcg_bind.py`，已在本机跑通）

输入：`--native /tmp/k8/out/kernel.ci.nabu`（24,982,320 字节，md5
`d3a341cbaf670cfaee6ec45ead3ebe22`，`__TEXT` @ `0xfffffe0007004000`）+
`out/macos26-assets/kc.mac13g.arm64.macho`（116,572,160 字节，MH_FILESET）+
`kc.mac13g.fileset.json`。默认 roots（可 `--root` 覆盖）：

```
com.apple.security.AppleImage4            com.apple.driver.AppleMobileFileIntegrity
com.apple.filesystems.apfs                com.apple.security.sandbox
```

> `board.toml` 的 9 个默认 root 里写的是 `com.apple.driver.AppleImage4`，
> 但 26.5 集合里真实 entry_id 是 `com.apple.security.AppleImage4`；
> `board.toml` 那份清单需要同步修正。

实测结果（可复现，`scripts/prelink_kcg_bind_tests.sh`）：

| 指标 | 值 |
|---|---|
| 依赖闭包 | 11 个组件 + 7 个 `com.apple.kpi.*` 接口 |
| 组件代码（`__TEXT`+`__TEXT_EXEC`） | 3,730,479 字节（3.56 MB） |
| 全局 slide | `-0x5787000` |
| 槽总数 / 分类 | 26,504；`a`=12,615 `b`=531 `c`=13,358 `d`=0 |
| 指针改写 | 26,504（闭包内 + slide） |
| 分支改写（原地） | 19,862（`bl`=18,921、`b`=941） |
| `adrp+add` 改写 | 0（跨模块数据访问全部走 `bl`/指针槽） |
| veneer | 0 |
| 近似绑定 | 2（corecrypto 两处 C++ vtable 尾调用，目标非符号边界，按 nearest-below + 偏移） |
| 符号覆盖率 | 1,564 个不同外部目标，**精确命中 100.00%**；名字在本内核存在 100.00%（Apple 6,894 个名字中 6,637 个存在 = 96.3%） |
| `__PRELINK_INFO` | 18 条（11 kext + 7 接口），56,197 字节 |
| `__macho_list` | 11 条，`0xfffffe000250c000` |
| band | `0xfffffe0001c8e000`–`0xfffffe0007004000`，83.46 MB（其中 28.84 MB 是从属间隙） |
| 输出 | 82,288,640 字节，sha256 `c7b36ded00af7651664e9cb5d97545d75b1052c4968b02d988d469ee33042c84` |

段放置（`filesize`/`vmsize` 均页对齐）：

| 段 | vmaddr | vmsize | fileoff | prot |
|---|---|---|---|---|
| `__PRELINK_TEXT` | `0xfffffe0001c8e000` | `0x87f000` | `0x17d4000` | 5 |
| `__PLK_DATA_CONST` | `0xfffffe0002864000` | `0x8a2000` | `0x4400000` | 3 |
| `__PLK_TEXT_EXEC` | `0xfffffe0003952000` | `0x23ad000` | `0x2053000` | 5 |
| `__PRELINK_DATA` | `0xfffffe0006e2c000` | `0x1c9000` | `0x4ca2000` | 3 |
| `__PRELINK_INFO` | `0xfffffe0006ff6000` | `0xe000` | `0x4e6c000` | 3 |

band 顶端紧贴内核 `__TEXT`，因此 `end_kern = getlastkerneladdr()` 覆盖 kext；
窗口需求 ≈ 83.5 MB + 25 MB < 254 MB 的 `WINDOW_SIZE` 守卫
（DarwinBoot.c:20），文件 82.3 MB < 128 MB 的 `ReadFile` 上限。

实现中踩到并已修正的三个真实陷阱（都写成了自检，防止回归）：

1. **`kmod_info` 的字段偏移不是 `kmod.h` 的**。实测 `address` 落在
   **偏移 156**（不是 160），`size` 紧随其后。工具改为按值定位：结构体内
   **恰好一个** u64 等于该 kext 的 load address，那个偏移就是 `address`，
   然后校验 `+8` 处的 `size` 合理。11 个组件全部得到 156，写入后
   `_PrelinkExecutableLoadAddr` 与 `__macho_list` 一一对应。
   顺带发现 4 个组件的 `kmod_info.name` 是 ld64 模板名
   （`com.company.driver.modulename`、`com.apple.iokit.CoreAnalytics` 等），
   所以**不能**用 name 字段做一致性校验。
2. **`tbz/tbnz` 的立即数不是 `b.cond` 那种位域拆分**。`imm14` 是原始字节偏移
   （bits 18:5），不乘 4。解码写错的后果是把 17 条普通条件跳转误译成
   "跳进内核"，进而被记成不可 veneer 的未解析。工具现在启动时先跑
   `decoder_self_test()`：`b/bl/b.cond/cbz/tbz/adrp` 全部做
   encode→decode 往返断言，不通过就直接失败。
3. **稀疏索引必须在布局阶段预留**。若在四组段已经排到文件上之后再"追加"
   `__macho_list`，`__PRELINK_TEXT` 的 filesize 会越过下一组 `__PLK_TEXT_EXEC`
   的 fileoff，后者发布时把索引本身覆盖成全 0。现在 table 空间在算 slide
   之前就进入 `file_high`，并额外做一次"发布后读回索引 == 计算值"的断言。

## 四、arm64e 专属要求与 Kryo 485 的冲突

### 4.1 冲突点

| arm64e 要求 | 出处 | Kryo 485 上是否成立 | 处理 |
|---|---|---|---|
| `cpusubtype = CPU_SUBTYPE_ARM64E (0x80000002)`，ABI 含 PAC 调用约定（`pacia`/`autia`、`retab` 返回、`braa` 间接跳转） | `[repo]scripts/nabu_pac_strip.py:18-19`、`:22-` | ✗ 无 FEAT_PAuth 分支指令 | 已由 `nabu_pac_strip.py` 逐指令翻译（`RETAA/RETAB→RET`、`BLRAA→BLR`、`BRAА→BR`…），`kc.mac13g.arm64.macho` 的 `cpusubtype` 实测已为 `0x0` |
| 链编码 `isAuth=1` 需 `sign_pointer`（`ptrauth_sign_unauthenticated`，IA/IB/DA/DB 四 key） | `dyld_kernel_fixups.h:74-100` | ✗ `HAS_APPLE_PAC=0` 时该函数直接返回 target，且 `fixup_value` 对 `isAuth` 走 `dyldLogFunc("Unexpected authenticated fixup"); *stop = 1;`（`:131-137`） | 原件 867,748 个认证槽必须清零；翻译版实测已是 0 |
| `__AUTH` / `__auth_got` / `__auth_ptr` / `__auth_const` 段 | arm64e KC 的 GOT 以认证指针形式存在 | ✗ 我们内核段表里没有这些段；`arm_vm_init.c:1994-2023` 也不查它们 | 翻译阶段须把外部全局访问从 `__auth_got` 改成普通 `__got` 或直接 `adrp+add`（`nabu_pac_strip.py` 的链格式转换已有此方向） |
| `__TEXT,__thread_starts`（threaded rebase） | `arm_init.c:217-218`、`:288-291` | 我们镜像该 section **长度为 0** → `static_kernelcache=false` → `rebase_threaded_starts` 不执行 | 一致：Nabu 固定 slide=0，本来就不需要 rebase |
| FEAT_SHA3 / FEAT_SM4 代码生成 | Apple 默认 `-march` 会吐 `bcax/eor3/rax1` | ✗ A76 只有 AES/SHA1/SHA2/CRC | 已在编译期关闭（`[repo]docs/nabu-kernel-bringup-lessons.md:118-126` 的 `-Xclang -target-feature -Xclang -sha3 -Xclang -target-feature -Xclang -sm4`）；**对 kext 同样成立**：扁平化前须扫描每个拷贝段的 `bcax/eor3/rax1/sm4e` 并拒绝或替换 |

### 4.2 与本仓软 PAC 工作的交互

`[repo]platform/nabu/macos26/pac/`：

- `0001-nabu-armv82-soft-pac.patch` + `nabu_soft_pac.h` —— 在 **内核源码**侧提供
  "软 PAC"：把 `__builtin_ptrauth_*` 与 PAC 相关的 TLS/密钥操作降级为无操作或软件校验。
- `smp_keys.h` / `smp_keys_storage.h` —— EL1 密钥状态的落盘/恢复（`docs/macos26-security.md`
  描述的"signed pointer 的 key 状态通过 EL1 密钥寄存器管理"）。

它与 KC 路径的交互，可以归结为三个"不会"：

1. **不会**让 `dyld_kernel_fixups.h` 的 `sign_pointer` 生效 —— 因为那取决于
   `HAS_APPLE_PAC`（编译期宏，`dyld_kernel_fixups.h:37-39`、`:79-98`），而不是运行期密钥状态。
   所以在 arm64 构建里，**任何 `isAuth=1` 的链槽都是致命错误**，必须在镜像阶段清零
   （这正是 `nabu_pac_strip.py` 做的事，也是方案 (b) 里"直接写裸绝对地址"的依据）。
2. **不会**替我们完成跨模块符号解析 —— 软 PAC 只处理"指针的认证位/密钥"，不处理
   "指针指向哪"。第二节第 4/5 项的重绑定与软 PAC 完全正交。
3. **会**影响 `OSRuntimeInitializeCPP` 的增强器签名：kext 的 C++ 静态构造走
   `OSRuntimeInitializeCPP` → `OSRuntimeSignStructorsInFileset`（`arm_init.c:220-221`，
   仅 `HAS_APPLE_PAC` 时存在）。Nabu 上该路径不编译，所以 kext 的 `__mod_init_func`/
   `__init_offsets` 必须以**未签名**形式保留；扁平化时不要顺手把 `__DATA_CONST` 里的
   构造器表当成普通指针去"认证"。

一句话：**软 PAC 让"内核自身"能在无 PAC 硬件上跑；KC kext 能不能跑，取决于镜像生成阶段
有没有把认证链清零、有没有把跨模块引用改写，软 PAC 不参与。**

---

## 五、尚未闭环的风险（实现时必须显式失败的点）

1. **`__TEXT_EXEC` 里的非指令数据**。12,940 个外部指针大多落在 Apple 内核
   `__TEXT_EXEC`，按名字反查时若同一 VA 对应多个符号（局部符号 + 导出别名），必须报歧义
   而不是任选（`plan_macos26_security.py:161-169` 已用 `blockers` 表达这一点）。
2. **`com.apple.kpi.*` 的 interface 语义**。它们不产生代码，但 `OSKext::start` 对
   `isInterface()` 直接返回成功（`OSKext.cpp:8396-8399`），而 `register_kmod` 会为它们造
   fake `kmod_info`（`OSKext.cpp:7428-7446`）。若 `_PrelinkInfoDictionary` 里漏掉，依赖检查
   会在 `OSKext::start` 的 `:8428-8464` 处失败（"dependency not started yet"）。
3. **`execve` 前的 `kalloc` 依赖**：`__PRELINK_TEXT` 一旦非空，`kalloc_init`（`kalloc.c:2088`
   的 `STARTUP(ZALLOC)`）就一定在 kext 加载**之前**遍历它。所以第 3/9/10 项（packed 布局、
   `__text` 段名、`LC_SEGMENT_SPLIT_INFO`）是"要么全对、要么核在打印第一条 kext 日志之前
   就 panic"的全有全无项。
4. **48 MB 上限**：`assert(segSizePRELINKTEXT < 0x03000000)`（`arm_vm_init.c:2078`）。
   4 个 root 的闭包实测 3,730,479 字节代码，余量充足，但扩到 `--all`（346 个）会撞线。

---

### 5.1 pmap 顺序导致的 W^X 放松（boot 前必须确认）

`arm_vm_init.c` 对 prelink 段的映射是**按名逐个**做的，顺序是：

```
:1402  arm_vm_page_granular_RWNX(segPRELINKTEXTB,   segSizePRELINKTEXT)
:1405  arm_vm_page_granular_RWNX(segPLKDATACONSTB,  segSizePLKDATACONST)
:1408  arm_vm_page_granular_ROX (segPLKTEXTEXECB,   segSizePLKTEXTEXEC)
:1413  else 分支（PLK_DATA_CONST / PLK_TEXT_EXEC 都非空时走 :1421）
:1474  arm_vm_page_granular_RWNX(segPRELINKDATAB,   segSizePRELINKDATA)
```

问题在 `:1421` 之后：当 `segSizePLKDATACONST > 0` 且 `segSizePLKTEXTEXEC > 0` 时，
内核会把 `[segPLKDATACONSTB + segSizePLKDATACONST, segTEXTB)` 这**整段**重新按
RWNX 映射。本方案的 VA 顺序是

```
__PRELINK_TEXT(0x1c8e000) < __PLK_DATA_CONST(0x2864000) < __PLK_TEXT_EXEC(0x3952000)
                          < __PRELINK_DATA(0x6e2c000) < kernel __TEXT(0x7004000)
```

于是这条 RWNX 重映射会**覆盖掉 `__PLK_TEXT_EXEC` 刚刚拿到的 ROX**，kext 代码段
最终是 W+X。功能上不阻塞启动，但这是安全属性的实质倒退，且 `:1684-1685`
的最终收紧只对 `__PRELINK_TEXT` 与 `__PLK_DATA_CONST` 做 RNX，不含 `__PLK_TEXT_EXEC`。

若要让 `__PLK_TEXT_EXEC` 保持只读，必须把 `__PLK_DATA_CONST` 排到
`__PLK_TEXT_EXEC` **之后**（这样 `segPLKDATACONST + size` 落在 TEXT_EXEC 之上，
`:1421` 的重映射区间就不再包含它），代价是 `__DATA_CONST` 与 `__PRELINK_DATA`
的相对距离变化——`adrp` 各自 ±4 GB 范围内仍然成立，但会破坏"一个全局 slide"的
等距性，需要重算 `adrp`。这是本次**未做**的取舍，先按功能优先。

## 附录：本次分析用的临时脚本（均在 `/tmp`，未进仓库）

- `/tmp/kc_probe2.py` —— 打印 Mach-O/KC 的段表、`LC_*` 概况、`LC_FILESET_ENTRY` 列表。
- `/tmp/kc_walk8.py` —— 按 `DYLD_CHAINED_PTR_64_KERNEL_CACHE`（`pointer_format=8`）解码
  链，统计 `slots / isAuth / cacheLevel / key / 最大 target`；同时确认"每个 kext 是否自带
  `LC_DYLD_CHAINED_FIXUPS`"（答案：0/346）。
- 试跑 `[repo]scripts/prelink_fileset_kernel.py --kernel /tmp/k8/out/kernel.ci.nabu
  --kernelcache out/macos26-assets/kc.mac13g.arm64.macho --root com.apple.security.AppleImage4
  --root com.apple.driver.AppleMobileFileIntegrity --root com.apple.filesystems.apfs
  --root com.apple.security.sandbox --output /tmp/kcpre/kernel.prelinked.nabu
  --report /tmp/kcpre/report.json`，得到第二节第 4 项的数字。

注意 `dyld_chained_starts_in_image.seg_info_offset[]` 是 **u32** 数组
（`EXTERNAL_HEADERS/mach-o/fixup-chains.h`），按 u16 读会得出错误的段索引 —— 本文数据已按
u32 修正（`__DATA_CONST` 索引 2、`__DATA` 索引 5）。
