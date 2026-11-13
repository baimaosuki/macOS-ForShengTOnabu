"""Real signed-byte fixtures and immutable-member repack failure tests."""
import hashlib
import json
from pathlib import Path
import plistlib
import struct
import subprocess
import sys
import uuid
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import repack_nabu_probe as repack
from prepare_nabu_boot_addon import prepare


def signed(entitlements, marker):
    """Executable with real SHA256 pages, XML/DER special slots and primary CD."""
    limit = 4096
    raw = bytearray(limit)
    ident = b'org.nabu.apfs-boot-layout\0'
    xml = plistlib.dumps(entitlements)
    blobs = {5:struct.pack('>II', 0xfade7171, 8 + len(xml)) + xml,
             7:struct.pack('>II', 0xfade7172, 10) + b'\x30\x00'}
    special = 7
    offset = 48 + len(ident) + special * 32
    cdlen = offset + 32
    signature_size = 12 + 3 * 8 + cdlen + sum(map(len, blobs.values()))
    struct.pack_into('<8I', raw, 0, 0xfeedfacf, 0x100000c, 0, 2, 1, 16, 0, 0)
    struct.pack_into('<4I', raw, 32, 0x1d, 16, limit, signature_size)
    raw[100:100 + len(marker)] = marker
    slots = b''.join(hashlib.sha256(blobs[n]).digest() if n in blobs else bytes(32)
                     for n in range(special, 0, -1))
    cd = (struct.pack('>9I4BII', 0xfade0c02, cdlen, 0x20100, 2, offset, 48,
          special, 1, limit, 32, 2, 0, 12, 0, 0) + ident + slots + hashlib.sha256(raw).digest())
    all_blobs = {0:cd, **blobs}
    at = 12 + len(all_blobs) * 8
    index = b''
    for n, b in all_blobs.items():
        index += struct.pack('>II', n, at)
        at += len(b)
    return bytes(raw) + struct.pack('>III', 0xfade0cc0, at, len(all_blobs)) + index + b''.join(all_blobs.values())


