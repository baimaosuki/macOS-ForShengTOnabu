#!/usr/bin/env python3
"""Execute generated queue admission and the CPU presenter on 90 live surface updates."""
from pathlib import Path
import argparse,subprocess,tempfile
from qualify_nabu_display_refresh import function
ROOT=Path(__file__).resolve().parents[1]
def qualify(source):
    method=function(Path(source).read_text(),'bool NativeGOPDisplay::clean_queue_gated()')
    with tempfile.TemporaryDirectory(prefix='nabu-live-updates-') as temp:
        folder=Path(temp);(folder/'nabu_clean_queue_generated.inc').write_text(method+'\n')
        binary=folder/'live'
        subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',
            '-Wno-missing-field-initializers','-pthread','-fsanitize=address,undefined',
            '-DNABU_CLEAN_QUEUE_TEST','-I',str(folder),
            str(ROOT/'tests/nabu_cpu_present_lifecycle.cpp'),'-o',str(binary)],check=True)
        subprocess.run([str(binary)],check=True)
    print('PASS: 90 in-place updates copied through generated admission and real presenter; native zero-plane cleanup preserved')
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True)
    qualify(p.parse_args().source)
