"""Execute the real XNU bulk control body before and after the progress fix."""
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'platform/nabu/macos26/desktop/bulk-progress.patch'


def test_bulk_union_progress_actual_control_body(tmp_path):
    source = ROOT / 'sources/macos26/xnu'
    if not source.exists():
        pytest.skip('pinned 25F71 XNU checkout required')
    if not shutil.which('cc'):
        pytest.skip('host C compiler required')
    relative = 'bsd/vfs/vfs_attrlist.c'
    baseline = subprocess.run(['git', '-C', str(source), 'show',
                               'ac9718fb1af618d5ce8678d0dc6e8a58f252216f:' + relative],
                              check=True, capture_output=True, text=True).stdout
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_text(baseline)
    for patch_name in ('patches/nabu-union-getattrlistbulk.patch',
                       'desktop/integrated.patch', 'desktop/session-filesystem.patch'):
        text = (ROOT / 'platform/nabu/macos26' / patch_name).read_text()
        sections = re.split(r'(?=^--- a/)', text, flags=re.M)[1:]
        selected = ''.join(section for section in sections
                           if section.splitlines()[0][6:] == relative)
        filtered = tmp_path / 'base.patch'
        filtered.write_text(selected)
        subprocess.run(['patch', '--batch', '--forward', '-p1', '-d', str(tmp_path),
                        '-i', str(filtered)], check=True, capture_output=True, text=True)
    original = target.read_text()
    marker = '#if CONFIG_UNION_MOUNTS\n\t/* Match getdirentriesattr:'
    if marker not in original:
        pytest.skip('prepared source must contain EVI union bulk implementation')
    subprocess.run(['patch', '--batch', '--forward', '-p1', '-d', str(tmp_path),
                    '-i', str(PATCH)], check=True, capture_output=True, text=True)
    fixed = target.read_text()
    template = (ROOT / 'tests/fixtures/nabu_bulk_progress.c').read_text()
    zero_start = original.index('\tif (!fp->fp_glob->fg_offset)', original.index('int\ngetattrlistbulk('))
    zero = original[zero_start:original.index('\n\tauio =', zero_start)]
    for name, source_text in [('pre', original), ('post', fixed)]:
        start = source_text.index(marker)
        block = source_text[start:source_text.index('\n\tif (count)', start)]
        code = template.replace('/* EXTRACTED_ZERO_OFFSET */', zero).replace('/* EXTRACTED_UNION_RETRY */', block)
        fixture = tmp_path / f'{name}.c'
        fixture.write_text(code)
        executable = tmp_path / name
        subprocess.run(['cc', '-std=c11', '-O0', *(['-DFIXED'] if name == 'post' else []),
                        str(fixture), '-o', str(executable)], check=True, capture_output=True, text=True)
        result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=5)
        assert result.returncode == 0, result.stdout + result.stderr
