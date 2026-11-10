#!/usr/bin/env python3
"""Build a disposable 25F71 RAM Data seed on macOS using pinned mainline tools."""
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import secrets
import shutil
import struct
import subprocess
import sys
import tarfile
import urllib.request
import zlib

ROOT = Path(__file__).resolve().parents[1]
PIN = '95e9feeb5dba4ddd8d47b897381550b5fa802567'
DATA_UUID = '0A5EED00-0000-4000-8000-000000000001'
MEMBER = '094-56453-087.dmg.aea'
AEA_SHA = '512a9edb8027d0e10cee83d7c461c0ac2e5a6cc820ce0ea93e5a384a68842f43'
RAW_SHA = '402ec952199815a4c293112d4d5c64729d91d9c7616ac9a5668d1ce1823b3e6f'
SOURCES = {
    'scripts/make-macos-data-seed.py': ('UniXNU', PIN, '58e21230057294bc851bb3fb7f712072e76f2ecaf7fe88281d56b0d1b4f982e9'),
    'scripts/seed-data-grow.py': ('UniXNU', PIN, '2c4b6da3f87fc4e148f0eb9321372bab42149b56eae0c4b1f405a900d81f1e44'),
    'drivers/DarwinBoot/Test/packed_ramdisk.py': ('DarwinBoot', '3c75734014cdab0acd9541e88057b7606ee7ae08', 'efb5fac21ae94dcb1aecd47da2b73a7112b487e43692ac6396858552d27a0e48'),
}


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fetch_os(output):
    import fetch_ipsw_entry as reader
    url = json.loads((ROOT / 'config/macos26.json').read_text())['ipsw']['url']
    entries, _ = reader.central_directory(url, reader.total_size(url))
    matches = [e for e in entries if e['name'] == MEMBER]
    if len(matches) != 1 or matches[0]['method'] != 0:
        raise ValueError('expected exactly the paired stored OS member')
    entry = matches[0]
    header = reader.fetch(url, entry['offset'], 30)
    fields = struct.unpack('<IHHHHHIIIHH', header)
    if fields[0] != reader.LOCAL:
        raise ValueError('bad ZIP local header')
    start = entry['offset'] + 30 + fields[-2] + fields[-1]
    # Bound RAM use; the existing single-member CLI reads the whole member.
    with output.open('xb') as stream:
        for offset in range(0, entry['compressed'], 32 << 20):
            stream.write(reader.fetch(url, start + offset, min(32 << 20, entry['compressed'] - offset)))
    if output.stat().st_size != entry['uncompressed'] or sha(output) != AEA_SHA:
        raise ValueError('remote OS differs from the verified local IPSW member')


@contextlib.contextmanager
def mounted(raw, point, *, readonly=True):
    point.mkdir()
    command = ['hdiutil', 'attach', '-imagekey', 'diskimage-class=CRawDiskImage',
               '-nobrowse', '-owners', 'on', '-mountpoint', str(point), '-plist']
    if readonly:
        command.append('-readonly')
    info = plistlib.loads(subprocess.check_output([*command, str(raw)]))
    device = next(e['dev-entry'] for e in info['system-entities'] if 'dev-entry' in e)
    try:
        yield point
    finally:
        subprocess.run(['hdiutil', 'detach', device], check=True)


