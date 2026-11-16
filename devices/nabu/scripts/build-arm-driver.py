#!/usr/bin/env python3
"""Board dispatch for the unchanged shared component build interface."""
from pathlib import Path
import os
import subprocess
import sys
root=Path(__file__).resolve().parents[1]
if sys.argv[1:] and sys.argv[1] == "ShengUfs" and os.environ.get("UNIXNU_IPSW_BUILD") == "26C5035r":
    raise SystemExit(subprocess.call([sys.executable,root/"scripts/build-nabu-source-storage.py","--tree",root]))
raise SystemExit(subprocess.call([sys.executable,root/"scripts/build-arm-driver-upstream.py",*sys.argv[1:]]))
