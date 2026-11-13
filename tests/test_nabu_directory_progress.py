"""Replay production union lookup and libc directory read control before/after."""
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_directory_progress_actual_helper_and_reader(tmp_path):
    source = ROOT / 'sources/macos26/xnu'
    if not source.exists() or not shutil.which('cc'):
        pytest.skip('pinned XNU source and host C compiler required')
    targets = ('bsd/vfs/vfs_lookup.c', 'bsd/vfs/vfs_syscalls.c')
    for relative in targets:
        data = subprocess.run(['git', '-C', str(source), 'show',
                               'ac9718fb1af618d5ce8678d0dc6e8a58f252216f:' + relative],
                              check=True, capture_output=True, text=True).stdout
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data)
    for name in ('patches/nabu-union-lower-directory.patch',
                 'desktop/integrated.patch', 'desktop/session-filesystem.patch'):
        text = (ROOT / 'platform/nabu/macos26' / name).read_text()
        sections = re.split(r'(?=^--- a/)', text, flags=re.M)[1:]
        patch = ''.join(part for part in sections if part.splitlines()[0][6:] in targets)
        if patch:
            file = tmp_path / 'base.patch'
            file.write_text(patch)
            subprocess.run(['patch', '--batch', '--forward', '-p1', '-d', str(tmp_path),
                            '-i', str(file)], check=True, capture_output=True, text=True)
    template = (ROOT / 'tests/fixtures/nabu_directory_progress.c').read_text()
    for name in ('before', 'after'):
        if name == 'after':
            subprocess.run(['patch', '--batch', '--forward', '-p1', '-d', str(tmp_path),
                            '-i', str(ROOT / 'platform/nabu/macos26/desktop/directory-progress.patch')],
                           check=True, capture_output=True, text=True)
        lookup = (tmp_path / targets[0]).read_text()
        a = lookup.index('int\nlookup_traverse_union(')
        helper = lookup[a:lookup.index('\n#endif /* CONFIG_UNION_MOUNTS */', a)]
        syscalls = (tmp_path / targets[1]).read_text()
        a = syscalls.index('static int\ngetdirentries_common(')
        reader = syscalls[a:syscalls.index('\n\nint\ngetdirentries(', a)]
        code = template.replace('/* EXTRACTED_UNION_HELPER */', helper).replace(
            '/* EXTRACTED_GETDIRENTRIES */', reader)
        file, exe = tmp_path / (name + '.c'), tmp_path / name
        file.write_text(code)
        compiled = subprocess.run(['cc', '-std=c11', '-O0', '-fsanitize=address,undefined',
                        '-fno-omit-frame-pointer', *(['-DFIXED'] if name == 'after' else []),
                        str(file), '-o', str(exe)], capture_output=True, text=True)
        assert compiled.returncode == 0, compiled.stdout + compiled.stderr
        result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=5)
        assert result.returncode == 0, result.stdout + result.stderr
        assert '10 cases passed' in result.stdout
