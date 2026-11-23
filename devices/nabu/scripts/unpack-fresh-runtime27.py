#!/usr/bin/env python3
"""Authenticate and decrypt a runtime built for your RSA recipient key.

No password, private key, account contents or wrapped key is printed. The
authenticated archive is extracted only after GCM verification. Outputs stay
private, and an existing output directory is never reused.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile


def unpack(encrypted, envelope_path, private_key, output):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    if output.exists():
        raise FileExistsError('Output must be a new private directory')
    envelope = json.loads(envelope_path.read_text())
    if envelope.get('algorithm') != 'RSA-OAEP-SHA256+AES256-GCM' or envelope.get('build') != '26C5035r':
        raise ValueError('Wrong runtime envelope algorithm/build')
    with encrypted.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != envelope['ciphertext_sha256']:
            raise ValueError('Runtime ciphertext checksum mismatch')
    key = serialization.load_pem_private_key(private_key.read_bytes(), password=None)
    public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo)
    if hashlib.sha256(public).hexdigest() != envelope['recipient_sha256']:
        raise ValueError('Runtime was encrypted for a different recipient')
    secret = key.decrypt(bytes.fromhex(envelope['wrapped_key']),
                         padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='nabu-runtime-', dir=output.parent) as td:
        temporary = Path(td)
        archive = temporary / 'runtime.tar'
        fd = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        decryptor = Cipher(algorithms.AES(secret), modes.GCM(bytes.fromhex(envelope['nonce']),
                                                           bytes.fromhex(envelope['tag']))).decryptor()
        with encrypted.open('rb') as source, os.fdopen(fd, 'wb') as target:
            for block in iter(lambda: source.read(8 << 20), b''):
                target.write(decryptor.update(block))
            target.write(decryptor.finalize())
        staged = temporary / 'files'
        staged.mkdir(mode=0o700)
        with tarfile.open(archive) as bundle:
            members = bundle.getmembers()
            names = [member.name for member in members]
            if len(names) != len(set(names)):
                raise ValueError('Duplicate runtime archive member')
            for member in members:
                path = PurePosixPath(member.name)
                if (not member.isfile() or path.is_absolute() or len(path.parts) != 1
                        or path.name in ('.', '..') or '\\' in member.name):
                    raise ValueError('Unsafe runtime archive member')
                target = staged / member.name
                with bundle.extractfile(member) as source, target.open('xb') as dest:
                    shutil.copyfileobj(source, dest)
                target.chmod(0o600)
        report = json.loads((staged / 'runtime-verification.json').read_text())
        if report.get('build') != '26C5035r' or not report.get('new_seed'):
            raise ValueError('Wrong runtime verification build')
        for name, entry in report['files'].items():
            if PurePosixPath(name).name != name or not (staged / name).is_file():
                raise ValueError('Invalid runtime file inventory')
            path = staged / name
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if path.stat().st_size != entry['bytes'] or digest != entry['sha256']:
                raise ValueError('Runtime file checksum mismatch')
        if set(names) != set(report['files']) | {'runtime-verification.json'}:
            raise ValueError('Runtime archive contains unrecorded files')
        # A sibling directory rename publishes no unauthenticated partial output.
        staged.rename(output)
    return dict(passed=True, build='26C5035r', files=len(report['files']),
                account_contents_printed=False, hardware_tested=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--encrypted', type=Path, required=True)
    parser.add_argument('--envelope', type=Path, required=True)
    parser.add_argument('--private-key', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(unpack(args.encrypted, args.envelope, args.private_key, args.output), indent=2))


if __name__ == '__main__':
    main()
