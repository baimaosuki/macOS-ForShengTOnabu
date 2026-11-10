#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Publish the native EL1 text-protection state in generated Apple DTs."""
from pathlib import Path
import struct
import uuid
import hashlib
import zlib


def seed_vm_nvram_uuid(image: bytes, identity: uuid.UUID) -> bytes:
    # Modify only an empty CHRP proxy created by this tool; reject existing firmware variables.
    if (len(image) != 8192 or image[4:16] != b'nvram' + bytes(7)
            or image[36:48] != b'common' + bytes(6)
            or struct.unpack_from('<H', image, 34)[0] * 16 != 2064
            or struct.unpack_from('<I', image, 16)[0] != zlib.adler32(image[20:])):
        raise ValueError('requires the generated volatile CHRP bank')
    encoded = bytearray()
    for value in identity.bytes:
        encoded.extend((255, 1 if value == 0 else 129) if value in (0, 255) else (value,))
    record = b'platform-uuid=' + encoded + b'\0'
    existing = image[48:2096]
    expected = record + bytes(2048 - len(record))
    if existing != bytes(2048) and existing != expected:
        raise ValueError('refusing to replace existing NVRAM variables or VM identity')
    result = bytearray(image)
    result[48:2096] = expected
    struct.pack_into('<I', result, 16, zlib.adler32(result[20:]))
    return bytes(result)