def write_json(path, record):
    path.write_text(json.dumps(record))


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    entitlements = {k:True for k in ('com.apple.private.bindfs-allow',
        'com.apple.system-task-ports.read', 'com.apple.system-task-ports.read.safe')}
    for key, name in repack.SOURCE_HASHES.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(plistlib.dumps(entitlements) if key == 'entitlements_sha256'
                         else ('fixture-' + key).encode())
    def git(*args):
        return subprocess.check_output(['git', '-C', str(source), *args], text=True).strip()
    git('init', '-q')
    git('add', '.')
    git('-c', 'user.name=test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'base')
    base_head = git('rev-parse', 'HEAD')
    marker_source = source / repack.SOURCE_HASHES['source_sha256']
    marker_source.write_text('new fixture source')
    git('add', '.')
    git('-c', 'user.name=test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'probe')
    shim_head = git('rev-parse', 'HEAD')
    base_meta, shim_meta = tmp_path / 'base-ci.json', tmp_path / 'shim-ci.json'
    for path, run, head in [(base_meta, repack.BASE_RUN, base_head), (shim_meta, 999, shim_head)]:
        write_json(path, dict(id=run,status='completed',conclusion='success',head_sha=head))
    modules = [struct.pack('<I16sI',2,uuid.UUID(int=n).bytes,1) + bytes([n])*20 + bytes((2,0,0,0))
               for n in [1,2,3]]
    original = tmp_path / 'original.trustcache'
    original.write_bytes(struct.pack('<4I',3,16,64,112)+b''.join(modules))
    old = tmp_path / 'old'; old.mkdir()
    old_raw = signed(entitlements,b'old-probe')
    (old / 'apfs_boot_util').write_bytes(old_raw)
    write_json(old / 'build.json', dict(sha256=repack.sha(old_raw)))
    prepare(old,original,tmp_path / 'old-addon')
    shim = tmp_path / 'new-shim'; shim.mkdir()
    new_raw = signed(entitlements,b'new-raw-probe ' + b' '.join(repack.BINARY_MARKERS))
    (shim / 'apfs_boot_util').write_bytes(new_raw)
    signature = repack.verify_shim_signature(new_raw,plistlib.dumps(entitlements))
    build = dict(sha256=repack.sha(new_raw),architecture='arm64',target='arm64-apple-macos26.5',
                 identifier='org.nabu.apfs-boot-layout',probe_policy=dict(repack.PROBE_POLICY),cdhash=signature['code_directories'][0]['cdhash'])
    build.update({k:repack.sha((source / n).read_bytes())for k,n in repack.SOURCE_HASHES.items()})
    write_json(shim / 'build.json',build)
    members = {repack.PAYLOAD+'kernel.macho':b'unchanged kernel',
               repack.PAYLOAD+'kernel.macho.adt':b'unchanged 8cpu adt',
               repack.TOML:b'unchanged config', 'README.txt':b'old default sample readme',
               'tools/retained-reader.py':b'unchanged helper'}
    for n in ['kernel.macho.gpuaddon','kernel.macho.trustcache']:
        members[repack.PAYLOAD+n]=(tmp_path / 'old-addon' / n).read_bytes()
    delivery=dict(ci_run=repack.BASE_RUN,commit=base_head,
      payload_sha256={n.removeprefix(repack.PAYLOAD):repack.sha(v)for n,v in members.items()if n.startswith(repack.PAYLOAD)},
      kernel_sha256=repack.sha(members[repack.PAYLOAD+'kernel.macho']),kernel_bytes=len(members[repack.PAYLOAD+'kernel.macho']),toml_sha256=repack.sha(members[repack.TOML]))
    members['delivery.json']=json.dumps(delivery).encode()
    base_zip=tmp_path/'base.zip'
    def save():
        with zipfile.ZipFile(base_zip,'w',compression=zipfile.ZIP_DEFLATED)as z:
            for n,b in members.items():z.writestr(n,b)
        digest=repack.sha(base_zip.read_bytes());monkeypatch.setattr(repack,'BASE_SHA256',digest)
        return digest
    digest=save()
    output=tmp_path/'result'
    def run():
        return repack.repack(base_zip,repack.BASE_SHA256,base_meta,shim,shim_meta,original,source,output)
    return locals()


def test_repack_preserves_every_other_member_and_two_provenances(fixture):
    f=fixture
    original=f['base_zip'].read_bytes()
    manifest=f['run']()
    assert f['base_zip'].read_bytes()==original
    assert manifest['kernel_provenance']['ci']['head_sha']==f['base_head']
    assert manifest['shim_provenance']['ci']['head_sha']==f['shim_head']
    assert manifest['desktop_fix'] is False
    with zipfile.ZipFile(f['output']/manifest['archive'])as z:
        for n,b in f['members'].items():
            if n not in [repack.ADDON,repack.TRUST,'README.txt','delivery.json']:
                assert z.read(n)==b
        assert z.read(repack.ADDON)!=f['members'][repack.ADDON]
        assert repack.modules(z.read(repack.TRUST))[:3]==repack.modules(f['original'].read_bytes())
        delivery=json.loads(z.read('delivery.json'))
        assert delivery['base_delivery']==f['delivery']
        assert 'raw stacks only' in z.read('README.txt').decode()
    assert (f['output']/'kernel-ci-metadata.json').read_bytes()==f['base_meta'].read_bytes()
    with pytest.raises(ValueError,match='already exists'):f['run']()


@pytest.mark.parametrize('which', ['zip','delivery','ci','source','build','page','entitlements','trust','data','policy'])
def test_repack_rejects_untrusted_inputs_without_output(fixture,which):
    f=fixture
    if which=='zip':
        f['base_zip'].write_bytes(f['base_zip'].read_bytes()+b'tamper')
    elif which=='delivery':
        d=dict(f['delivery']);d['toml_sha256']='0'*64
        f['members']['delivery.json']=json.dumps(d).encode();f['save']()
    elif which=='ci':
        d=json.loads(f['shim_meta'].read_text());d['conclusion']='failure';write_json(f['shim_meta'],d)
    elif which=='source':
        path=f['source']/repack.SOURCE_HASHES['raw_stacks_header_sha256'];path.write_text('dirty')
        build=dict(f['build']);build['raw_stacks_header_sha256']=repack.sha(path.read_bytes())
        write_json(f['shim']/'build.json',build)
    elif which=='policy':
        build=dict(f['build']);build['probe_policy']=dict(repack.PROBE_POLICY,default='symbolic')
        write_json(f['shim']/'build.json',build)
    elif which=='build':
        build=dict(f['build']);build.pop('build_script_sha256');write_json(f['shim']/'build.json',build)
    elif which in ['page','entitlements']:
        raw=bytearray(f['new_raw']);raw[110 if which=='page'else -1]^=1
        (f['shim']/'apfs_boot_util').write_bytes(raw)
        build=dict(f['build']);build['sha256']=repack.sha(raw);write_json(f['shim']/'build.json',build)
    elif which=='trust':
        raw=bytearray(f['original'].read_bytes());raw[-2]^=1;f['original'].write_bytes(raw)
    elif which=='data':
        f['members']['data.dbr']=b'forbidden';f['save']()
    with pytest.raises((ValueError,subprocess.CalledProcessError)):
        f['run']()
    assert not f['output'].exists()


def test_signature_rejects_fake_page_count_even_with_valid_cdhash(fixture):
    raw=bytearray(fixture['new_raw'])
    # nCodeSlots=0 would fool a verifier that only loops existing slots.
    cd_offset=4096+36
    struct.pack_into('>I',raw,cd_offset+28,0)
    with pytest.raises(ValueError,match='every executable page'):
        repack.verify_shim_signature(raw,plistlib.dumps(fixture['entitlements']))
