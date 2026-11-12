"""Execute the actual opt-in installer with host filesystem/sysctl adapters."""
from pathlib import Path
import json
import os
import plistlib
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SHIM = ROOT / 'platform/nabu/macos26/bootshim'


def test_session_probe_installs_valid_job_only_when_enabled(tmp_path):
    (tmp_path / 'sys').mkdir()
    (tmp_path / 'sys/sysctl.h').write_text('/* sysctl adapter is declared by the harness */\n')
    script = (SHIM / 'session_probe.sh').read_text()
    (tmp_path / 'nabu_session_probe_script.h').write_text(
        'static const char nabu_session_probe_script[] = ' + json.dumps(script) + ';\n')
    (tmp_path / 'test.c').write_text(r'''
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
static int fixture_sysctl(const char *key, void *out, size_t *size, void *input, size_t n) {
    (void)input; (void)n;
    assert(!strcmp(key,"kern.bootargs"));
    const char *value=getenv("PROBE_ARGS");
    assert(strlen(value)+1<=*size);
    strcpy(out,value); *size=strlen(value)+1; return 0;
}
static int fixture_open(const char *path, int flags, mode_t mode) {
    assert(!strncmp(path,"/System/Volumes/Data/",21));
    return open(strrchr(path,'/')+1,flags,mode);
}
static int fixture_mkdir(const char *path, mode_t mode) {
    assert(!strcmp(path,"/System/Volumes/Data/Library/LaunchDaemons"));
    assert(mode==0755); return 0;
}
#define sysctlbyname fixture_sysctl
#define open fixture_open
#define mkdir fixture_mkdir
#include "session_probe.h"
int main(void) { nabu_install_session_probe(); return 0; }
''')
    executable = tmp_path / 'test'
    subprocess.run(['cc', '-std=c11', '-D_GNU_SOURCE', '-Wall', '-Wextra', '-Werror',
                    '-I', str(tmp_path), '-I', str(SHIM), str(tmp_path / 'test.c'),
                    '-o', str(executable)], check=True, capture_output=True)
    subprocess.run([executable], cwd=tmp_path, env=dict(os.environ, PROBE_ARGS='nabu-session-log=1'), check=True)
    assert not (tmp_path / 'org.nabu.session-probe.plist').exists()
    subprocess.run([executable], cwd=tmp_path,
                   env=dict(os.environ, PROBE_ARGS='-v nabu-session-probe=1 cpus=1'), check=True)
    job = plistlib.loads((tmp_path / 'org.nabu.session-probe.plist').read_bytes())
    assert job['ProgramArguments'] == ['/bin/sh', '/private/var/db/nabu-session-probe.sh']
    assert job['RunAtLoad'] and 'KeepAlive' not in job
    assert job['StandardOutPath'] == job['StandardErrorPath'] == '/dev/console'
    assert (tmp_path / 'nabu-session-probe.sh').read_text() == script
    assert (tmp_path / 'nabu-session-probe.sh').stat().st_mode & 0o777 == 0o600
    subprocess.run(['/bin/sh', '-n', str(tmp_path / 'nabu-session-probe.sh')], check=True)
