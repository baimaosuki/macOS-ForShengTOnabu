#!/usr/bin/env python3
"""Install the pinned Intel ipsw release, independent of Homebrew casks."""
import hashlib
import os
from pathlib import Path
import platform
import tarfile
import urllib.request

assert platform.system() == 'Darwin' and platform.machine() == 'x86_64'
target = Path('.ci/bin').resolve()
target.mkdir(parents=True, exist_ok=True)
archive = target / 'ipsw.tar.gz'
url = 'https://github.com/blacktop/ipsw/releases/download/v3.1.749/ipsw_3.1.749_macOS_x86_64.tar.gz'
with urllib.request.urlopen(url, timeout=120) as stream:
    data = stream.read()
assert hashlib.sha256(data).hexdigest() == 'b141b419870f740d2b527536c22e4af046360ec1e59dba47f002de002521cfea'
archive.write_bytes(data)
with tarfile.open(archive) as bundle:
    member = next(m for m in bundle if m.name == 'ipsw' or m.name.endswith('/ipsw'))
    (target / 'ipsw').write_bytes(bundle.extractfile(member).read())
(target / 'ipsw').chmod(0o755)
with Path(os.environ['GITHUB_PATH']).open('a') as stream:
    stream.write(str(target) + '\n')
print('ipsw3.1.749 Intel release SHA256 verified')
