"""Exercise release reconstruction tools with synthetic inputs, no device I/O."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import shutil
import subprocess
import sys
import tarfile
import zipfile

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from nabu_root27 import WINDOWS, converted_text_fixups, export_volume, merge_plan, pack_root, sha, target_path


def module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    item = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(item)
    return item


def test_export_and_overlay_preserve_owners_modes_and_symlinks(tmp_path):
    source = tmp_path / 'read-only'
    (source / 'bin').mkdir(parents=True)
    (source / 'bin/program').write_bytes(b'original')
    (source / 'bin/program').chmod(0o4755)
    (source / 'alias').symlink_to('bin/program')
    plan = export_volume(source, tmp_path / 'export')
    assert plan['bsd_flags_pending']
    assert plan['symlinks'][0]['target'] == 'bin/program'
    replacement = tmp_path / 'replacement'
    replacement.write_bytes(b'converted')
    item = plan['files'][0]
    merged = merge_plan(plan, [[dict(path='/bin/program', source=str(replacement), uid=999, mode='600')]])
    assert merged['files'][0]['uid'] == item['uid']
    assert merged['files'][0]['mode'] == '4755'
    assert merged['files'][0]['sha256'] == sha(replacement)
    assert (source / 'bin/program').read_bytes() == b'original'
    with pytest.raises(ValueError, match='Unknown or duplicate'):
        merge_plan(plan, [[dict(path='/unknown', source=str(replacement))]])
    with pytest.raises(ValueError):
        export_volume(source, source / 'bad-export')


@pytest.mark.parametrize('path', ['relative', '/../escape', '/bad\nname', '/bad\tname', '/bad\\name'])
def test_invalid_volume_paths(path):
    with pytest.raises(ValueError):
        target_path(path)


def test_real_shngroot_layout_and_readback(tmp_path):
    windows, tables = {}, {}
    for index, name in enumerate(WINDOWS):
        windows[name] = tmp_path / (name + '.raw')
        windows[name].write_bytes(bytes([index + 1]) * (4096 + index * 512))
        tables[name] = tmp_path / (name + '.hashes')
        tables[name].write_bytes(hashlib.sha256(windows[name].read_bytes()).digest())
    output = tmp_path / 'root.img'
    report = pack_root(output, windows, tables)
    assert report['readback_verified'] and report['image_sha256'] == sha(output)
    with output.open('rb') as stream:
        header = stream.read(4096)
        assert struct.unpack_from('<8sIIQ', header) == (b'SHNGROOT', 2, 6, output.stat().st_size)
        assert header[4064:] == hashlib.sha256(header[:4064]).digest()
        for index, item in enumerate(report['windows']):
            name, offset, size, block, flags, digest, _ = struct.unpack_from('<32sQQII32s40s', header, 24 + 128 * index)
            assert name.rstrip(b'\0').decode() == item['name']
            assert offset % (1 << 20) == 0 and block == (512 if flags == 1 else 4096)
            stream.seek(offset)
            assert hashlib.sha256(stream.read(size)).digest() == digest
    with pytest.raises(ValueError):
        pack_root(output, windows, tables)


def encrypted_runtime(tmp_path, malicious=False):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    private = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    key = tmp_path / 'recipient.pem'
    key.write_bytes(private.private_bytes(serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    public = private.public_key().public_bytes(serialization.Encoding.PEM,
                                               serialization.PublicFormat.SubjectPublicKeyInfo)
    files = {'account.json': b'{"name":"test","password":"synthetic"}', 'data.dbr': b'fixture'}
    report = dict(build='26C5035r', new_seed=True,
                  files={name: dict(bytes=len(body), sha256=hashlib.sha256(body).hexdigest()) for name, body in files.items()})
    files['runtime-verification.json'] = json.dumps(report).encode()
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w') as bundle:
        for name, body in files.items():
            info = tarfile.TarInfo('../escape' if malicious and name == 'data.dbr' else name)
            info.size = len(body)
            bundle.addfile(info, io.BytesIO(body))
    secret, nonce = os.urandom(32), os.urandom(12)
    sealed = AESGCM(secret).encrypt(nonce, data.getvalue(), None)
    cipher = tmp_path / 'runtime.enc'
    cipher.write_bytes(sealed[:-16])
    envelope = dict(algorithm='RSA-OAEP-SHA256+AES256-GCM', build='26C5035r', nonce=nonce.hex(),
                    tag=sealed[-16:].hex(), ciphertext_sha256=sha(cipher),
                    recipient_sha256=hashlib.sha256(public).hexdigest(),
                    wrapped_key=private.public_key().encrypt(secret, padding.OAEP(
                        mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)).hex())
    path = tmp_path / 'envelope.json'
    path.write_text(json.dumps(envelope))
    return cipher, path, key


def test_runtime_authentication_private_permissions_and_no_secret_output(tmp_path, capsys):
    tool = module('unpack-fresh-runtime27')
    cipher, envelope, key = encrypted_runtime(tmp_path)
    output = tmp_path / 'decrypted'
    assert tool.unpack(cipher, envelope, key, output)['passed']
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert stat.S_IMODE((output / 'account.json').stat().st_mode) == 0o600
    assert 'synthetic' not in capsys.readouterr().out
    with pytest.raises(FileExistsError):
        tool.unpack(cipher, envelope, key, output)
    raw = bytearray(cipher.read_bytes())
    raw[0] ^= 1
    cipher.write_bytes(raw)
    with pytest.raises(ValueError, match='checksum'):
        tool.unpack(cipher, envelope, key, tmp_path / 'bad')
    assert not (tmp_path / 'bad').exists()


def test_runtime_rejects_unauthenticated_data_and_archive_escape(tmp_path):
    from cryptography.exceptions import InvalidTag
    tool = module('unpack-fresh-runtime27')
    cipher, envelope, key = encrypted_runtime(tmp_path, malicious=True)
    with pytest.raises(ValueError, match='Unsafe'):
        tool.unpack(cipher, envelope, key, tmp_path / 'bad')
    assert not (tmp_path / 'escape').exists() and not (tmp_path / 'bad').exists()
    record = json.loads(envelope.read_text())
    record['tag'] = '00' * 16
    envelope.write_text(json.dumps(record))
    with pytest.raises(InvalidTag):
        tool.unpack(cipher, envelope, key, tmp_path / 'bad-tag')
    assert not (tmp_path / 'bad-tag').exists()


def test_trust_merge_preserves_original_and_rewrite_attributes():
    tool = module('assemble-nabu-root27')
    original_module = struct.pack('<I16sI', 2, bytes(16), 1) + b'A' * 20 + bytes([2, 1, 3, 0])
    original = struct.pack('<II', 1, 8) + original_module
    result = tool.trust_bundle(original, [dict(cdhash=(b'B' * 20).hex(), hash_type=2, flags=1, constraint_category=3)])
    assert result[12:12 + len(original_module)] == original_module
    second = struct.unpack_from('<I', result, 8)[0]
    assert result[second + 24:] == b'B' * 20 + bytes([2, 1, 3, 0])
    with pytest.raises(ValueError):
        tool.trust_bundle(original[:-1], [])


def test_incomplete_conversion_is_not_a_prelink_report(tmp_path):
    tool = module('assemble-nabu-root27')
    with pytest.raises(ValueError, match='Independent'):
        tool.conversion_evidence({}, tmp_path, {}, dict(passed=False))


def test_conversion_requires_apfs_readback_of_all_rewritten_bytes(tmp_path):
    tool = module('assemble-nabu-root27')
    windows = {}
    rewritten = tmp_path / 'rewritten'
    rewritten.mkdir()
    (rewritten / 'cache').write_bytes(b'rewritten cache fixture')
    cache_hash = sha(rewritten / 'cache')
    attr = dict(cdhash='11' * 20, hash_type=2, flags=1, constraint_category=0)
    for name in WINDOWS:
        image = tmp_path / (name + '.raw')
        image.write_bytes(b'APFS container fixture ' + name.encode())
        windows[name] = image
        tree = dict(abi=tool.ABI, upstream_commit=tool.PIN, complete=True, failures=0,
                    inventory_errors=[], files=[dict(paths=['/program'], sha256='a' * 64,
                                                     trust_cache_entry=attr)])
        hashes = {'/program': 'a' * 64}
        if name == 'SystemOS':
            hashes['/System/Library/dyld/cache'] = cache_hash
        write = dict(complete=True, clean_unmount=True, container=str(image), container_sha256=sha(image),
                     file_hashes=hashes, files=len(hashes), verified_hashes=len(hashes))
        (tmp_path / (name + '-tree.json')).write_text(json.dumps(tree))
        (tmp_path / (name + '-write.json')).write_text(json.dumps(write))
    cache = dict(abi=tool.ABI, upstream_commit=tool.PIN, output=str(rewritten),
                 files=[dict(name='cache', sha256=cache_hash, trust_cache_entry=attr)])
    independent = dict(passed=True, all_code_pages_hashed=True, files=1)
    entries, evidence = tool.conversion_evidence(windows, tmp_path, cache, independent)
    assert len(entries) == 4 and set(evidence) == set(WINDOWS)
    windows['OS'].write_bytes(b'changed after APFS verification')
    with pytest.raises(ValueError, match='binding'):
        tool.conversion_evidence(windows, tmp_path, cache, independent)


def test_signed_text_preimage_follows_actual_pac_conversion(tmp_path):
    original, converted = tmp_path / 'original', tmp_path / 'converted'
    original.mkdir()
    converted.mkdir()
    (original / 'cache').write_bytes(bytes.fromhex('7f2303d5c0035fd6'))
    (converted / 'cache').write_bytes(bytes.fromhex('1f2003d5c0035fd6'))
    report = dict(cache=str(original / 'cache'), output=str(converted))
    result = converted_text_fixups(['cache,0,7f2303d5,000080d2'], report)
    assert result == ['cache,0,1f2003d5,000080d2']
    with pytest.raises(ValueError, match='original cache'):
        converted_text_fixups(['cache,0,ffffffff,000080d2'], report)


def test_nt36523_stock_header_sizes_and_bounds():
    tool = module('prepare-nabu-touch-firmware27')
    blob = bytearray(512)
    struct.pack_into('<6I', blob, 0, 0x50, 0x100, 15, 0x90, 0x200, 31)
    struct.pack_into('<II', blob, 0x18, 0x1234, 0x5678)
    struct.pack_into('<4I', blob, 0x30, 0x300, 7, 0xc0, 0x1111)
    struct.pack_into('<4I', blob, 0x40, 0x400, 3, 0xd0, 0x2222)
    parts, banks = tool.parse(bytes(blob))
    assert parts == [(0x100, 0x50, 16), (0x200, 0x90, 32), (0x300, 0xc0, 8), (0x400, 0xd0, 4)]
    assert banks == [0x100, 15, 0x1234, 0x200, 31, 0x5678]
    struct.pack_into('<I', blob, 8, 1000)
    with pytest.raises(ValueError, match='bounds'):
        tool.parse(bytes(blob))


def test_local_root_profile_only_overrides_qualified_fields(tmp_path, monkeypatch):
    sys.path.insert(0, str(SCRIPTS.parents[2] / 'scripts'))
    from unixnu.cli import parser
    from unixnu.config import Context
    from unixnu.util import Error
    root = SCRIPTS.parents[2]
    args = parser().parse_args(['status', '-D', str(root / 'devices/nabu'), '--os', 'macos-27'])
    monkeypatch.delenv('UNIXNU_ROOT_PROFILE', raising=False)
    baseline = Context(args)
    profile = tmp_path / 'root.local.toml'
    profile.write_text('[root]\n' + ''.join(
        f'{key} = "{value if key.endswith("_uuid") else "a" * 64}"\n'
        for key, value in baseline.board['root'].items()))
    monkeypatch.setenv('UNIXNU_ROOT_PROFILE', str(profile))
    assert Context(args).board['root']['os_image_sha256'] == 'a' * 64
    profile.write_text('[root]\nos_image_sha256 = "bad"\n')
    with pytest.raises(Error, match='all three'):
        Context(args)


def test_gpu_firmware_injection_rejects_wrong_bytes(tmp_path, monkeypatch):
    sys.path.insert(0, str(SCRIPTS.parents[2] / 'scripts'))
    from unixnu import devicetree
    from unixnu.util import Error
    data = b'synthetic GPU firmware'
    (tmp_path / 'fixture.bin').write_bytes(data)
    monkeypatch.setattr(devicetree, 'NABU_GPU_FIRMWARE', {
        'gmu-firmware': ('fixture.bin', len(data), hashlib.sha256(data).hexdigest())})
    assert devicetree.nabu_gpu_firmware(tmp_path) == [('gmu-firmware', data)]
    (tmp_path / 'fixture.bin').write_bytes(bytes(len(data)))
    with pytest.raises(Error, match='Unqualified'):
        devicetree.nabu_gpu_firmware(tmp_path)


def test_relocation_rehashes_image_without_changing_conversion_evidence(tmp_path):
    tool = module('relocate-nabu-root-report27')
    image = tmp_path / 'moved.img'
    image.write_bytes(b'converted fixture')
    report = dict(complete=True, abi='zeta-ordinary-v1', root_image='/old/host/root.img',
                  root_image_sha256=sha(image), conversion={'OS': 'evidence'})
    rebound = tool.relocate(report, image)
    assert rebound['conversion'] == report['conversion']
    assert rebound['root_image'] == str(image.resolve())
    image.write_bytes(b'changed')
    with pytest.raises(ValueError, match='differs'):
        tool.relocate(report, image)


def test_real_fat_zip_and_payload_pairing(tmp_path):
    if not shutil.which('mcopy') or not shutil.which('mkfs.vfat'):
        pytest.skip('FAT regression requires mtools/dosfstools')
    import nabu_boot_config27 as menu
    esp = tmp_path / 'esp'
    menu.install(esp, without_persistent_log=True)
    pe = bytearray(512)
    pe[:2] = b'MZ'
    struct.pack_into('<I', pe, 60, 128)
    pe[128:132] = b'PE\0\0'
    struct.pack_into('<H', pe, 132, 0xaa64)
    for name in ['EFI/BOOT/BOOTAA64.EFI', 'EFI/UniXNU/UniXNU.efi']:
        path = esp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pe)
    for name in ['kernel.macho', 'kernel.macho.adt', 'kernel.macho.trustcache',
                 'kernel.macho.gpuaddon', 'kernel.macho.vroot', 'scratch-data.img']:
        path = esp / 'EFI/UniXNU/Payloads' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'explicit packaging fixture:' + name.encode())
    image = tmp_path / 'root.img'
    image.write_bytes(b'explicit converted ROOT fixture')
    root = dict(complete=True, abi='zeta-ordinary-v1', root_image=str(image), root_image_sha256=sha(image))
    prelink = dict(zeta_runtime=root, candidate_sha256=hashlib.sha256(b'prelink fixture').hexdigest())
    payload = dict(kernel_sha256=prelink['candidate_sha256'],
                   payload_kernel_sha256=sha(esp / 'EFI/UniXNU/Payloads/kernel.macho'))
    for name, report in [('root', root), ('prelink', prelink), ('payload', payload)]:
        (tmp_path / (name + '.json')).write_text(json.dumps(report))
    archive = tmp_path / 'esp.zip'
    command = [sys.executable, str(SCRIPTS / 'pack-nabu-esp27.py'), '--esp', str(esp),
               '--prelink-report', str(tmp_path / 'prelink.json'),
               '--payload-report', str(tmp_path / 'payload.json'),
               '--root-report', str(tmp_path / 'root.json'), '--output', str(archive)]
    subprocess.run(command, check=True, capture_output=True, text=True)
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.testzip() is None
        result = json.loads(bundle.read('verification.json'))
        assert result['fat_readback']['passed'] and not result['hardware_tested']
        for name, record in result['efi_files'].items():
            assert hashlib.sha256(bundle.read(name)).hexdigest() == record['sha256']
    archive.unlink()
    (esp / 'EFI/UniXNU/Payloads/kernel.macho').write_bytes(b'different kernel fixture')
    run = subprocess.run(command, capture_output=True, text=True)
    assert run.returncode and not archive.exists()
    assert 'Actual payload kernel' in run.stderr


def test_original_apfs_offset_checks_gpt_crc_and_partition(tmp_path):
    import uuid
    import zlib
    tool = module('prepare-nabu-inputs27')
    image = tmp_path / 'original.raw'
    raw = bytearray(16384)
    table = bytearray(128)
    table[:16] = uuid.UUID('7C3457EF-0000-11AA-AA11-00306543ECAC').bytes_le
    struct.pack_into('<Q', table, 32, 8)
    header = bytearray(512)
    header[:8] = b'EFI PART'
    struct.pack_into('<I', header, 12, 92)
    struct.pack_into('<QIII', header, 72, 2, 1, 128, zlib.crc32(table) & 0xffffffff)
    struct.pack_into('<I', header, 16, zlib.crc32(header[:92]) & 0xffffffff)
    raw[512:1024] = header
    raw[1024:1152] = table
    raw[4096 + 32:4096 + 36] = b'NXSB'
    image.write_bytes(raw)
    assert tool.apfs_offset(image) == 4096
    raw[512 + 32] ^= 1
    image.write_bytes(raw)
    with pytest.raises(ValueError, match='checksum'):
        tool.apfs_offset(image)
