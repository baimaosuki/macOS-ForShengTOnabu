"""Execute the real opt-in installer and its directly spawned shell marker."""
from pathlib import Path
import json
import os
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
SHIM = ROOT / 'platform/nabu/macos26/bootshim'


def test_session_probe_direct_child_and_spawn_failure(tmp_path):
    (tmp_path / 'sys').mkdir()
    (tmp_path / 'sys/sysctl.h').write_text('/* declared by the host adapter */\n')
    script = (SHIM / 'session_probe.sh').read_text()
    (tmp_path / 'nabu_session_probe_script.h').write_text(
        'static const char nabu_session_probe_script[] = ' + json.dumps(script) + ';\n')
    (tmp_path / 'test.c').write_text(r'''
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
static pid_t child=-1;
static int fixture_sysctl(const char *key, void *out, size_t *size, void *input, size_t n) {
    (void)input; (void)n;
    assert(!strcmp(key,"kern.bootargs"));
    const char *value=getenv("PROBE_ARGS");
    assert(strlen(value)+1<=*size);
    strcpy(out,value); *size=strlen(value)+1; return 0;
}
static int fixture_open(const char *path, int flags, mode_t mode) {
    assert(!strcmp(path,"/System/Volumes/Data/private/var/db/nabu-session-probe.sh"));
    return open("nabu-session-probe.sh",flags,mode);
}
static int fixture_mkdir(const char *path, mode_t mode) {
    assert(!strcmp(path,"/System/Volumes/Data/private/var/tmp/nabu-session-probe"));
    return mkdir("reports",mode);
}
static int fixture_lstat(const char *path, struct stat *st) {
    assert(!strcmp(path,"/System/Volumes/Data/private/var/tmp/nabu-session-probe"));
    int result=lstat("reports",st);st->st_uid=0;return result;
}
static int fixture_addopen(posix_spawn_file_actions_t *a, int fd, const char *path, int flags, mode_t mode) {
    assert(fd>=0 && fd<=2);
    assert(!strcmp(path,fd?"/dev/console":"/dev/null"));
    if(fd) {path="console.txt";flags|=O_CREAT|O_APPEND;mode=0600;}
    return posix_spawn_file_actions_addopen(a,fd,path,flags,mode);
}
static int fixture_spawn(pid_t *pid,const char *path,const posix_spawn_file_actions_t *actions,
        const posix_spawnattr_t *attr,char *const args[],char *const env[]) {
    short flags=0;assert(!posix_spawnattr_getflags(attr,&flags));
    assert(flags&POSIX_SPAWN_SETSID);
    assert(!strcmp(path,"/bin/sh")&&!strcmp(args[0],path));
    assert(!strcmp(args[1],"/private/var/db/nabu-session-probe.sh")&&!args[2]);
    assert(!strcmp(env[0],"PATH=/usr/bin:/bin:/usr/sbin:/sbin"));
    assert(!strcmp(env[1],"HOME=/var/root")&&!env[2]);
    if(getenv("SPAWN_FAIL"))return EACCES;
    /* Execute the real installed script. Stop at its first delay after its
     * actual start marker, without running macOS sampling tools on Linux. */
    char *local[]={"/bin/sh","-c","sleep() { exit 0; }; . ./nabu-session-probe.sh",NULL};
    int result=posix_spawn(pid,path,actions,attr,local,env);child=*pid;return result;
}
#define sysctlbyname fixture_sysctl
#define open fixture_open
#define mkdir fixture_mkdir
#define lstat fixture_lstat
#define posix_spawn_file_actions_addopen fixture_addopen
#define posix_spawn fixture_spawn
#include "session_probe.h"
int main(void) {
    nabu_install_session_probe();
    if(child>0){int status;assert(waitpid(child,&status,0)==child);assert(WIFEXITED(status)&&!WEXITSTATUS(status));}
    return 0;
}
''')
    executable = tmp_path / 'test'
    subprocess.run(['cc', '-std=c11', '-D_GNU_SOURCE', '-Wall', '-Wextra', '-Werror',
                    '-I', str(tmp_path), '-I', str(SHIM), str(tmp_path / 'test.c'),
                    '-o', str(executable)], check=True, capture_output=True)
    for args in ('nabu-session-log=1', 'x-nabu-session-probe=1', 'nabu-session-probe=10'):
        subprocess.run([executable], cwd=tmp_path, env=dict(os.environ, PROBE_ARGS=args), check=True)
        assert not (tmp_path / 'nabu-session-probe.sh').exists()
    result = subprocess.run([executable], cwd=tmp_path,
        env=dict(os.environ, PROBE_ARGS='-v nabu-session-probe=1 cpus=8'),
        check=True, capture_output=True, text=True)
    assert 'direct spawn pid=' in result.stderr and 'status=0' in result.stderr
    assert (tmp_path / 'nabu-session-probe.sh').read_text() == script
    assert (tmp_path / 'nabu-session-probe.sh').stat().st_mode & 0o777 == 0o600
    console = (tmp_path / 'console.txt').read_text()
    assert 'NABU-SESSION-PROBE started pid=' in console and 'version=5' in console
    assert 'NABU-SESSION-PROBE shell exit=0' in console
    assert not (tmp_path / 'org.nabu.session-probe.plist').exists()
    result = subprocess.run([executable], cwd=tmp_path,
        env=dict(os.environ, PROBE_ARGS='nabu-session-probe=1', SPAWN_FAIL='1'),
        check=True, capture_output=True, text=True)
    assert 'direct spawn pid=-1 status=13' in result.stderr
    subprocess.run(['/bin/sh', '-n', str(tmp_path / 'nabu-session-probe.sh')], check=True)