def main():
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise SystemExit('run as root on the macOS build host')
    work = Path(os.environ.get('RUNNER_TEMP', '/tmp')) / 'nabu-desktop-data'
    work.mkdir(exist_ok=False)
    output = ROOT / 'out/nabu-desktop-data'
    output.mkdir(parents=True, exist_ok=False)
    upstream = work / 'upstream'
    for relative, (repo, revision, digest) in SOURCES.items():
        source_path = 'Test/packed_ramdisk.py' if repo == 'DarwinBoot' else relative
        data = urllib.request.urlopen(f'https://raw.githubusercontent.com/UniXNU/{repo}/{revision}/{source_path}', timeout=60).read()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('upstream tool changed: ' + relative)
        target = upstream / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (upstream / 'build').mkdir()
    archive = work / 'ipsw.tar.gz'
    urllib.request.urlretrieve('https://github.com/blacktop/ipsw/releases/download/v3.1.739/ipsw_3.1.739_macOS_arm64.tar.gz', archive)
    if sha(archive) != 'c09e8eba02c332f5b50d15f94383e59dd35646d70f6ea9e78d185e9afda6abc2':
        raise ValueError('ipsw tool archive differs')
    with tarfile.open(archive) as package:
        member = next(m for m in package.getmembers() if Path(m.name).name == 'ipsw' and m.isfile())
        tool = work / 'ipsw'
        tool.write_bytes(package.extractfile(member).read())
        tool.chmod(0o755)
    aea = work / MEMBER
    fetch_os(aea)
    subprocess.run([str(tool), 'fw', 'aea', str(aea), '--output', str(work)], check=True)
    raw = aea.with_suffix('')
    if sha(raw) != RAW_SHA:
        raise ValueError('decrypted OS differs from the local 25F71 image')
    aea.unlink()
    password = secrets.token_urlsafe(18)
    with mounted(raw, work / 'os') as os_mount:
        version = plistlib.loads((os_mount / 'System/Library/CoreServices/SystemVersion.plist').read_bytes())
        if version['ProductBuildVersion'] != '25F71':
            raise ValueError('wrong OS version')
        # Passing a mount avoids modifying the pinned tool's raw-image attach path.
        subprocess.run([sys.executable, str(upstream / 'scripts/make-macos-data-seed.py'),
                        str(os_mount), str(output / 'data.dbr'), '--gib', '3',
                        '--volume-uuid', DATA_UUID, '--skip-setup',
                        '--user', 'nabu:' + password, '--auto-login'], check=True)
        for name in ('sbin/launchd', 'usr/lib/dyld', 'usr/share/firmlinks'):
            target = output / 'os-samples' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(os_mount / name, target)
    sys.path.insert(0, str(upstream / 'drivers/DarwinBoot/Test'))
    import packed_ramdisk
    logical, extents = packed_ramdisk.regions((output / 'data.dbr').read_bytes())
    unpacked = work / 'verify.raw'
    with unpacked.open('xb') as stream:
        stream.truncate(logical)
        for offset, content in extents:
            stream.seek(offset)
            stream.write(content)
    with mounted(unpacked, work / 'verify') as data:
        for name in ('private/var/db/.AppleSetupDone', 'Users/nabu/.skipbuddy',
                     'private/var/db/dslocal/nodes/Default/users/nabu.plist', 'private/etc/kcpassword'):
            if not (data / name).is_file():
                raise ValueError('missing Data state: ' + name)
        login = plistlib.loads((data / 'Library/Preferences/com.apple.loginwindow.plist').read_bytes())
        if login.get('autoLoginUser') != 'nabu' or (data / 'Users/nabu').stat().st_uid != 501:
            raise ValueError('auto login/home ownership differs')
        volume = plistlib.loads(subprocess.check_output(['diskutil', 'info', '-plist', str(data)]))
        if volume.get('VolumeUUID', '').upper() != DATA_UUID:
            raise ValueError('Data UUID differs')
    credentials = output / 'test-account.json'
    credentials.write_text(json.dumps({'username': 'nabu', 'password': password, 'test_only': True}) + '\n')
    credentials.chmod(0o600)
    if os.environ.get('SUDO_UID') and os.environ.get('SUDO_GID'):
        os.chown(credentials, int(os.environ['SUDO_UID']), int(os.environ['SUDO_GID']))
    report = {'build': '25F71', 'upstream': PIN, 'source_os_sha256': RAW_SHA,
              'data_uuid': DATA_UUID, 'logical_bytes': logical,
              'packed_bytes': (output / 'data.dbr').stat().st_size,
              'packed_sha256': sha(output / 'data.dbr'), 'readback_verified': True,
              'persistent': False, 'hardware_verified': False}
    (output / 'build.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
