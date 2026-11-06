#!/usr/bin/env python3
"""Build the complete Xiaomi Pad 5 (nabu / SM8150) fastboot boot image.

Integrates upstream UniXNU components:
  - UniXNU/DarwinBoot (ARM64 Linux Image header + FDT/NRAM loader + DarwinEnter EL1 handoff)
  - UniXNU/ArmSmmu4K (4K stage-1 LPAE page-table builder for SM8150 SMMUv2)
  - UniXNU-nabu (compact_loader_kernel sparse Mach-O packer + Qualcomm ABL DTB tags)
  - macOS 26.5 BootKC (kernelcache.release.mac13g.nabu.kc verification metadata)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from package_nabu_fastboot import package_nabu_fastboot
import gzip
import shutil


def verify_android_boot_image(boot_img_path: Path) -> dict[str, Any]:
    """Parse and verify the Android Boot Image v0, gzipped ARM64 Image + FDT, and NRAM ramdisk."""
    raw = boot_img_path.read_bytes()
    magic, kernel_sz, kernel_addr, ramdisk_sz, ramdisk_addr, second_sz, second_addr, tags_addr, page_sz = (
        struct.unpack_from("<8s8I", raw, 0)
    )
    if magic != b"ANDROID!":
        raise ValueError(f"Invalid Android boot image magic: {magic!r}")

    k_off = page_sz
    k_dtb = raw[k_off : k_off + kernel_sz]
    r_off = k_off + _align_up(kernel_sz, page_sz)
    ramdisk = raw[r_off : r_off + ramdisk_sz]

    # Locate FDT (0xd00dfeed big-endian) appended after the gzip stream
    fdt_idx = k_dtb.rfind(b"\xd0\x0d\xfe\xed")
    if fdt_idx <= 0:
        raise ValueError("Missing appended FDT (0xd00dfeed) in kernel+dtb payload")
    gz_stream = k_dtb[:fdt_idx]
    fdt_blob = k_dtb[fdt_idx:]
    decompressed_loader = gzip.decompress(gz_stream)
    if decompressed_loader[0x38:0x3C] != b"ARM\x64":
        raise ValueError("Decompressed loader missing ARM64 Image magic 'ARM\\x64' at offset 0x38")

    nram_magic, k_len, adt_len = struct.unpack_from("<4sII", ramdisk, 0)
    if nram_magic != b"NRAM":
        raise ValueError(f"Invalid NRAM ramdisk magic: {nram_magic!r}")
    macho_magic, cputype = struct.unpack_from("<II", ramdisk, 12)
    if macho_magic != MH_MAGIC_64 or cputype != CPU_TYPE_ARM64:
        raise ValueError("Embedded kernel.macho inside NRAM is not a valid 64-bit ARM64 Mach-O")

    return {
        "header_magic": magic.decode("ascii"),
        "page_size": page_sz,
        "kernel_dtb_bytes": kernel_sz,
        "decompressed_darwinboot_bytes": len(decompressed_loader),
        "darwinboot_arm64_magic": "ARM\\x64",
        "appended_fdt_bytes": len(fdt_blob),
        "fdt_has_qcom_board_id": b"qcom,board-id" in fdt_blob,
        "fdt_has_qcom_msm_id": b"qcom,msm-id" in fdt_blob,
        "nram_magic": "NRAM",
        "nram_kernel_bytes": k_len,
        "nram_adt_bytes": adt_len,
        "kernel_macho_magic": hex(macho_magic),
    }



MH_MAGIC_64 = 0xFEEDFACF
CPU_TYPE_ARM64 = 0x0100000C
CPU_SUBTYPE_ARM64_ALL = 0x00000000
MH_EXECUTE = 0x2
MH_NOUNDEFS = 0x1
MH_PIE = 0x200000

LC_SEGMENT_64 = 0x19
LC_UNIXTHREAD = 0x5
LC_UUID = 0x1B
LC_BUILD_VERSION = 0x32

ARM_THREAD_STATE64 = 6
ARM_THREAD_STATE64_COUNT = 68

KERNEL_LINK_BASE = 0xFFFFFE0007004000
PAGE_SIZE_4K = 0x1000
PAGE_SIZE_16K = 0x4000


DARWINBOOT_LDS = """
OUTPUT_FORMAT("elf64-littleaarch64")
OUTPUT_ARCH(aarch64)
ENTRY(_start)

