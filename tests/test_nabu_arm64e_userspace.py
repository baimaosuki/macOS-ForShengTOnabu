# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile the actual patched no-PAuth decoder/loader functions on the host."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'platform/nabu/macos26/patches/zzz-nabu-arm64e-userspace.patch'


def function(text, name):
    start = text.index('\n' + name + '(') + 1
    start = text.rfind('\n', 0, start - 1) + 1
    brace = text.index('{', start)
    depth = 1
    at = brace + 1
    while depth:
        depth += (text[at] == '{') - (text[at] == '}')
        at += 1
    return text[start:at] + '\n'


class Arm64eUserspace(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.tree = Path(cls.temp.name)
        # Materialize only patch inputs; never rewrite the pinned checkout.
        for part in PATCH.read_text().split('--- a/')[1:]:
            file = part.splitlines()[0]
            source = ROOT / 'sources/macos26/xnu' / file
            dest = cls.tree / file
            dest.parent.mkdir(parents=True, exist_ok=True)
            if source.exists(): dest.write_bytes(source.read_bytes())
            elif file.endswith('nabu_v82_emul.c'):
                patch = (ROOT / 'platform/nabu/macos26/patches/zz-nabu-v82-crypto-emulation.patch').read_text()
                section = patch.split('--- a/osfmk/arm64/nabu_v82_emul.c\n')[1].split('--- a/osfmk/arm64/nabu_v82_emul.h\n')[0]
                dest.write_text('\n'.join(s[1:] for s in section.splitlines() if s.startswith('+') and not s.startswith('+++')) + '\n')
        subprocess.run(['git', 'apply', '--unsafe-paths', str(PATCH)], cwd=cls.tree, check=True, capture_output=True)
        # BSD imports osfmk's EXPORT_HDRS; it has no -I SRCROOT/osfmk.
        # Evaluate the actual export list with make and stage only its files.
        makefile = (cls.tree / 'osfmk/arm64/Makefile').read_text()
        makefile = '\n'.join(line for line in makefile.splitlines() if not line.startswith('include '))
        makefile += '\nprint-exports:\n\t@printf "%s\\n" "$(EXPORT_MD_DIR)" "$(EXPORT_MD_LIST)"\n'
        result = subprocess.run(['make', '--no-print-directory', '-f', '-', 'print-exports'],
            input=makefile, text=True, check=True, capture_output=True)
        directory, names = result.stdout.strip().split('\n')
        cls.exports = cls.tree / 'EXPORT_HDRS/osfmk'
        for name in names.split():
            source = cls.tree / 'osfmk/arm64' / name
            if not source.exists(): source = ROOT / 'sources/macos26/xnu/osfmk/arm64' / name
            target = cls.exports / directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def compile_run(self, name, code, extra=()):
        source, binary = self.tree / (name + '.c'), self.tree / name
        source.write_text(code)
        compiler = next((shutil.which(name) for name in ('clang', 'clang-19', 'clang-18', 'clang-17') if shutil.which(name)), None)
        if not compiler: self.skipTest('requires Clang for feature-gated source compilation')
        result = subprocess.run([compiler, '-std=gnu11', '-Wall', '-Wextra', '-Werror',
            '-Wno-unused-function', '-Wno-unused-parameter', '-Wno-unused-variable',
            '-I', str(self.exports), *extra, str(source), '-o', str(binary)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([str(binary)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_bsd_component_can_import_both_exported_headers(self):
        # pexpert is a separate component import, as in the BSD build command.
        # Its board selector is stubbed here; the two arm64 headers must come
        # solely from the real osfmk export list, never its source directory.
        pexpert = self.tree / 'EXPORT_HDRS/pexpert'
        board = pexpert / 'pexpert/arm64/board_config.h'
        board.parent.mkdir(parents=True, exist_ok=True)
        board.write_text('#define ARM64_BOARD_CONFIG_NABU 1\n')
        self.compile_run('bsd_headers', r'''
#include <arm64/nabu_arm64e_compat.h>
#include <arm64/nabu_nopac.h>
_Static_assert(NABU_ARM64E_COMPAT == 1, "NABU macOS no-PAuth policy");
int main(void) { return nabu_nopac_canonical(0xabcd000000001234ULL, true) != 0x1234; }
''', ('-D__arm64__=1', '-DXNU_TARGET_OS_OSX=1', '-I', str(pexpert)))

    def test_actual_nopac_state_machine_preserves_sources_and_failure_atomicity(self):
        self.compile_run('nopac', r'''
#include <assert.h>
#include <string.h>
#include "arm64/nabu_nopac.h"
static unsigned reads;
static uint64_t wanted, loaded;
static bool read64(uint64_t address, uint64_t *value, void *context) {
    (void)context; reads++; if (address != wanted) return false; *value = loaded; return true;
}
int main(void) {
    struct nabu_nopac_frame f = { .pc=0x1000, .sp=0x8000 }, before;
    f.x[8]=0xabcd000012345678ULL; f.x[9]=0x7777;
    /* Every PAC opcode preserves Xd, not Xn (including zero modifiers). */
    for (unsigned opc=0; opc<12; opc++) if (opc<4 || opc>=8) {
        before=f; uint32_t op=0xdac10000U|(opc<<10)|((opc>=8?31:9)<<5)|8;
        assert(nabu_nopac_step(&f,op,true,read64,0));
        assert(f.x[8]==before.x[8] && f.x[9]==before.x[9] && f.pc==before.pc+4);
    }
    assert(nabu_nopac_step(&f,0xdac11000U|(9<<5)|8,true,read64,0));
    assert(f.x[8]==0x12345678);
    f.x[8]=0xabcdfe0012345678ULL;
    assert(nabu_nopac_step(&f,0xdac143e8U,false,read64,0));
    assert(f.x[8]==0xfffffe0012345678ULL);
    before=f; assert(!nabu_nopac_step(&f,0xdac12008U,true,read64,0)); assert(!memcmp(&f,&before,sizeof f));
    f.pc=0x1000; f.x[30]=0xbeef000001234560ULL;
    assert(nabu_nopac_step(&f,0xd73f0800U|(30<<5)|9,true,read64,0));
    assert(f.pc==0x1234560 && f.x[30]==0x1004); /* target x30 captured before link */
    f.x[30]=0xffff000001020304ULL;
    assert(nabu_nopac_step(&f,0xd65f0fffU,true,read64,0)); assert(f.pc==0x1020304);
    f.pc=0x1000; f.x[9]=0xabcd000000002000ULL; wanted=0x2008;loaded=0xfedcba9876543210ULL;
    assert(nabu_nopac_step(&f,0xf8200400U|(1<<12)|(9<<5)|8,true,read64,0));
    assert(f.x[8]==loaded && f.x[9]==0xabcd000000002000ULL && f.pc==0x1004);
    f.sp=0x4000; wanted=0x3ff8;
    assert(nabu_nopac_step(&f,0xf8200400U|(1<<22)|(511<<12)|(1<<11)|(31<<5)|8,true,read64,0));
    assert(f.sp==0x3ff8 && f.x[8]==loaded);
    before=f; assert(!nabu_nopac_step(&f,0xf8200400U|(1<<11)|(9<<5)|9,true,read64,0));assert(!memcmp(&f,&before,sizeof f));
    wanted=0xdead;before=f;assert(!nabu_nopac_step(&f,0xf8200400U|(9<<5)|8,true,read64,0));assert(!memcmp(&f,&before,sizeof f));
    before=f;assert(!nabu_nopac_step(&f,0xd71f0800U|(31<<5),true,read64,0));assert(!memcmp(&f,&before,sizeof f));
    assert(reads==3);
    return 0;
}
''')

    def test_data_auth_preserves_objc_rw_flag_and_top_byte_without_tagging_addresses(self):
        self.compile_run('objc_top_byte', r'''
#include <assert.h>
#include <string.h>
#include "arm64/nabu_nopac.h"
static uint64_t wanted;
static bool read64(uint64_t address, uint64_t *value, void *context) {
    (void)context; if (address != wanted) return false; *value = 0x80080001; return true;
}
int main(void) {
    struct nabu_nopac_frame f = { .pc=0x1000 }, before;
    /* AUTDA/AUTDB/AUTDZA/AUTDZB/XPACD retain every TBI data tag. */
    for (unsigned tag=0; tag<256; tag++) for (unsigned i=0; i<5; i++) {
        unsigned opc=(unsigned[]){6,7,14,15,17}[i], rn=opc>=8?31:21;
        uint64_t input=((uint64_t)tag<<56)|0x00cd0007d4c00ce4ULL;
        f.x[16]=input;f.x[21]=0xc93a0001ef3ba788ULL;before=f;
        assert(nabu_nopac_step(&f,0xdac10000U|(opc<<10)|(rn<<5)|16,true,0,0));
        assert(f.x[16]==(((uint64_t)tag<<56)|0x7d4c00ce4ULL));
        assert(f.x[21]==before.x[21] && f.pc==before.pc+4);
    }
    /* Real libobjc class-data pattern: bit63 distinguishes class_rw_t
     * from class_ro_t. Clearing it makes safe_ro read rw flags as a pointer. */
    f.x[16]=0x80000007d4c00ce4ULL; f.x[21]=0xc93a0001ef3ba788ULL;
    assert(nabu_nopac_step(&f,0xdac11eb0U,true,0,0)); /* autdb x16,x21 */
    assert(f.x[16]&(1ULL<<63));
    f.x[17]=f.x[16];
    assert(nabu_nopac_step(&f,0xdac147f1U,true,0,0)); /* xpacd x17 */
    assert(f.x[16]==f.x[17]);
    assert((f.x[16]&0x0f007ffffffffff8ULL)==0x7d4c00ce0ULL);
    /* Instruction auth/strip uses an instruction address, not a data tag. */
    for (unsigned i=0; i<5; i++) {
        unsigned opc=(unsigned[]){4,5,12,13,16}[i], rn=opc>=8?31:21;
        f.x[16]=0xabcd000182dd5e1cULL;
        assert(nabu_nopac_step(&f,0xdac10000U|(opc<<10)|(rn<<5)|16,true,0,0));
        assert(f.x[16]==0x182dd5e1cULL);
    }
    /* A data tag may reach an authenticated load/branch. Only their effective
     * address is untagged; loaded data and the source register stay intact. */
    f.x[9]=0x80000007d4c00800ULL;wanted=0x7d4c00820ULL;
    assert(nabu_nopac_step(&f,0xf8200400U|(4<<12)|(9<<5)|8,true,read64,0));
    assert(f.x[8]==0x80080001 && f.x[9]==0x80000007d4c00800ULL);
    f.x[9]=0x8000000182dd5e1cULL;
    assert(nabu_nopac_step(&f,0xd71f0800U|(9<<5),true,0,0));
    assert(f.pc==0x182dd5e1cULL && f.x[9]==0x8000000182dd5e1cULL);
    return 0;
}
''')

    def test_actual_grade_accepts_only_current_version_and_leaves_other_boards(self):
        text = (self.tree / 'bsd/dev/arm/kern_machdep.c').read_text()
        functions = function(text, 'grade_arm64e_binary') + function(text, 'ml_grade_binary')
        common = r'''
#include <assert.h>
#include <stdbool.h>
#define __unused __attribute__((unused))
#define __arm64__ 1
#define CPU_ARCH_ABI64 0x1000000
#define CPU_TYPE_ARM64 0x100000c
#define CPU_SUBTYPE_ARM64_ALL 0
#define CPU_SUBTYPE_ARM64_V8 1
#define CPU_SUBTYPE_ARM64E 2
#define CPU_SUBTYPE_PTRAUTH_ABI 0x80000000U
#define CPU_SUBTYPE_ARM64_PTR_AUTH_VERSION(x) (((x)>>24)&15)
#define CPU_SUBTYPE_ARM64_PTR_AUTH_MAX_PREFERRED_VERSION 1
#define CPU_SUBTYPE_ARM64_PTR_AUTH_CURRENT_VERSION 0
#define XNU_TARGET_OS_IOS 0
#define XNU_TARGET_OS_XR 0
#define cpu_subtype() 1
#define cpu_subtype32() 0
typedef int cpu_type_t;
typedef int cpu_subtype_t;
'''
        for mode in (0, 1):
            body = f'\n#define NABU_ARM64E_COMPAT {mode}\n' + functions + f'''
int main(void) {{
 assert(ml_grade_binary(CPU_TYPE_ARM64,0,0,true)==9);
 assert(ml_grade_binary(CPU_TYPE_ARM64,1,0,true)==10);
 assert(ml_grade_binary(CPU_TYPE_ARM64,2,0x80000000U,true)=={12 if mode else 0});
 assert(!ml_grade_binary(CPU_TYPE_ARM64,2,0,true));
 assert(!ml_grade_binary(CPU_TYPE_ARM64,2,0x81000000U,true));
 assert(!ml_grade_binary(CPU_TYPE_ARM64,3,0x80000000U,true));
 assert(!ml_grade_binary(7,2,0x80000000U,true));
 return 0;
}}
'''
            self.compile_run('grade' + str(mode), common + body)

    def test_actual_shared_cache_v3_v5_mixed_chains(self):
        text=(self.tree/'osfmk/vm/vm_shared_region.c').read_text()
        common=r'''
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
#define __unused __attribute__((unused))
#define NABU_ARM64E_COMPAT 1
#define CONFIG_SPTM 0
#define KERN_SUCCESS 0
#define KERN_FAILURE 5
#define DYLD_CACHE_SLIDE_V3_PAGE_ATTR_NO_REBASE 0xffff
#define DYLD_CACHE_SLIDE_V5_PAGE_ATTR_NO_REBASE 0xffff
typedef int kern_return_t; typedef uintptr_t vm_offset_t; typedef uint64_t mach_vm_offset_t;
struct vm_shared_region_slide_info_entry_v3 { uint32_t version,page_size,page_starts_count;uint64_t value_add;uint16_t page_starts[1]; };
struct vm_shared_region_slide_info_entry_v5 { uint32_t version,page_size,page_starts_count;uint64_t value_add;uint16_t page_starts[1]; };
typedef struct vm_shared_region_slide_info_entry_v3 *vm_shared_region_slide_info_entry_v3_t;
typedef struct vm_shared_region_slide_info_entry_v5 *vm_shared_region_slide_info_entry_v5_t;
union entries { struct vm_shared_region_slide_info_entry_v3 v3;struct vm_shared_region_slide_info_entry_v5 v5; };
struct info {union entries *si_slide_info_entry;uint32_t si_slide;};typedef struct info *vm_shared_region_slide_info_t;
'''
        body=r'''
int main(void) {
 uint64_t page[32]={0};union entries e={0};struct info si={&e,0x4000};
 e.v3.page_starts_count=1;e.v3.value_add=0x180000000ULL;e.v3.page_starts[0]=0;
 page[0]=(1ULL<<63)|(1ULL<<51)|0x1234;page[1]=0x2345;
 assert(!vm_shared_region_slide_page_v3(&si,(uintptr_t)page,0x180000000,0,0));
 assert(page[0]==0x180005234ULL && page[1]==0x6345);
 assert(vm_shared_region_slide_page_v3(&si,(uintptr_t)page,0,1,0)==KERN_FAILURE);
 e.v5.page_starts_count=1;e.v5.value_add=0x180000000ULL;e.v5.page_starts[0]=0;
 page[0]=(1ULL<<63)|(1ULL<<52)|0x1234;page[1]=(0xabULL<<34)|0x3456;
 assert(!vm_shared_region_slide_page_v5(&si,(uintptr_t)page,0x180000000,0,0));
 assert(page[0]==0x180005234ULL && page[1]==0xab00000180007456ULL);
 assert(vm_shared_region_slide_page_v5(&si,(uintptr_t)page,0,1,0)==KERN_FAILURE);
 return 0;
}
'''
        self.compile_run('shared',common+function(text,'vm_shared_region_slide_page_v3')+function(text,'vm_shared_region_slide_page_v5')+body)

    def test_actual_dyld_pager_formats_and_errors_propagate(self):
        text=(self.tree/'osfmk/vm/vm_dyld_pager.c').read_text()
        block=text[text.index('#if defined(HAS_APPLE_PAC) || NABU_ARM64E_COMPAT\n#if'):text.index('/*\n * Handle dyld fixups for a page.')]
        # Actual structs from the public headers are represented without
        # unrelated kernel annotations; functions and control flow are verbatim.
        common=r'''
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#define NABU_ARM64E_COMPAT 1
#define PAGE_SIZE 4096
#define PAGE_SHIFT 12
#define PAGE_MASK 4095
#define KERN_SUCCESS 0
#define KERN_FAILURE 5
#define DYLD_CHAINED_PTR_ARM64E 1
#define DYLD_CHAINED_PTR_64 2
#define DYLD_CHAINED_PTR_32 3
#define DYLD_CHAINED_PTR_64_OFFSET 6
#define DYLD_CHAINED_PTR_ARM64E_USERLAND 9
#define DYLD_CHAINED_PTR_ARM64E_USERLAND24 12
#define DYLD_CHAINED_PTR_ARM64E_SHARED_CACHE 13
#define DYLD_CHAINED_PTR_START_NONE 0xffff
#define KDBG_TRIAGE_EVENTID(...) 0
#define ktriage_record(...) ((void)0)
#define thread_tid(...) 0
#define current_thread() 0
#define panic(...) abort()
typedef int kern_return_t;typedef uintptr_t vm_offset_t;
static bool panic_on_dyld_issue=false;
struct mwl_info_hdr {uint32_t mwli_version;uint16_t mwli_page_size,mwli_pointer_format;uint32_t mwli_binds_offset,mwli_binds_count,mwli_chains_offset,mwli_chains_size;uint64_t mwli_slide,mwli_image_address;};
struct dyld_chained_starts_in_image {uint32_t seg_count,seg_info_offset[1];};
struct dyld_chained_starts_in_segment {uint32_t size;uint16_t page_size,pointer_format;uint64_t segment_offset;uint32_t max_valid_pointer;uint16_t page_count,page_start[1];};
struct dyld_pager {void *dyld_link_info;uint32_t dyld_link_info_size;};typedef struct dyld_pager *dyld_pager_t;
static bool _delta_ptr_within_page_nospec(uint64_t **p,uint64_t delta,bool *cross,uintptr_t va) {
 (void)va;*cross=(((uintptr_t)*p+delta)>>12)!=((uintptr_t)*p>>12);if(*cross)return false;*p=(uint64_t*)((uintptr_t)*p+delta);return delta!=0;
}
static void fixupPage64(uint64_t a,uintptr_t b,void*c,void*d,uint32_t e,bool f){(void)a;(void)b;(void)c;(void)d;(void)e;(void)f;}
static void fixupPage32(uint64_t a,uintptr_t b,void*c,uint32_t d,void*e,uint32_t f){(void)a;(void)b;(void)c;(void)d;(void)e;(void)f;}
'''
        body=r'''
int main(void) {
 uint8_t blob[256]={0};uint64_t *page=aligned_alloc(PAGE_SIZE,PAGE_SIZE);assert(page);
 struct mwl_info_hdr *h=(void*)blob;h->mwli_page_size=4096;h->mwli_binds_offset=48;h->mwli_binds_count=2;h->mwli_chains_offset=64;h->mwli_image_address=0x100000000ULL;h->mwli_slide=0x4000;
 uint64_t *bind=(void*)(blob+48);bind[0]=0x12340000;bind[1]=0x56780000;
 struct dyld_chained_starts_in_image *starts=(void*)(blob+64);starts->seg_count=1;starts->seg_info_offset[0]=8;
 struct dyld_chained_starts_in_segment *seg=(void*)(blob+72);seg->size=sizeof(*seg);seg->page_size=4096;seg->page_count=1;seg->page_start[0]=0;
 struct dyld_pager pager={blob,sizeof blob};
 for(unsigned format=0;format<3;format++) {
  h->mwli_pointer_format=(uint16_t[]){1,9,12}[format];memset(page,0,PAGE_SIZE);
  page[0]=(1ULL<<63)|(1ULL<<51)|0x1234; /* auth rebase */
  page[1]=(1ULL<<63)|(1ULL<<62)|(1ULL<<51)|1; /* auth bind */
  page[2]=(1ULL<<62)|(1ULL<<51)|(0x7ffffULL<<32)|0; /* nonauth bind addend -1 */
  page[3]=0x3456;
  assert(!fixup_page((uintptr_t)page,h->mwli_image_address,&pager));
  assert(page[0]==0x100001234ULL && page[1]==bind[1] && page[2]==bind[0]-1);
  assert(page[3]==0x3456+(format?0x100000000ULL:0x4000));
 }
 h->mwli_pointer_format=12;page[0]=(1ULL<<63)|(1ULL<<62)|2;
 assert(fixup_page((uintptr_t)page,h->mwli_image_address,&pager)==KERN_FAILURE);
 page[0]=(1ULL<<63)|(1ULL<<49)|0x1234; /* unsupported B key */
 assert(fixup_page((uintptr_t)page,h->mwli_image_address,&pager)==KERN_FAILURE);
 page[0]=(1ULL<<63)|(512ULL<<51)|0x1234; /* next pointer crosses page */
 assert(fixup_page((uintptr_t)page,h->mwli_image_address,&pager)==KERN_FAILURE);
 h->mwli_pointer_format=13;page[0]=(1ULL<<63)|(1ULL<<52)|0x1234;page[1]=(0xabULL<<34)|0x3456;
 assert(!fixup_page((uintptr_t)page,h->mwli_image_address,&pager));
 assert(page[0]==0x100001234ULL && page[1]==0xab00000100003456ULL);
 page[0]=(1ULL<<63)|(512ULL<<52)|0x1234;
 assert(fixup_page((uintptr_t)page,h->mwli_image_address,&pager)==KERN_FAILURE);
 h->mwli_pointer_format=99;assert(fixup_page((uintptr_t)page,h->mwli_image_address,&pager)==KERN_FAILURE);
 free(page);return 0;
}
'''
        self.compile_run('pager',common+block+function(text,'fixup_page')+body)
        apple = common.replace('#define NABU_ARM64E_COMPAT 1', r'''
#define NABU_ARM64E_COMPAT 0
#define HAS_APPLE_PAC 1
typedef enum {ptrauth_key_asia=0,ptrauth_key_asib=1,ptrauth_key_asda=2,ptrauth_key_asdb=3} ptrauth_key;
#define __builtin_ptrauth_blend_discriminator(loc,div) ((uint64_t)(uintptr_t)(loc)^(div))
static bool arm_user_jop_disabled(void){return false;}
static uintptr_t pmap_sign_user_ptr(void *address,ptrauth_key key,uint64_t div,uint64_t id){(void)key;(void)div;(void)id;return (uintptr_t)address;}
''').replace('struct dyld_pager {void *dyld_link_info;uint32_t dyld_link_info_size;}',
             'struct dyld_pager {void *dyld_link_info;uint32_t dyld_link_info_size;uint64_t dyld_a_key;}')
        apple_body = body.replace('struct dyld_pager pager={blob,sizeof blob};',
                                  'struct dyld_pager pager={blob,sizeof blob,1};')
        self.compile_run('pager_apple',apple+block+function(text,'fixup_page')+apple_body,
                         ('-Wenum-conversion',))

    def test_exec_policy_and_source_hook_are_linked_to_real_paths(self):
        text=(self.tree/'bsd/kern/kern_exec.c').read_text()
        self.assertIn('imgp->ip_flags |= IMGPF_NOJOP;',text)
        self.assertIn('#if __has_feature(ptrauth_calls) || NABU_ARM64E_COMPAT\n\tif (is_arm64e_running_as_arm64(imgp))',text)
        emul=(self.tree/'osfmk/arm64/nabu_v82_emul.c').read_text()
        self.assertLess(emul.index('if (nabu_nopac_opcode(insn))'),emul.index('nabu_v82_core(&op'))
        self.assertIn('if (!nabu_nopac_step',emul)
        self.assertNotIn('nabu_area',emul)
        self.assertNotIn('0xfffffe',emul)
        self.assertNotIn('nabu_v82_canon',emul)

    def test_actual_exception_adapter_preserves_el0_el1_frame_and_refuses_bad_load(self):
        text=(self.tree/'osfmk/arm64/nabu_v82_emul.c').read_text()
        core=text[text.index('typedef struct nabu_v82_vec'):text.index(' * END NABU_V82_CORE')]
        core=core[:core.rfind('/*')]
        adapter=function(text,'nabu_v82_emul_sync_exception')
        helpers=''.join(function(text,name) for name in (
            'nabu_v82_read_vec','nabu_v82_write_vec','nabu_v82_fetch','nabu_v82_read_pointer'))
        common=r'''
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <assert.h>
#include "arm64/nabu_nopac.h"
#define __improbable(x) (x)
#define printf(...) ((void)0)
#define ESR_EC(x) (x)
#define ESR_EC_UNCATEGORIZED 0
#define PSR64_IS_USER(x) ((x)==0)
#define NABU_V82_INSN_SIZE 4
typedef uint64_t user_addr_t;
typedef __uint128_t uint128_t;
typedef struct {uint64_t x[29],fp,lr,sp,pc;uint32_t cpsr;} arm_saved_state64_t;
typedef arm_saved_state64_t arm_saved_state_t;
typedef struct {struct {uint128_t q[32];} v;} arm_neon_saved_state64_t;
typedef struct {arm_saved_state_t ss;struct {arm_neon_saved_state64_t ns_64;} ns;} arm_context_t;
#define saved_state64(x) (x)
#define neon_state64(x) (&(x)->ns_64)
#define get_saved_state_pc(x) ((x)->pc)
#define get_saved_state_cpsr(x) ((x)->cpsr)
#define set_saved_state_pc(x,v) ((x)->pc=(v))
#define is_saved_state64(x) true
#define is_neon_saved_state64(x) true
#define nabu_v82_emul_is_enabled() true
static bool nabu_v82_emul_reported_emulated,nabu_v82_emul_reported_unsupported;
static uint32_t instruction;
static bool fail_load;
static int copyin(uint64_t address,void *target,unsigned long size) {
 if(size==4){assert(address==0x1000);memcpy(target,&instruction,4);return 0;}
 if(fail_load)return 1;assert(address==0x2000);uint64_t value=0xabcdabcd01234567ULL;memcpy(target,&value,8);return 0;
}
static int copyin_kern(uint64_t address,char *target,unsigned long size){return copyin(address,target,size);}
'''
        body=r'''
int main(void) {
 for(unsigned mode=0;mode<2;mode++) {
  arm_context_t c={0}; c.ss.pc=0x1000;c.ss.cpsr=mode?4:0;c.ss.x[8]=0x12345678;c.ss.x[9]=0x7777;
  for(unsigned i=0;i<32;i++)c.ns.ns_64.v.q[i]=((uint128_t)i<<96)|i;
  arm_context_t before=c;instruction=0xdac10128; /* PACIA x8,x9 */
  assert(nabu_v82_emul_sync_exception(&c,0));assert(c.ss.pc==0x1004);c.ss.pc=0x1000;
  assert(!memcmp(&c,&before,sizeof c));
  instruction=0xf8200400U|(9<<5)|8;c.ss.x[9]=0xabcd000000002000ULL;fail_load=true;before=c;
  assert(!nabu_v82_emul_sync_exception(&c,0));assert(!memcmp(&c,&before,sizeof c));
  fail_load=false;assert(nabu_v82_emul_sync_exception(&c,0));assert(c.ss.x[8]==0xabcdabcd01234567ULL);
  assert(!memcmp(&c.ns,&before.ns,sizeof c.ns));
  if (!mode) {
   c.ss.pc=0x1000;c.ss.x[16]=0x80cd0007d4c00ce4ULL;c.ss.x[21]=0xc93a0001ef3ba788ULL;
   before=c;instruction=0xdac11eb0U; /* AUTDB x16,x21 from libobjc */
   assert(nabu_v82_emul_sync_exception(&c,0));
   before.ss.x[16]=0x80000007d4c00ce4ULL;before.ss.pc=0x1004;
   assert(!memcmp(&c,&before,sizeof c));
  }
 }
 return 0;
}
'''
        self.compile_run('adapter',common+core+helpers+adapter+body)


if __name__=='__main__':unittest.main()
