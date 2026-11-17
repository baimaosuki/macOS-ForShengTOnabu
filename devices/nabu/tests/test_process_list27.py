"""Exercise the production snapshot helper against growing sysctl results."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]


def test_snapshot_growth_and_errors(tmp_path):
    source = tmp_path / 'snapshot.c'
    source.write_text(r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#define CTL_KERN 1
#define KERN_PROC 2
#define KERN_PROC_ALL 3
struct kinfo_proc { unsigned pid; char name[28]; };
static unsigned mode, queries, fetches, allocations, frees;
static void *allocate(size_t n) { ++allocations; if (mode == 7) { errno=ENOMEM; return NULL; } return malloc(n); }
static void release(void *p) { ++frees; free(p); }
static int sysctl(int *mib, unsigned count, void *out, size_t *n, void *in, size_t len) {
    assert(count==4 && mib[0]==1 && mib[1]==2 && mib[2]==3 && !in && !len);
    if (!out) {
        ++queries;
        if (mode==4) { errno=EPERM; return -1; }
        if (mode==8 && queries<3) { errno=ENOMEM; return -1; }
        *n=mode==5 ? SIZE_MAX : 20*sizeof(struct kinfo_proc); return 0;
    }
    ++fetches;
    assert(*n==40*sizeof(struct kinfo_proc)+65536);
    if (mode==2 || (mode==1 && fetches<=3)) {
        memset(out,0x55,*n); *n+=sizeof(struct kinfo_proc); errno=ENOMEM; return -1;
    }
    if (mode==3) { errno=EACCES; return -1; }
    if (mode==6) { *n=1; return 0; }
    if (mode==9) { ++*n; return 0; }
    *n=25*sizeof(struct kinfo_proc);
    for(unsigned i=0;i<25;i++) ((struct kinfo_proc *)out)[i].pid=100+i;
    return 0;
}
#define malloc allocate
#define free release
#include "nabu_process_list.h"
#undef malloc
#undef free
int main(void) {
    for (mode=0;mode<10;mode++) {
        queries=fetches=allocations=frees=0;
        size_t n=123; struct kinfo_proc *p=nabu_session_process_list(&n);
        if (mode==0 || mode==1 || mode==8) {
            assert(p && n==25*sizeof(*p) && p[24].pid==124);
            assert(fetches==(mode==1 ? 4u : 1u));
            assert(queries==(mode==8 ? 3u : fetches)); release(p);
        } else {
            assert(!p && !n);
            assert(errno==(mode==2 || mode==7 ? ENOMEM : mode==3 ? EACCES :
                mode==4 ? EPERM : mode==5 ? EOVERFLOW : EIO));
            if(mode==2) assert(queries==5 && fetches==5);
            else assert(queries==1);
        }
        assert(frees==allocations-(mode==7));
    }
    puts("10 production snapshot race/error cases passed");
}
''')
    compiler = next(shutil.which(c) for c in ('clang', 'cc') if shutil.which(c))
    output = tmp_path / 'snapshot'
    subprocess.run([compiler, '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                    '-I', str(ROOT / 'scripts/macos-boot-shims'), str(source), '-o', str(output)], check=True)
    subprocess.run([str(output)], check=True)

if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as work:
        test_snapshot_growth_and_errors(Path(work))
