"""Compile the Darwin overlay branch with a deterministic mount/lookup model."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'platform/nabu/macos26/bootshim/apfs_boot_util.c'


class ODOverlay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('cc')
        if not compiler:
            raise unittest.SkipTest('C compiler unavailable')
        cls.work = tempfile.TemporaryDirectory(prefix='nabu-od-overlay-')
        root = Path(cls.work.name)
        (root / 'sys').mkdir()
        (root / 'sys/mount.h').write_text('''#pragma once
#define MNT_RDONLY 1
#define MNT_UNION 32
struct statfs { unsigned long f_flags; char f_fstypename[16], f_mntonname[1024], f_mntfromname[1024]; };
int statfs(const char *, struct statfs *);
int mount(const char *, const char *, int, void *);
int unmount(const char *, int);
''')
        wrapper = root / 'overlay.c'
        wrapper.write_text(r'''
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include <sys/mount.h>
static int scenario, mounted, mounts, unmounts;
static int fake_stat(const char *path, struct stat *s) {
    memset(s, 0, sizeof(*s));
    s->st_uid=0; s->st_gid=0; s->st_dev=1; s->st_ino=2;
    if (strstr(path,"xpcproxy")) {
        if (mounted && scenario==3) { errno=ENOENT; return -1; }
        if (mounted && scenario==4) s->st_ino=3;
        s->st_mode=S_IFREG|0755; return 0;
    }
    if (!strcmp(path,"/private/var/ngpu/od")) {
        if (scenario==1) { errno=ENOENT; return -1; }
        s->st_mode=S_IFDIR|0755;
        if (scenario==5) s->st_uid=501;
        return 0;
    }
    s->st_mode=S_IFREG|0755; s->st_size=1;
    if (scenario==6) s->st_mode|=0020;
    return 0;
}
int statfs(const char *path, struct statfs *s) {
    (void)path; memset(s,0,sizeof(*s));
    strcpy(s->f_fstypename,mounted?"bindfs":"apfs");
    if (mounted) {
        s->f_flags=MNT_RDONLY|MNT_UNION;
        strcpy(s->f_mntonname,"/usr/libexec");
        strcpy(s->f_mntfromname,"/private/var/ngpu/od");
        if (scenario==7) s->f_flags &= ~MNT_UNION;
        if (scenario==9) strcpy(s->f_mntfromname,"/other");
    }
    return 0;
}
int mount(const char *type,const char *point,int flags,void *source) {
    if (strcmp(type,"bindfs") || strcmp(point,"/usr/libexec") ||
        strcmp((char *)source,"/private/var/ngpu/od") || flags!=(MNT_RDONLY|MNT_UNION)) abort();
    if (scenario==8 || scenario==11) {
        if (scenario==11) mounted=1; /* unexpected failed-mount side effect */
        errno=EPERM; return -1;
    }
    mounts++; mounted=1; return 0;
}
int unmount(const char *path,int flags) {
    if (strcmp(path,"/usr/libexec") || flags) abort();
    unmounts++;
    if (scenario==10) { errno=EPERM; return -1; }
    mounted=0; return 0;
}
static int fake_open(const char *path,int flags,...) {
    if (!(flags&O_NOFOLLOW)) abort();
    return strstr(path,"/private/")?100:101;
}
static ssize_t fake_read(int fd,void *bytes,size_t count) {
    (void)count;
    static int seen[2];
    if (seen[fd-100]++%2) return 0;
    *(char *)bytes=((scenario==2 || scenario==10) && fd==101)?'b':'a';
    return 1;
}
static int fake_close(int fd) { (void)fd; return 0; }
#define stat(path,buf) fake_stat(path,buf)
#define lstat(path,buf) fake_stat(path,buf)
#define open fake_open
#define read fake_read
#define close fake_close
#define main original_shim_main
''' + '#include "' + str(SOURCE) + '"\n' + r'''
#undef main
int main(int argc,char **argv) {
    if(argc!=2) return 2;
    scenario=atoi(argv[1]);
    if(scenario==9) mounted=1;
    int result=prepare_od_overlay();
    if(result==0 && scenario==0) result=prepare_od_overlay();
    printf("result=%d mounts=%d unmounts=%d\n",result,mounts,unmounts);
    return 0;
}
''')
        cls.binary = root / 'overlay'
        subprocess.run([compiler, '-std=c11', '-D_GNU_SOURCE', '-D__APPLE__',
                        '-I', str(root), '-Wall', '-Wextra', '-Werror',
                        str(wrapper), '-o', str(cls.binary)], check=True,
                       capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def run_case(self, case):
        return subprocess.run([str(self.binary), str(case)], check=True,
                              capture_output=True, text=True)

    def test_union_mount_and_second_phase_are_idempotent(self):
        result = self.run_case(0)
        self.assertIn('result=0 mounts=1 unmounts=0', result.stdout)
        self.assertEqual(result.stderr.count('lower xpcproxy preserved'), 2)

    def test_absent_source_keeps_original_and_bad_permissions_never_mount(self):
        for case in (1, 5, 6, 8, 9):
            with self.subTest(case=case):
                result = self.run_case(case)
                self.assertIn('result=0 mounts=0 unmounts=0', result.stdout)
                self.assertIn('optional OD overlay inactive:', result.stderr)
                self.assertIn('continuing boot', result.stderr)
                self.assertNotIn('readonly union ready', result.stderr)

    def test_eperm_preserves_original_namespace_and_continues_boot(self):
        result = self.run_case(8)
        self.assertIn('result=0 mounts=0 unmounts=0', result.stdout)
        self.assertIn('mount OD union rejected; original OD retained', result.stderr)
        self.assertIn('errno=1 ', result.stderr)
        self.assertNotIn('readonly union ready', result.stderr)

    def test_byte_mismatch_missing_lower_wrong_inode_or_missing_union_roll_back(self):
        for case in (2, 3, 4, 7):
            with self.subTest(case=case):
                result = self.run_case(case)
                self.assertIn('result=0 mounts=1 unmounts=1', result.stdout)
                self.assertIn('rollback succeeded, original OD retained, continuing boot', result.stderr)
                self.assertNotIn('readonly union ready', result.stderr)

    def test_failed_rollback_remains_fatal_and_never_claims_original_retained(self):
        result = self.run_case(10)
        self.assertIn('result=1 mounts=1 unmounts=1', result.stdout)
        self.assertIn('rollback OD union', result.stderr)
        self.assertNotIn('continuing boot', result.stderr)
        self.assertNotIn('original OD retained', result.stderr)
        self.assertNotIn('readonly union ready', result.stderr)

    def test_failed_mount_with_changed_namespace_is_not_silently_accepted(self):
        result = self.run_case(11)
        self.assertIn('result=1 mounts=0 unmounts=0', result.stdout)
        self.assertIn('OD mount rejected but namespace differs', result.stderr)
        self.assertNotIn('continuing boot', result.stderr)


if __name__ == '__main__':
    unittest.main()
