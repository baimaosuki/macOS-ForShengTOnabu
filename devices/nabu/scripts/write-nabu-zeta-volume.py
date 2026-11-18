#!/usr/bin/env python3
"""Populate a host APFS image in an isolated KVM guest with 9p read-only inputs.

No host filesystem module or device writes are used. The guest hashes every
written file through APFS and reports a clean unmount before success.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(8 << 20), b''):
            h.update(block)
    return h.hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def safe_target(path):
    require(path.startswith('/') and '..' not in Path(path).parts and '\n' not in path, 'invalid APFS target')
    return shlex.quote('/mnt/dst' + path)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--container', type=Path, required=True)
    p.add_argument('--export', dest='export_root', type=Path, required=True)
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--kernel', type=Path, required=True)
    p.add_argument('--module', type=Path, required=True)
    p.add_argument('--kernel-version', default='6.8.0-106-generic')
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--memory', default='4096')
    a = p.parse_args(argv)
    a.work.mkdir(parents=True, exist_ok=True)
    require(a.container.is_file() and a.kernel.is_file() and a.module.is_file(), 'missing guest/container input')
    require(not a.report.exists(), 'report already exists')
    plan = json.loads(a.plan.read_text())
    lines = ['#!/bin/sh', 'set -e']
    for entry in plan.get('directories', []):
        target = safe_target(entry['path'])
        lines += [f'mkdir -p {target}', f'chown {int(entry["uid"])}:{int(entry["gid"])} {target}',
                  f'chmod {int(entry["mode"], 8):o} {target}']
    files = []
    export = a.export_root.resolve()
    for index, entry in enumerate(plan.get('files', [])):
        source = Path(entry['source']).absolute()
        relative = source.relative_to(export)
        require(source.is_file(), f'missing source {source}')
        src = shlex.quote('/mnt/src/' + relative.as_posix())
        dst = safe_target(entry['path'])
        expected = digest(source)
        lines += [f'mkdir -p {safe_target(str(Path(entry["path"]).parent))}', f'rm -f {dst}',
                  f'cp {src} {dst}', f'chown {int(entry["uid"])}:{int(entry["gid"])} {dst}',
                  f'chmod {int(entry["mode"], 8):o} {dst}',
                  f'echo "NABU-APFS HASH {index} $(sha256sum {dst} | cut -d\" \" -f1)"']
        if index % 100 == 0:
            lines.append(f'echo "NABU-APFS PROGRESS {index}/{len(plan["files"])}"')
        files.append(dict(entry, sha256=expected, bytes=source.stat().st_size))
    for entry in plan.get('symlinks', []):
        target = safe_target(entry['path'])
        lines += [f'ln -sfn {shlex.quote(entry["target"])} {target}',
                  f'chown -h {int(entry["uid"])}:{int(entry["gid"])} {target}']
    metadata = plan.get('bsd_flags', [])
    for index, entry in enumerate(metadata):
        flags = int(entry['flags'])
        require(0 <= flags <= 0xffffffff and not flags & 0x20, 'invalid decompressed BSD flags')
        lines += [f'actual=$(/bin/apfs-bsdflags {safe_target(entry["path"])} {flags})',
                  f'echo "NABU-APFS FLAGS {index} $actual"']
    lines += ['sync', 'echo "NABU-APFS PLAN-OK"']
    a.work.joinpath('plan.sh').write_text('\n'.join(lines) + '\n')
    require(a.work.resolve().is_relative_to(export), 'work must be inside read-only export')
    init = a.work / 'initramfs'
    for folder in ['bin', 'dev', 'proc', 'sys', 'mnt/src', 'mnt/dst', 'modules']:
        (init / folder).mkdir(parents=True, exist_ok=True)
    shutil.copyfile('/usr/bin/busybox', init / 'bin/busybox')
    (init / 'bin/busybox').chmod(0o755)
    if metadata:
        subprocess.run(['x86_64-linux-gnu-gcc-13', '-static', '-O2', '-Wall', '-Wextra', '-Werror',
                        str(Path(__file__).with_name('zeta-guest-bsd-flags.c')),
                        '-o', str(init / 'bin/apfs-bsdflags')], check=True)
    dependencies = []
    for module in ['9p', '9pnet_virtio', 'libcrc32c']:
        out = subprocess.check_output(['modprobe', '--show-depends', '--set-version', a.kernel_version, module], text=True)
        dependencies += [line.split()[1] for line in out.splitlines() if line.startswith('insmod ')]
    module_names = []
    for source in dict.fromkeys(dependencies):
        dest = init / 'modules' / Path(source).name.removesuffix('.zst')
        if source.endswith('.zst'):
            with dest.open('wb') as f:
                subprocess.run(['zstd', '-q', '-d', '-c', source], stdout=f, check=True)
        else:
            shutil.copyfile(source, dest)
        module_names.append(dest.name)
    shutil.copyfile(a.module, init / 'modules/apfs.ko')
    module_names.append('apfs.ko')
    guest_plan = shlex.quote('/mnt/src/' + (a.work / 'plan.sh').resolve().relative_to(export).as_posix())
    init_lines = ['#!/bin/busybox sh', '/bin/busybox --install -s /bin',
                  'trap \'echo "NABU-APFS FAIL"; sync; poweroff -f\' EXIT', 'set -e',
                  'mount -t proc proc /proc', 'mount -t sysfs sys /sys', 'mount -t devtmpfs dev /dev']
    init_lines += [f'insmod /modules/{name}' for name in module_names]
    init_lines += ['mount -t 9p -o trans=virtio,version=9p2000.L,msize=1048576,ro src /mnt/src',
                   'mount -t apfs -o readwrite /dev/vda /mnt/dst', f'sh {guest_plan}', 'sync',
                   'umount /mnt/dst', 'echo "NABU-APFS UNMOUNTED"', 'umount /mnt/src',
                   'echo "NABU-APFS DONE"', 'trap - EXIT', 'poweroff -f']
    (init / 'init').write_text('\n'.join(init_lines) + '\n')
    (init / 'init').chmod(0o755)
    listing = subprocess.check_output(['find', '.'], cwd=init)
    with (a.work / 'initramfs.cpio').open('wb') as output:
        subprocess.run(['cpio', '-o', '-H', 'newc'], cwd=init, input=listing, stdout=output, stderr=subprocess.PIPE, check=True)
    console = a.work / 'guest.log'
    command = ['qemu-system-x86_64', '-enable-kvm', '-cpu', 'host', '-m', a.memory, '-smp', '2',
               '-kernel', str(a.kernel), '-initrd', str(a.work / 'initramfs.cpio'),
               '-append', 'console=ttyS0 quiet panic=-1', '-nographic', '-no-reboot',
               '-drive', f'file={a.container},format=raw,if=virtio',
               '-virtfs', f'local,path={export},mount_tag=src,security_model=none,readonly=on']
    with console.open('w') as f:
        run = subprocess.run(command, stdout=f, stderr=subprocess.STDOUT, timeout=7200)
    text = console.read_text(errors='replace')
    hashes = {int(i): h for i, h in re.findall(r'NABU-APFS HASH (\d+) ([0-9a-f]{64})', text)}
    failures = [entry['path'] for i, entry in enumerate(files) if hashes.get(i) != entry['sha256']]
    native_flags = {int(i): int(flags, 16) for i, flags in re.findall(r'NABU-APFS FLAGS (\d+) ([0-9a-f]{8})', text)}
    failures += [entry['path'] + ': BSD flags' for i, entry in enumerate(metadata)
                 if native_flags.get(i) != entry['flags']]
    markers = all('NABU-APFS ' + marker in text for marker in ['PLAN-OK', 'UNMOUNTED', 'DONE'])
    result = dict(schema='nabu-zeta-apfs-write-v1', container=str(a.container),
                  files=len(files), bytes=sum(f['bytes'] for f in files), verified_hashes=len(hashes),
                  native_bsd_flags_verified=len(native_flags),
                  failures=failures, clean_unmount=markers, exit_status=run.returncode,
                  complete=not failures and markers and 'NABU-APFS FAIL' not in text and run.returncode == 0,
                  plan_sha256=digest(a.plan), console=str(console), hardware_tested=False)
    a.report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
