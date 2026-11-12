"""Verify the compiled page policy rejects mismatches without partial writes."""
from pathlib import Path
import hashlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / 'platform/nabu/macos26/software-compositor'


class CpuCompositor(unittest.TestCase):
    def test_actual_patch_compiles_with_both_bitmap_configurations(self):
        compiler = shutil.which('clang') or shutil.which('cc')
        if not compiler:
            self.skipTest('requires C compiler')
        patch = (ROOT / 'platform/nabu/macos26/patches/zzzzz-nabu-cpu-compositor.patch').read_text()
        # Materialize the actual postimage of each hunk; do not duplicate the
        # declaration or its conditional consumer in the test fixture.
        hunks = []
        for hunk in re.split(r'^@@[^\n]*\n', patch, flags=re.MULTILINE)[1:]:
            hunks.append('\n'.join(line[1:] for line in hunk.splitlines()
                                   if line.startswith((' ', '+'))))
        declaration = next(h for h in hunks if 'const bool nabu_cpu_page_transformed' in h)
        declaration = declaration[declaration.index('#if defined(ARM64_BOARD_CONFIG_NABU)'):]
        declaration = declaration[:declaration.index('#endif') + len('#endif')]
        consumer = next(h for h in hunks if '#if CHECK_CS_VALIDATION_BITMAP' in h)
        consumer = consumer[consumer.index('#if CHECK_CS_VALIDATION_BITMAP'):]
        # The patch's context ends at the final call argument. Complete only
        # the unchanged lexical closure omitted by unified-diff context.
        consumer += '\n}\n#endif\n'
        prefix = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#define ARM64_BOARD_CONFIG_NABU 1