def test_probe_waits_for_real_pids_and_samples_before_blocked_launchctl(tmp_path):
    # Run the actual shell implementation against tools with startup delays.
    # launchctl ignores TERM, so only the hard guard lets round 1 finish.
    tools = tmp_path / 'tools'
    tools.mkdir()
    (tmp_path / 'nabu-session-probe').mkdir()
    counter = tmp_path / 'ps-count'
    invocations = tmp_path / 'samples'
    def tool(name, body):
        path = tools / name
        path.write_text('#!/bin/sh\n' + body)
        path.chmod(0o755)
        return str(path)
    ps = tool('ps', f"""test "$1" = -axo || exit 97
test "$2" = pid=,ucomm= || exit 98
n=0
[ ! -f '{counter}' ] || n=$(cat '{counter}')
n=$((n + 1)); echo "$n" > '{counter}'
[ "$n" -gt 1 ] || exit 0
cat <<'ROWS'
88 /usr/libexec/opendirectoryd
123 /System/Library/CoreServices/loginwindow.app/Contents/MacOS/loginwindow
151 /usr/libexec/lsd
120 /System/Library/PrivateFrameworks/SkyLight.framework/Resources/WindowServer
ROWS
""")
    sample = tool('sample', f"""case "$1" in ''|*[!0-9]*) exit 99 ;; esac
echo "$1" >> '{invocations}'
[ "$2" = 0 ] && [ "$3" = 100 ] || exit 98
while [ "$1" != -file ]; do shift; done
shift
echo "Call graph for real target" > "$1"
echo "sample diagnostic preserved"
""")
    launchctl = tool('launchctl', "trap '' TERM\nwhile :; do /bin/sleep 0.1; done\n")
    # A real symbol-processing phase takes 25 logical seconds: longer than
    # the original 15-second guard, shorter than the requested 120 seconds.
    sample_source = Path(sample)
    sample_source.write_text(sample_source.read_text().replace(
        'echo "Call graph for real target"',
        'echo "Sampling completed, processing symbols..."\n/bin/sleep 0.25\necho "Call graph for real target"'))
    shim = tool('shim', 'case "$1" in --nabu-raw-stacks) echo "NABU-RAWSTACK fixture" >&2 ;; --nabu-probe-relay) echo "relay file=$2" ;; *) exit 91 ;; esac\n')
    sleep = tool('sleep', 'case "$1" in 120) /bin/sleep 1.2 ;; 15|5|1) /bin/sleep 0.15 ;; *) : ;; esac\n')
    script = (SHIM / 'session_probe.sh').read_text()
    script = script[:script.rindex('\nsleep 15')]
    script = script.replace('/bin/ps', ps).replace('/usr/bin/sample', sample)
    script = script.replace('/bin/launchctl', launchctl).replace('/bin/sleep', sleep)
    script = script.replace('/System/Library/Filesystems/apfs.fs/Contents/Resources/apfs_boot_util', shim)
    script = script.replace('/private/var/tmp', str(tmp_path))
    script += '\nsnapshot 1\n'
    path = tmp_path / 'run.sh'
    path.write_text(script)
    result = subprocess.run(['/bin/sh', str(path)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert int(counter.read_text()) >= 2
    assert invocations.read_text().splitlines() == ['88', '123', '151', '120']
    assert result.stdout.count('sample-result process=') == 4
    assert result.stdout.count('status=0') >= 4
    for process in ('opendirectoryd', 'loginwindow', 'lsd', 'WindowServer'):
        assert (tmp_path / 'nabu-session-probe' / f'nabu-sample-{process}.txt').read_text() == 'Call graph for real target\n'
    assert result.stdout.count('report-retained path=') == 4
    assert 'sample diagnostic preserved' in result.stdout
    assert 'command-timeout' in result.stderr
    assert result.stdout.index('sample-result process=WindowServer') < result.stdout.index('launchd job=')
    assert 'end round=1' in result.stdout
    assert 'raw-watcher status=0' in result.stdout