def configure_native_text_state(path: Path, *, development_root: bool = False,
                                root_hash: bytes | None = None,
                                boot_volume_uuid: uuid.UUID | None = None,
                                platform_uuid: uuid.UUID | None = None,
                                qemu_disable_sleep: bool = False,
                                native_gop_display: bool = False,
                                qemu_virtio_input: bool = False) -> None:
    data = path.read_bytes()
    chosen = asmb = auth = options = proxies = 0
    arm_io = gop_nodes = 0
    defaults = 0
    input_nodes = 0
    if qemu_disable_sleep and not development_root:
        raise ValueError('sleep policy is restricted to the development fixture')
    if qemu_virtio_input and not development_root:
        raise ValueError('VirtIO input is restricted to the development fixture')
    if root_hash is not None and not 16 <= len(root_hash) <= 16384:
        raise ValueError('invalid raw root-hash payload size')

    if boot_volume_uuid is not None and root_hash is None:
        raise ValueError('boot volume identity requires the paired recovery root hash')

    if platform_uuid is not None and platform_uuid.int == 0:
        raise ValueError('VM platform UUID cannot be nil')
    if platform_uuid is not None and not development_root:
        raise ValueError('synthetic platform UUID is restricted to the development fixture')

    def node(at: int, parents: tuple[str, ...] = ()) -> tuple[bytes, int]:
        nonlocal chosen, asmb, auth, options, proxies, arm_io, gop_nodes, defaults, input_nodes
        if len(parents) > 32 or at + 8 > len(data):
            raise ValueError('invalid generated device-tree node')
        count, children = struct.unpack_from('<II', data, at)
        at += 8
        if count > 4096 or children > 4096:
            raise ValueError('excessive generated device-tree counts')
        properties = {}
        encoded = bytearray()
        for _ in range(count):
            if at + 36 > len(data):
                raise ValueError('truncated device-tree property')
            key, size = struct.unpack_from('<32sI', data, at)
            key = key.split(b'\0', 1)[0].decode('ascii')
            at += 36
            end = at + ((size + 3) & ~3)
            if end > len(data) or key in properties:
                raise ValueError('invalid or repeated device-tree property')
            value = data[at:at + size]
            if platform_uuid is not None and parents == ('device-tree',) and key == 'nvram-proxy-data':
                value = seed_vm_nvram_uuid(value, platform_uuid)
                proxies += 1
            properties[key] = value
            encoded.extend(struct.pack('<32sI', key.encode(), size) + value + bytes(-size % 4))
            at = end
        name = properties.get('name', b'').rstrip(b'\0').decode('ascii')
        location = (*parents, name)
        def ensure(key: str, value: bytes) -> None:
            nonlocal count
            if key in properties:
                if properties[key] != value:
                    raise ValueError(f'generated tree conflicts with native policy: {key}')
            else:
                encoded.extend(struct.pack('<32sI', key.encode(), len(value)) +
                               value + bytes(-len(value) % 4))
                count += 1

        if qemu_virtio_input and location == ('device-tree',):
            if properties.get('model') != b'qemu-entry-fixture\0':
                raise ValueError('VirtIO input requires the owned QEMU tree')
            ensure('#address-cells', struct.pack('<I', 2))
            ensure('#size-cells', struct.pack('<I', 2))
        if qemu_virtio_input and location == ('device-tree', 'native-recovery-input'):
            input_nodes += 1
            ensure('compatible', b'sheng,qemu-virtio-input\0')
            ensure('reg', struct.pack('<QQ', 0x4010000000, 0x100000))

        if platform_uuid is not None and location == ('device-tree', 'options'):
            options += 1
            ensure('platform-uuid', platform_uuid.bytes)
        if qemu_disable_sleep and location == ('device-tree', 'defaults'):
            defaults += 1
            # IOPMrootDomain reads this policy before accepting idle or demand sleep.
            ensure('sleep-disabled', struct.pack('<I', 1))
        is_chosen = location == ('device-tree', 'chosen')
        if native_gop_display and location == ('device-tree', 'arm-io'):
            arm_io += 1
        if native_gop_display and location == ('device-tree', 'arm-io', 'native-gop'):
            gop_nodes += 1
            # The strict nabu ADT contract forbids the sheng vendor prefix, so the
            # display node is claimed as nabu,gop-framebuffer; older fixtures that
            # still carry the sheng prefix are accepted unchanged.
            ensure('device_type', b'display-subsystem\0')
            if 'compatible' not in properties:
                ensure('compatible', b'nabu,gop-framebuffer\0')
            elif properties['compatible'] not in (b'nabu,gop-framebuffer\0',
                                                  b'sheng,gop-framebuffer\0'):
                raise ValueError('generated tree conflicts with native policy: compatible')
        if root_hash is not None and location == ('device-tree', 'chosen', 'secure-boot-hashes'):
            auth += 1
            ensure('system-volume-auth-blob', root_hash)
        if is_chosen:
            chosen += 1
            ensure('kernel-ctrr-to-be-enabled', struct.pack('<I', 0))
            if root_hash is not None:
                ensure('osenvironment', b'recoveryos\0')
            if boot_volume_uuid is not None:
                identity = str(boot_volume_uuid).upper().encode('ascii') + b'\0'
                ensure('boot-uuid', identity)
                ensure('apfs-preboot-uuid', identity)
        if development_root and location == ('device-tree', 'chosen', 'asmb'):
            asmb += 1
            ensure('lp-sip0', struct.pack('<Q', 0))
            ensure('lp-sip1', struct.pack('<I', 1))
        descendants = bytearray()
        for _ in range(children):
            child, at = node(at, location)
            descendants.extend(child)
        if qemu_virtio_input and location == ('device-tree',) and not input_nodes:
            # Bus 0 occupies the first MiB of QEMU virt's default high ECAM window.
            entries = (('name', b'native-recovery-input\0'),
                       ('compatible', b'sheng,qemu-virtio-input\0'),
                       ('reg', struct.pack('<QQ', 0x4010000000, 0x100000)))
            descendants.extend(struct.pack('<II', len(entries), 0))
            for key, value in entries:
                descendants.extend(struct.pack('<32sI', key.encode(), len(value)) +
                                   value + bytes(-len(value) % 4))
            children += 1
            input_nodes += 1
        if qemu_disable_sleep and location == ('device-tree',) and not defaults:
            entries = (('name', b'defaults\0'), ('sleep-disabled', struct.pack('<I', 1)))
            descendants.extend(struct.pack('<II', len(entries), 0))
            for key, value in entries:
                descendants.extend(struct.pack('<32sI', key.encode(), len(value)) +
                                   value + bytes(-len(value) % 4))
            children += 1
            defaults += 1
        if native_gop_display and location == ('device-tree', 'arm-io') and not gop_nodes:
            # The original framework counts display-subsystem descriptors before matching services.
            entries = (('name', b'native-gop\0'), ('device_type', b'display-subsystem\0'),
                       ('compatible', b'sheng,gop-framebuffer\0'))
            descendants.extend(struct.pack('<II', len(entries), 0))
            for key, value in entries:
                descendants.extend(struct.pack('<32sI', key.encode(), len(value)) +
                                   value + bytes(-len(value) % 4))
            children += 1
            gop_nodes += 1
        if development_root and is_chosen and asmb == 0:
            entries = (('name', b'asmb\0'), ('lp-sip0', struct.pack('<Q', 0)),
                       ('lp-sip1', struct.pack('<I', 1)))
            descendants.extend(struct.pack('<II', len(entries), 0))
            for key, value in entries:
                descendants.extend(struct.pack('<32sI', key.encode(), len(value)) +
                                   value + bytes(-len(value) % 4))
            children += 1
            asmb += 1
        if root_hash is not None and is_chosen and auth == 0:
            entries = (('name', b'secure-boot-hashes\0'), ('system-volume-auth-blob', root_hash))
            descendants.extend(struct.pack('<II', len(entries), 0))
            for key, value in entries:
                descendants.extend(struct.pack('<32sI', key.encode(), len(value)) +
                                   value + bytes(-len(value) % 4))
            children += 1
            auth += 1
        return bytes(struct.pack('<II', count, children) + encoded + descendants), at

    result, end = node(0)
    if (end != len(data) or chosen != 1 or development_root and asmb != 1
            or root_hash is not None and auth != 1
            or platform_uuid is not None and (options != 1 or proxies != 1)
            or qemu_disable_sleep and defaults != 1
            or qemu_virtio_input and input_nodes != 1
            or native_gop_display and (arm_io != 1 or gop_nodes != 1)):
        raise ValueError('requires one complete generated /chosen node')
    path.write_bytes(result)