#define SHENG 1
#define DEVELOPMENT 1
#define DEBUG 0
#define __unused __attribute__((unused))
#define VMP_CS_ALL_TRUE 15
#define VMP_CS_ALL_FALSE 0
#define CS_BITMAP_SET 1
struct page {int vmp_cs_validated,vmp_cs_tainted;};
struct vnode {int tag;};
static unsigned hook_calls,bitmap_calls;
static bool transformed;
static bool nabu_cpu_compositor_page(struct page *p,struct vnode *v,uint64_t offset,bool valid){
 assert(p->vmp_cs_validated==15&&v->tag==9&&offset==0x6ea0000&&valid);
 hook_calls++;return transformed;
}
#if CHECK_CS_VALIDATION_BITMAP
struct object {int pager;};
static void vnode_pager_cs_check_validation_bitmap(int pager,uint64_t offset,int op){
 assert(pager==7&&offset==0x6ea0000&&op==1);bitmap_calls++;
}
#endif
static void run(bool changed){
 struct page storage={15,0},*page=&storage;
 struct vnode node={9},*vnode=&node;
 uint64_t mo_offset=0x6ea0000;
 int validated=15,tainted=0,nx=0;
#if CHECK_CS_VALIDATION_BITMAP
 struct object backing={7},*object=&backing;
#endif
 transformed=changed;hook_calls=bitmap_calls=0;
'''
        suffix = r'''
 assert(hook_calls==1);
 assert(bitmap_calls==(CHECK_CS_VALIDATION_BITMAP&&!changed?1u:0u));
}
int main(void){run(false);run(true);return 0;}
'''
        code = prefix + declaration + '\n' + consumer + suffix
        with tempfile.TemporaryDirectory() as temp:
            src, binary = Path(temp) / 'bitmap.c', Path(temp) / 'bitmap'
            for enabled in (0, 1):
                with self.subTest(CHECK_CS_VALIDATION_BITMAP=enabled):
                    src.write_text(code)
                    build = subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror',
                                            f'-DCHECK_CS_VALIDATION_BITMAP={enabled}', str(src),
                                            '-o', str(binary)], capture_output=True, text=True)
                    self.assertEqual(build.returncode, 0, build.stderr)
                    subprocess.run([str(binary)], check=True, capture_output=True)
            # Verify that this harness reproduces the original CI failure.
            src.write_text(code.replace('nabu_cpu_page_transformed __unused =',
                                        'nabu_cpu_page_transformed ='))
            regression = subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror',
                                         '-DCHECK_CS_VALIDATION_BITMAP=0', str(src), '-o', str(binary)],
                                        capture_output=True, text=True)
            self.assertNotEqual(regression.returncode, 0)
            self.assertIn('unused variable', regression.stderr)
            self.assertIn('nabu_cpu_page_transformed', regression.stderr)

    def test_compiled_policy_atomicity_and_idempotence(self):
        compiler = shutil.which('cc')
        if not compiler:
            self.skipTest('requires C compiler')
        original = bytes((i * 19 + 7) & 255 for i in range(16384))
        after = bytearray(original)
        after[20:24] = b'ABCD'
        after[64:68] = b'EFGH'
        digest = lambda value: ','.join(str(v) for v in hashlib.sha256(value).digest())
        code = r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <openssl/sha.h>
#include "policy.h"
static void digest(const void *p,size_t n,uint8_t h[32]){SHA256(p,n,h);}
static uint8_t page[16384],saved[16384];
int main(void){
 for(size_t i=0;i<sizeof page;i++)page[i]=(i*19+7)&255;
 memcpy(saved,page,sizeof page);
 struct nabu_cpu_page rule={.before_sha256={BEFORE_HASH},.after_sha256={AFTER_HASH},.edit_count=2,
 .edits={{.offset=20,.size=4,.after={'A','B','C','D'}},{.offset=64,.size=4,.after={'E','F','G','H'}}}};
 memcpy(rule.edits[0].before,page+20,4);memcpy(rule.edits[1].before,page+64,4);
 assert(nabu_cpu_apply_page(page,sizeof page,&rule,digest)==1);
 assert(nabu_cpu_apply_page(page,sizeof page,&rule,digest)==0);
 memcpy(page,saved,sizeof page);page[800]^=1;memcpy(saved,page,sizeof page);
 assert(nabu_cpu_apply_page(page,sizeof page,&rule,digest)==-1&&!memcmp(page,saved,sizeof page));
 page[800]^=1;memcpy(saved,page,sizeof page);
 rule.edits[1].before[0]^=1;
 assert(nabu_cpu_apply_page(page,sizeof page,&rule,digest)==-1&&!memcmp(page,saved,sizeof page));
 rule.edits[1].before[0]^=1;rule.after_sha256[0]^=1;
 assert(nabu_cpu_apply_page(page,sizeof page,&rule,digest)==-1&&!memcmp(page,saved,sizeof page));
 rule.after_sha256[0]^=1;rule.edits[1].offset=20;
 assert(nabu_cpu_apply_page(page,sizeof page,&rule,digest)==-1&&!memcmp(page,saved,sizeof page));
 rule.edits[1].offset=16384;assert(nabu_cpu_apply_page(page,sizeof page,&rule,digest)==-1);
 rule.edits[1].offset=64;rule.edit_count=5;assert(nabu_cpu_apply_page(page,sizeof page,&rule,digest)==-1);
 rule.edit_count=2;assert(nabu_cpu_apply_page(page,4096,&rule,digest)==-1);
 return 0;
}
'''.replace('BEFORE_HASH', digest(original)).replace('AFTER_HASH', digest(after))
        with tempfile.TemporaryDirectory() as temp:
            src, binary = Path(temp) / 'policy.c', Path(temp) / 'policy'
            src.write_text(code)
            subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-I', str(POLICY), str(src), '-lcrypto', '-o', str(binary)],
                           check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True)

    def test_actual_hook_guards_and_bitmap_reload(self):
        kernel = (POLICY / 'kernel.h').read_text()
        patch = (ROOT / 'platform/nabu/macos26/patches/zzzzz-nabu-cpu-compositor.patch').read_text()
        for guard in ('"nabu-cpu-compositor", 0', 'csr_check(CSR_ALLOW_UNAUTHENTICATED_ROOT)',
                      '!original_validated', '!page->vmp_busy', 'page->vmp_wpmapped',
                      'page->vmp_dirty', 'sheng_recovery_ui_enabled', '== 3,'):
            self.assertIn(guard, kernel)
        self.assertIn('cache_sync_page(VM_PAGE_GET_PHYS_PAGE(page))', kernel)
        self.assertIn('&& !nabu_cpu_page_transformed', patch)
        self.assertNotIn('copyout(', kernel)
        self.assertNotIn('vmp_cs_validated =', kernel)
        self.assertNotIn('vmp_cs_tainted =', kernel)


if __name__ == '__main__':
    unittest.main()