SECTIONS
{
    . = 0x0;
    .head.text : {
        KEEP(*(.head.text))
    }
    .text : ALIGN(16) {
        *(.text.darwin_enter)
        *(.text .text.*)
    }
    .rodata : ALIGN(16) {
        *(.rodata .rodata.*)
    }
    .data : ALIGN(16) {
        *(.data .data.*)
    }
    .got : ALIGN(16) {
        *(.got .got.*)
    }
    .bss : ALIGN(16) {
        *(.bss .bss.* COMMON)
    }
    /DISCARD/ : {
        *(.comment)
        *(.note*)
        *(.eh_frame*)
    }
}
"""


STAGE1_KERNEL_LDS = """
OUTPUT_FORMAT("elf64-littleaarch64")
OUTPUT_ARCH(aarch64)
ENTRY(__start)

SECTIONS
{
    . = 0x0;
    .text : ALIGN(16) {
        KEEP(*(.text.entry))
        *(.text .text.*)
        *(.rodata .rodata.*)
    }
    . = ALIGN(0x4000);
    __data_start = .;
    .data : ALIGN(16) {
        *(.data .data.*)
        *(.got .got.*)
    }
    __bss_start = .;
    .bss : ALIGN(16) {
        *(.bss .bss.* COMMON)
    }
    __bss_end = .;
    /DISCARD/ : {
        *(.comment)
        *(.note*)
        *(.eh_frame*)
    }
}
"""


def _align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def compile_darwinboot_fastboot_loader(out_dir: Path) -> Path:
    """Compile the bare-metal ARM64 Linux Image-compatible DarwinBoot Stage-1 loader."""
    boot_src = ROOT / "platform" / "nabu" / "macos26" / "boot"
    lds_path = out_dir / "darwinboot_fastboot.lds"
    elf_path = out_dir / "nabu-darwinboot-fastboot.elf"
    bin_path = out_dir / "nabu-darwinboot-fastboot.bin"

    lds_path.write_text(DARWINBOOT_LDS, encoding="utf-8")

    cmd = [
        "/bin/aarch64-linux-gnu-gcc",
        "-march=armv8.2-a",
        "-ffreestanding",
        "-nostdlib",
        "-fno-builtin",
        "-fno-stack-protector",
        "-fPIC",
        "-no-pie",
        "-O2",
        "-Wall",
        "-Wextra",
        f"-I{ROOT}",
        f"-I{boot_src}",
        "-Wl,--build-id=none,--no-warn-rwx-segments",
        f"-T{lds_path}",
        str(boot_src / "nabu_darwinboot_entry.S"),
        str(boot_src / "nabu_darwinboot_fastboot.c"),
        "-o",
        str(elf_path),
    ]
    subprocess.run(cmd, check=True)
    subprocess.run(
        ["/bin/aarch64-linux-gnu-objcopy", "-O", "binary", str(elf_path), str(bin_path)],
        check=True,
    )

    raw = bytearray(bin_path.read_bytes())
    if len(raw) < 64 or raw[0x38:0x3C] != b"ARM\x64":
        raise RuntimeError("Compiled DarwinBoot loader is missing ARM64 Linux Image magic 'ARM\\x64' at 0x38")

    # Patch image_size (offset 0x10) to cover the binary plus BSS/stack headroom
    effective_image_size = max(_align_up(len(raw) + 0x40000, PAGE_SIZE_16K), 0x80000)
    struct.pack_into("<Q", raw, 0x10, effective_image_size)
    bin_path.write_bytes(bytes(raw))
    return bin_path


def _build_macho_executable(text_bytes: bytes, data_bytes: bytes, bss_size: int) -> bytes:
    """Wrap compiled AArch64 .text and .data into a 64-bit XNU MH_EXECUTE Mach-O."""
    text_Segment_fileoff = PAGE_SIZE_16K
    text_filesz = _align_up(len(text_bytes), PAGE_SIZE_16K)
    text_vmsize = text_filesz
    text_vmaddr = KERNEL_LINK_BASE

    data_fileoff = text_Segment_fileoff + text_filesz
    data_filesz = _align_up(len(data_bytes), PAGE_SIZE_16K) if data_bytes else PAGE_SIZE_16K
    data_vmsize = _align_up(len(data_bytes) + bss_size, PAGE_SIZE_16K)
    if data_vmsize < data_filesz:
        data_vmsize = data_filesz
    data_vmaddr = text_vmaddr + text_vmsize

    def _pack_segment_64(
        segname: bytes,
        vmaddr: int,
        vmsize: int,
        fileoff: int,
        filesize: int,
        maxprot: int,
        initprot: int,
        sectname: bytes,
        sect_addr: int,
        sect_size: int,
        sect_offset: int,
        sect_flags: int,
    ) -> bytes:
        seg_cmd_size = 72 + 80
        seg_hdr = struct.pack(
            "<II16sQQQQiiII",
            LC_SEGMENT_64,
            seg_cmd_size,
            segname.ljust(16, b"\x00"),
            vmaddr,
            vmsize,
            fileoff,
            filesize,
            maxprot,
            initprot,
            1,
            0,
        )
        sect_hdr = struct.pack(
            "<16s16sQQIIIIIIII",
            sectname.ljust(16, b"\x00"),
            segname.ljust(16, b"\x00"),
            sect_addr,
            sect_size,
            sect_offset,
            4,
            0,
            0,
            sect_flags,
            0,
            0,
            0,
        )
        return seg_hdr + sect_hdr

    cmd_text = _pack_segment_64(
        segname=b"__TEXT",
        vmaddr=text_vmaddr,
        vmsize=text_vmsize,
        fileoff=text_Segment_fileoff,
        filesize=text_filesz,
        maxprot=5,
        initprot=5,
        sectname=b"__text",
        sect_addr=text_vmaddr,
        sect_size=len(text_bytes),
        sect_offset=text_Segment_fileoff,
        sect_flags=0x80000400,
    )

    cmd_data = _pack_segment_64(
        segname=b"__DATA",
        vmaddr=data_vmaddr,
        vmsize=data_vmsize,
        fileoff=data_fileoff,
        filesize=data_filesz,
        maxprot=3,
        initprot=3,
        sectname=b"__data",
        sect_addr=data_vmaddr,
        sect_size=len(data_bytes),
        sect_offset=data_fileoff,
        sect_flags=0x00000000,
    )

    # LC_UNIXTHREAD (ARM_THREAD_STATE64: x0..x28, fp, lr, sp, pc, cpsr, pad)
    # 16 bytes header + 272 bytes state = 288 bytes
    regs = [0] * 32
    pc = text_vmaddr
    cpsr = 0x3C5  # EL1h, DAIF masked
    thread_state = struct.pack("<32QQII", *regs, pc, cpsr, 0)
    cmd_unixthread = struct.pack(
        "<IIII",
        LC_UNIXTHREAD,
        16 + len(thread_state),
        ARM_THREAD_STATE64,
        ARM_THREAD_STATE64_COUNT,
    ) + thread_state

    uuid_bytes = hashlib.sha256(text_bytes + data_bytes).digest()[:16]
    cmd_uuid = struct.pack("<II16s", LC_UUID, 24, uuid_bytes)

    # LC_BUILD_VERSION: platform=1 (MACOS), minos=26.5.0 (0x001a0500), sdk=26.5.0
    cmd_build_ver = struct.pack("<IIIIII", LC_BUILD_VERSION, 24, 1, 0x001A0500, 0x001A0500, 0)

    load_commands = cmd_text + cmd_data + cmd_unixthread + cmd_uuid + cmd_build_ver
    ncmds = 5
    sizeofcmds = len(load_commands)

    mach_header = struct.pack(
        "<IIIIIIII",
        MH_MAGIC_64,
        CPU_TYPE_ARM64,
        CPU_SUBTYPE_ARM64_ALL,
        MH_EXECUTE,
        ncmds,
        sizeofcmds,
        MH_NOUNDEFS | MH_PIE,
        0,
    )

    image = bytearray(data_fileoff + data_filesz)
    header_and_cmds = mach_header + load_commands
    image[0 : len(header_and_cmds)] = header_and_cmds
    image[text_Segment_fileoff : text_Segment_fileoff + len(text_bytes)] = text_bytes
    image[data_fileoff : data_fileoff + len(data_bytes)] = data_bytes
    return bytes(image)


def compile_stage1_kernel_macho(out_dir: Path) -> Path:
    """Compile the Nabu Stage-1 XNU verification kernel + UniXNU ArmSmmu4K into kernel.macho."""
    boot_src = ROOT / "platform" / "nabu" / "macos26" / "boot"
    smmu_src = ROOT / "sources" / "unixnu-components" / "ArmSmmu4K"
    lds_path = out_dir / "stage1_kernel.lds"
    elf_path = out_dir / "nabu-stage1-kernel.elf"
    text_bin = out_dir / "nabu-stage1-kernel.text.bin"
    data_bin = out_dir / "nabu-stage1-kernel.data.bin"
    macho_path = out_dir / "kernel.macho"

    lds_path.write_text(STAGE1_KERNEL_LDS, encoding="utf-8")

    cmd = [
        "/bin/aarch64-linux-gnu-gcc",
        "-march=armv8.2-a",
        "-ffreestanding",
        "-nostdlib",
        "-fno-builtin",
        "-fno-stack-protector",
        "-fPIC",
        "-no-pie",
        "-O2",
        "-Wall",
        "-Wextra",
        f"-I{ROOT}",
        f"-I{boot_src}",
        f"-I{smmu_src}",
        "-Wl,--build-id=none,--no-warn-rwx-segments",
        f"-T{lds_path}",
        str(boot_src / "nabu_stage1_kernel.c"),
        str(smmu_src / "ArmSmmu4K.c"),
        "-o",
        str(elf_path),
    ]
    subprocess.run(cmd, check=True)

    subprocess.run(
        [
            "/bin/aarch64-linux-gnu-objcopy",
            "-O",
            "binary",
            "--only-section=.text",
            str(elf_path),
            str(text_bin),
        ],
        check=True,
    )
    subprocess.run(
        [
            "/bin/aarch64-linux-gnu-objcopy",
            "-O",
            "binary",
            "--only-section=.data",
            str(elf_path),
            str(data_bin),
        ],
        check=True,
    )

    text_bytes = text_bin.read_bytes() if text_bin.exists() else b""
    data_bytes = data_bin.read_bytes() if data_bin.exists() else b""
    macho_bytes = _build_macho_executable(text_bytes, data_bytes, bss_size=0x10000)
    macho_path.write_bytes(macho_bytes)
    return macho_path


def compile_nabu_fastboot_dtb(out_dir: Path) -> Path:
    """Compile nabu.dtb with Qualcomm ABL SM8150/Nabu board identification properties."""
    base_dts = (ROOT / "dts" / "nabu.dts").read_text(encoding="utf-8")
    # Inject Qualcomm ABL root tags (msm-id 339 = SM8150, board-id 48 = Xiaomi Pad 5 nabu)
    target = '    compatible = "xiaomi,nabu", "qcom,sm8150";\n'
    abl_props = (
        target
        + "    qcom,msm-id = <339 0x20000>, <339 0x10000>;\n"
        + "    qcom,board-id = <48 0>;\n"
    )
    if target not in base_dts:
        raise RuntimeError("Could not find compatible line in dts/nabu.dts")
    patched_dts = base_dts.replace(target, abl_props, 1)
    fastboot_dts_path = out_dir / "nabu-fastboot.dts"
    dtb_path = out_dir / "nabu.dtb"
    fastboot_dts_path.write_text(patched_dts, encoding="utf-8")

    subprocess.run(
        [
            "/bin/dtc",
            "-i",
            str(ROOT / "dts"),
            "-I",
            "dts",
            "-O",
            "dtb",
            "-o",
            str(dtb_path),
            str(fastboot_dts_path),
        ],
        check=True,
    )
    return dtb_path


def build_nabu_fastboot_boot_suite(
    out_dir: Path | None = None,
    vc_scale: int = 2,
    managed_rt_normal: bool = True,
    source_text: bool = True,
) -> dict[str, Any]:
    """Compile all Stage-1 binaries, DTB, and package the Nabu fastboot boot image."""
    if out_dir is None:
        out_dir = ROOT / "out" / "nabu-fastboot-boot"
    out_dir.mkdir(parents=True, exist_ok=True)
    image_dir = out_dir / "image"
    if image_dir.exists():
        shutil.rmtree(image_dir)

    loader_bin = compile_darwinboot_fastboot_loader(out_dir)
    kernel_macho = compile_stage1_kernel_macho(out_dir)
    dtb_path = compile_nabu_fastboot_dtb(out_dir)

    bootkc_report_path = ROOT / "out" / "nabu-bootkc" / "nabu_bootkc_translation_report.json"
    bootkc_kc_path = ROOT / "out" / "nabu-bootkc" / "kernelcache.release.mac13g.nabu.kc"
    bootkc_meta: dict[str, Any] = {}
    if bootkc_report_path.exists():
        report_raw = bootkc_report_path.read_bytes()
        bootkc_meta = json.loads(report_raw.decode("utf-8"))
        if bootkc_kc_path.exists():
            bootkc_meta["translated_kc_path"] = str(bootkc_kc_path)
            bootkc_meta["translated_kc_size"] = bootkc_kc_path.stat().st_size

    pkg_summary = package_nabu_fastboot(
        kernel_macho,
        loader_bin.read_bytes(),
        dtb_path.read_bytes(),
        image_dir,
        cpus=8,
        owned_memory_size=0x20000000,
        enable_ufs_windows=True,
    )

    boot_img_path = image_dir / pkg_summary["image_name"]
    verification = verify_android_boot_image(boot_img_path)

    full_summary = {
        **pkg_summary,
        "boot_image": str(boot_img_path),
        "verification": verification,
        "compiled_artifacts": {
            "darwinboot_loader_bin": str(loader_bin),
            "darwinboot_loader_size": loader_bin.stat().st_size,
            "darwinboot_loader_sha256": hashlib.sha256(loader_bin.read_bytes()).hexdigest(),
            "stage1_kernel_macho": str(kernel_macho),
            "stage1_kernel_size": kernel_macho.stat().st_size,
            "stage1_kernel_sha256": hashlib.sha256(kernel_macho.read_bytes()).hexdigest(),
            "nabu_dtb": str(dtb_path),
            "nabu_dtb_size": dtb_path.stat().st_size,
            "nabu_dtb_sha256": hashlib.sha256(dtb_path.read_bytes()).hexdigest(),
        },
        "unixnu_integrations": {
            "DarwinBoot": "ARM64 Linux Image header + FDT/NRAM unpack + DarwinEnter EL1 handoff",
            "ArmSmmu4K": "SM8150 SMMUv2 4K stage-1 LPAE page table builder (36-bit IA/OA)",
            "nabu_managed_rt_normal": managed_rt_normal,
            "nabu_vc_scale": vc_scale,
            "nabu_source_text": source_text,
            "qcom_abl_dtb_tags": {
                "msm_id": [339, 0x20000, 339, 0x10000],
                "board_id": [48, 0],
            },
        },
        "macos26_bootkc": bootkc_meta,
    }

    (image_dir / "build.json").write_text(json.dumps(full_summary, indent=2) + "\n", encoding="utf-8")
    return full_summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "out" / "nabu-fastboot-boot",
        help="Output directory for Nabu fastboot boot build artifacts",
    )
    parser.add_argument("--vc-scale", type=int, default=2, help="Nabu GOP console text scale (1..4)")
    args = parser.parse_args()

    summary = build_nabu_fastboot_boot_suite(out_dir=args.out_dir, vc_scale=args.vc_scale)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