def _adt_node(name: str, properties: tuple[tuple[str, bytes], ...] = (),
              children: tuple[bytes, ...] = ()) -> bytes:
    props = (('name', name.encode('ascii') + b'\0'), *properties)
    encoded = bytearray(struct.pack('<II', len(props), len(children)))
    for key, value in props:
        encoded.extend(struct.pack('<32sI', key.encode('ascii'), len(value)) +
                       value + bytes(-len(value) % 4))
    for child in children:
        encoded.extend(child)
    return bytes(encoded)


def _adt_random_seed() -> bytes:
    """256 non-zero bytes for /chosen/random-seed.

    XNU's arm64 KPRNG asks the bootloader for 4 * SHA512_DIGEST_LENGTH bytes and
    treats a short read as fatal ("Expected 256 seed bytes from bootloader, but
    got N" in prng_random.c), so the property has to carry a full 256 bytes.
    Derived deterministically so the ADT stays reproducible byte for byte.
    """
    out = bytearray()
    counter = 0
    while len(out) < 256:
        out += hashlib.sha512(b'nabu-adt-random-seed-v1' + counter.to_bytes(4, 'little')).digest()
        counter += 1
    return bytes(out[:256])


def _adt_nvram_props() -> list[tuple[str, bytes]]:
    """CHRP NVRAM bank: 8 KiB with 'nvram'/'common'/'free' headers and Adler-32."""
    image = bytearray(8192)
    image[20:52] = hashlib.sha256(b'nabu-adt-nvram').digest()

    def header(offset: int, name: bytes, length: int) -> None:
        struct.pack_into('<BBH12s', image, offset, 0x70, 0, length // 16,
                         name + bytes(12 - len(name)))

    header(0, b'nvram', 32)
    header(32, b'common', 16 + 2048)
    header(2096, b'free', 8192 - 2096)
    for offset in (0, 32, 2096):
        block = bytearray(image[offset:offset + 16])
        block[1] = 0
        total = sum(block)
        while total > 255:
            total = (total & 0xFF) + (total >> 8)
        image[offset + 1] = total & 0xFF
    struct.pack_into('<I', image, 16, zlib.adler32(bytes(image[20:])))
    return [
        ('nvram-bank-size', struct.pack('<I', 8192)),
        ('nvram-bank-count', struct.pack('<I', 1)),
        ('nvram-current-bank', struct.pack('<I', 0)),
        ('nvram-proxy-data', bytes(image)),
    ]

