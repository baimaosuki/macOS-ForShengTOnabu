#!/usr/bin/env python3
"""Create a new 27.3 Data seed with upstream tools on an Intel Mac.

The seed and unattended test account are encrypted for the local recipient
before artifact upload. No installed or previous Data volume is read.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
from unixnu import cli, ipsw, tree
from unixnu.config import Context
from unixnu.util import sha256_file


def main():
    assert platform.machine() == 'x86_64' and platform.system() == 'Darwin'
    ctx = Context(cli.parser().parse_args(['build', 'runtime', '-D', str(ROOT / 'devices/nabu'), '--os', 'macos-27']))
    output = ROOT / 'out/fresh-runtime'
    output.mkdir(parents=True, exist_ok=True)
    source = ipsw.Ipsw(ctx)
    image = source.disk_image('OS')
    expected = ctx.board['root']['os_image_sha256']
    assert sha256_file(image) == expected
    config = (ROOT / 'devices/nabu/configs/nabu_macos27_defconfig').read_text()
    identity = re.search(r'^CONFIG_XNU_OWNER_DATA_VOLUME_UUID="([A-F0-9-]+)"$', config, re.M).group(1)
    account = dict(name='nabu', password=secrets.token_urlsafe(24), real_name='Nabu')
    private = output / 'private'
    private.mkdir(mode=0o700)
    account_file = private / 'account.json'
    account_file.write_text(json.dumps(account) + '\n')
    account_file.chmod(0o600)
    seed = private / 'data.dbr'
    subprocess.run(['sudo', '-n', sys.executable, str(ROOT / 'scripts/make-macos-data-seed.py'),
                    str(image), str(seed), '--gib', '8', '--skip-setup', '--user-stdin',
                    '--auto-login', '--volume-uuid', identity],
                   input=json.dumps(account), text=True, check=True)
    # The source runtime builder needs only its script layout and the boot
    # shims, not the full selected GPU component closure.
    trust = [str(source.extract(name)) for name in ctx.board['payload']['runtime']['trust_components']]
    request = dict(tree=str(ROOT), device=str(ROOT / 'devices/nabu'), output=str(private),
                   target='macos27', build='26C5035r', data_ramdisk=str(seed),
                   init_data=False, gpu_addon_enabled=False, gpu_addon=None,
                   trust_cache=None, boot_shims=True, trust_modules=trust)
    request_file = output / 'request.json'
    request_file.write_text(json.dumps(request, indent=2) + '\n')
    subprocess.run([sys.executable, str(ROOT / 'scripts/build-boot-runtime.py'), '--request', str(request_file)], check=True)
    generated = json.loads((private / 'generated.json').read_text())
    files = [seed, account_file, Path(generated['gpu_addon']), Path(generated['trust_cache'])]
    report = dict(build='26C5035r', host=platform.machine(), data_volume_uuid=identity,
                  new_seed=True, old_data_reused=False, skip_setup=True, auto_login=True,
                  files={p.name: dict(bytes=p.stat().st_size, sha256=sha256_file(p)) for p in files},
                  device_tested=False)
    (private / 'runtime-verification.json').write_text(json.dumps(report, indent=2) + '\n')
    archive = output / 'runtime.tar'
    with tarfile.open(archive, 'w') as bundle:
        for path in [*files, private / 'runtime-verification.json']:
            bundle.add(path, arcname=path.name)
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    recipient = (ROOT / 'devices/nabu/runtime-recipient.pem').read_bytes()
    key, nonce = secrets.token_bytes(32), secrets.token_bytes(12)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encrypted = output / 'runtime.enc'
    with archive.open('rb') as inp, encrypted.open('wb') as out:
        while block := inp.read(8 << 20):
            out.write(encryptor.update(block))
        out.write(encryptor.finalize())
    public = serialization.load_pem_public_key(recipient)
    wrapped = public.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    envelope = dict(algorithm='RSA-OAEP-SHA256+AES256-GCM', nonce=nonce.hex(), tag=encryptor.tag.hex(),
                    wrapped_key=wrapped.hex(), recipient_sha256=hashlib.sha256(recipient).hexdigest(),
                    ciphertext_sha256=sha256_file(encrypted), build='26C5035r')
    (output / 'runtime-envelope.json').write_text(json.dumps(envelope, indent=2) + '\n')
    archive.unlink()
    print('New27.3 Data seed and signed upstream boot shims encrypted for local delivery')


if __name__ == '__main__':
    main()