def make_nabu_adt(path: Path, *, dram_base: int = 0xc0000000,
                  dram_size: int = 0x140000000,
                  owned_memory_size: int = 0x40000000,
                  os_environment: str | None = None,
                  panic_log: bool = True,
                  native_gop_display: bool = False,
                  native_ufs: bool = False,
                  packed_ramdisk: bool = False) -> None:
    """Emit the Xiaomi Pad 5 (nabu / SM8150) Apple DeviceTree (.adt)."""
    # Nabu has 3 physical DRAM banks separated by the TrustZone hole 0xb9900000..0xc0000000.
    # XNU owns the upper contiguous 5 GiB window [0xc0000000, 0x200000000), while stock UEFI GOP
    # scanout remains in Bank 0 at 0x9c000000 (1600x2560, stride 6400).
    if dram_base < 0xc0000000 or dram_base + dram_size > 0x200000000:
        raise ValueError('nabu XNU DRAM window must avoid the 0xb9900000..0xc0000000 TrustZone hole')
    if not 0 < owned_memory_size <= dram_size:
        raise ValueError('nabu darwinboot-memory-size must lie within the XNU DRAM window')
    chosen_props = [
        ('dram-base', struct.pack('<Q', dram_base)),
        ('dram-size', struct.pack('<Q', dram_size)),
        ('darwinboot-memory-size', struct.pack('<Q', owned_memory_size)),
        ('nabu-dram-banks', struct.pack('<6Q',
            0x80000000, 0x3cc00000,
            0xc0000000, 0x80000000,
            0x140000000, 0xc0000000)),
        ('trustzone-hole', struct.pack('<QQ', 0xb9900000, 0x06700000)),
        ('framebuffer-base', struct.pack('<Q', 0x9c000000)),
        ('framebuffer-size', struct.pack('<Q', 0x01000000)),
        ('framebuffer-width', struct.pack('<I', 1600)),
        ('framebuffer-height', struct.pack('<I', 2560)),
        ('framebuffer-depth', struct.pack('<I', 32)),
        ('framebuffer-stride', struct.pack('<I', 6400)),
        ('kernel-ctrr-to-be-enabled', struct.pack('<I', 0)),
        # AppleImage4 reads this during start() and panics with
        # "non-sensical crypto hash method: " when the property is absent; the
        # board device tree of every supported target names sha2-384, and
        # sheng ships the same value.
        ('crypto-hash-method', b'sha2-384\0'),
    ]
    chosen_props.append(('osenvironment', (os_environment or 'main').encode('ascii') + b'\0'))
    chosen_props.append(('random-seed', _adt_random_seed()))
    chosen_props.extend(_adt_nvram_props())
    if panic_log:
        chosen_props.append(('embedded-panic-log-size', struct.pack('<I', 0x80000)))
    # Controlled first boot: one core only. ArmDTPlatform walks /cpus children
    # with device_type == "cpu"; the strict ADT contract rejects secondaries.
    cpus = [_adt_node('cpu@0', (
        ('device_type', b'cpu\0'),
        ('compatible', b'arm,cortex-a55\0'),
        ('reg', struct.pack('<I', 0)),
        ('cpu-id', struct.pack('<I', 0)),
        ('enable-method', b'psci\0'),
        # pe_identify_machine reads the boot state and the timebase; a missing
        # state string leaves the cpu unusable and a zero rate divides by zero.
        ('state', b'running\0'),
        ('timebase-frequency', struct.pack('<I', 19200000)),
        # Without bus-frequency the kernel keeps bus_frequency_hz at 0 (bzero)
        # and copies it into bus_clock_rate_hz; L154 then divides by zero.
        ('bus-frequency', struct.pack('<Q', 100000000)),
        ('clock-frequency', struct.pack('<Q', 2840000000)),
    ))]
    # XNU ArmDTPlatform requires interrupt-controller = "master\0" and name = "interrupt-controller\0"
    intc = _adt_node('intc', (
        ('compatible', b'arm,gic-v3\0'),
        ('interrupt-controller', b'master\0'),
        ('#interrupt-cells', struct.pack('<I', 4)),
        # Child offsets inside the arm-io bus: ranges maps 0x0 -> 0x17000000,
        # so GICD/GICR land at 0x17a00000 / 0x17a60000 on the parent bus.
        ('reg', struct.pack('<4Q', 0x00a00000, 0x10000, 0x00a60000, 0x100000)),
        ('redistributor-stride', struct.pack('<Q', 0x20000)),
        ('virtual-timer-intid', struct.pack('<I', 19)),
        ('ipi-transport', b'sgi-sysreg\0'),
    ))
    ufs = _adt_node('ufs', (
        ('compatible', b'qcom,sm8150-ufshc\0'),
        ('reg', struct.pack('<QQ', 0x01d84000, 0x2500)),
        ('nabu,ufs-hc', struct.pack('<QQ', 0x01d84000, 0x2500)),
        ('nabu,ufs-gcc', struct.pack('<QQ', 0x00177000, 0x1000)),
        ('nabu,ufs-smmu', struct.pack('<QQ', 0x15000000, 0x100000)),
        ('nabu,ufs-stream', struct.pack('<I', 0x300)),
        ('nabu,ufs-block-bytes', struct.pack('<I', 4096)),
    ))
    ufs_smmu = _adt_node('ufs-smmu', (
        ('compatible', b'qcom,sm8150-smmu\0arm,smmu-v2\0'),
        ('reg', struct.pack('<QQ', 0x15000000, 0x100000)),
    ))
    zap = _adt_node('zap-shader', (
        ('reg', struct.pack('<QQ', 0x98f15000, 0x2000)),
    ))
    # First boot stays off the storage and GPU blocks: the strict ADT contract
    # forbids ufs/gpu/adreno/kgsl nodes until their drivers are qualified.
    arm_io_children = [intc]
    if native_ufs:
        arm_io_children.extend([ufs, ufs_smmu])
    if native_gop_display:
        arm_io_children.append(_adt_node('native-gop', (
            ('device_type', b'display-subsystem\0'),
            ('compatible', b'nabu,gop-framebuffer\0'),
            ('reg', struct.pack('<QQ', 0x9c000000, 0x01000000)),
            ('width', struct.pack('<I', 1600)),
            ('height', struct.pack('<I', 2560)),
            ('stride', struct.pack('<I', 6400)),
        )))
    arm_io = _adt_node('arm-io', (
        # pe_arm_get_soc_base_phys reads device_type first and hands it to
        # strlcpy without a null check, so the property must exist.
        ('device_type', b'arm-io\0'),
        ('compatible', b'simple-bus\0'),
        ('#address-cells', struct.pack('<I', 2)),
        ('#size-cells', struct.pack('<I', 2)),
        ('ranges', struct.pack('<3Q', 0x0, 0x17000000, 0x08000000)),
    ), tuple(arm_io_children))
    tree = _adt_node('device-tree', (
        ('model', b'Xiaomi Pad 5\0'),
        ('compatible', b'xiaomi,nabu\0qcom,sm8150\0'),
        ('#address-cells', struct.pack('<I', 2)),
        ('#size-cells', struct.pack('<I', 2)),
    ), (
        # The empty manifest-properties child is what the original AppleImage4
        # looks up first; without it start() panics with "failed to get manifest
        # properties" before it ever reaches the crypto hash method check.
        _adt_node('chosen', tuple(chosen_props),
                  (_adt_node('memory-map', (('PackedRAMDisk', bytes(16)),) if packed_ramdisk else ()),
                   _adt_node('manifest-properties'))),
        _adt_node('defaults', (('model', b'Xiaomi Pad 5\0'), ('target-type', b'Mac\0'))),
        _adt_node('options'),
        _adt_node('product', (
            ('model', b'xiaomi,nabu\0'),
            ('soc-name', b'sm8150\0'),
        )),
        _adt_node('psci', (
            ('compatible', b'arm,psci-1.0\0'),
            ('method', b'smc\0'),
        )),
        _adt_node('cpus', (
            ('#address-cells', struct.pack('<I', 2)),
            ('#size-cells', struct.pack('<I', 0)),
            ('timebase-frequency', struct.pack('<I', 19200000)),
        ), tuple(cpus)),
        arm_io,
    ))
    path.write_bytes(tree)
