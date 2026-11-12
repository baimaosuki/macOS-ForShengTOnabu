"""Execute the deployed log hot paths with observable cache/owner adapters."""
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def test_retained_batches_preserve_partial_lines_wrap_and_snapshot():
    source = (ROOT / 'platform/nabu/macos26/log/retained_log.h').read_text()
    record = source[source.index('struct sheng_log_record {'):source.index('_Static_assert')]
    batch = source[source.index('static uint64_t sheng_log_clean_total;'):source.index('static uint64_t sheng_log_mask_async')]
    putc = source[source.index('void sheng_log_putc(char byte)'):source.index('volatile uint64_t sheng_panel_bound_rows;')]
    snapshot = source[source.index('unsigned sheng_log_snapshot_copy(void *destination, unsigned bytes)\n{'):source.index('void sheng_log_bind_panic_panel')]
    harness = r'''
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <assert.h>
#define SHENG_LOG_PREFIX 16384u
#define SHENG_LOG_TAIL 65536u
''' + record + r'''
static struct sheng_log_record sheng_log_buffer;
static unsigned clean_calls, captured, panel_calls;
static uint8_t published[sizeof(sheng_log_buffer)];
static uint32_t sheng_log_panel_writer;
static unsigned sheng_panel_ready;
static void sheng_log_clean(const volatile void *address, size_t bytes) {
 uintptr_t at=(uintptr_t)address-(uintptr_t)&sheng_log_buffer;
 assert(at+bytes<=sizeof(sheng_log_buffer));
 memcpy(published+at,(const void *)address,bytes); clean_calls++;
}
static uint64_t sheng_log_mask_async(void) {return 0;}
static void sheng_log_restore_async(uint64_t x) {(void)x;}
static void sheng_boot_progress_tick(void) {}
static void nabu_capture_append(const char *p,unsigned n) {(void)p;captured+=n;}
static void sheng_panel_putc(unsigned char b) {(void)b;panel_calls++;}
''' + batch + putc + snapshot + r'''
int main(void) {
 struct sheng_log_record snapshot;
 for(unsigned n=0;n<63;n++) sheng_log_putc('x');
 assert(clean_calls==0 && captured==63);
 sheng_log_putc('y'); assert(clean_calls==3);
 assert(((struct sheng_log_record *)published)->total==64);
 sheng_log_putc('z'); assert(clean_calls==3);
 assert(sheng_log_snapshot_copy(&snapshot,sizeof snapshot)==sizeof snapshot);
 assert(snapshot.total==65 && snapshot.prefix[64]=='z');
 assert(((struct sheng_log_record *)published)->tail[64]=='z');
 assert(!((struct sheng_log_record *)published)->writer);
 sheng_log_putc('q'); sheng_log_putc('\n');
 assert(((struct sheng_log_record *)published)->total==67);
 for(unsigned n=67;n<SHENG_LOG_TAIL+3;n++) sheng_log_putc((char)(n%127));
 sheng_log_snapshot_copy(&snapshot,sizeof snapshot);
 assert(snapshot.total==SHENG_LOG_TAIL+3);
 for(unsigned n=SHENG_LOG_TAIL;n<SHENG_LOG_TAIL+3;n++)
  assert(((struct sheng_log_record *)published)->tail[n%SHENG_LOG_TAIL]==n%127);
 unsigned before=clean_calls;sheng_log_buffer.panic_state=2;sheng_log_putc('P');
 assert(clean_calls>before && ((struct sheng_log_record *)published)->total==snapshot.total+1);
 sheng_log_buffer.writer=1; sheng_log_putc('D');
 assert(sheng_log_buffer.dropped==1 && sheng_log_buffer.writer==1);
 assert(!sheng_log_snapshot_copy(&snapshot,sizeof snapshot));
 assert(captured==SHENG_LOG_TAIL+5 && !panel_calls);
}
'''
    compiler = shutil.which('cc')
    assert compiler
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        (path / 'batch.c').write_text(harness)
        subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', str(path / 'batch.c'), '-o', str(path / 'batch')], check=True)
        subprocess.run([str(path / 'batch')], check=True)
    panic = source[source.index('void sheng_log_panic(uint64_t caller'):]
    assert panic.index('sheng_log_clean(r, sizeof(*r));') < panic.index('r->panic_state, &expected')


def test_panel_glyph_has_one_lease_and_handoff_blocks_pixels():
    source = (ROOT / 'platform/nabu/macos26/log/panel_console.h').read_text()
    function = source[source.index('static void sheng_panel_putc('):source.index('static void sheng_panel_pin_panic(')]
    function = function.replace('__asm__ volatile ("dsb sy" ::: "memory");', '')
    harness = r'''
#include <stdint.h>
#include <assert.h>
#define SHENG_FB_BOOT 0u
struct {unsigned owner;} sheng_framebuffer;
static unsigned sheng_panel_ready=1, begin_count, end_count, newline_count;
static uint64_t sheng_panel_witness, sheng_panel_characters;
static unsigned char sheng_panel_font[4096];
static struct {uintptr_t base;unsigned stride,columns,rows,scale,x,y,pinned;} sheng_panel;
static int sheng_fb_begin(unsigned owner,uint64_t *saved) {*saved=0;begin_count++;return sheng_framebuffer.owner==owner;}
static void sheng_fb_end(uint64_t saved) {(void)saved;end_count++;}
static void sheng_panel_newline(void) {newline_count++;}
''' + function + r'''
int main(void) {
 uint32_t pixels[32*16];for(unsigned n=0;n<32*16;n++) pixels[n]=0x12345678;
 sheng_panel.base=(uintptr_t)pixels;sheng_panel.stride=64;sheng_panel.columns=1;
 sheng_panel.rows=1;sheng_panel.scale=2;
 sheng_panel_font['A'*16]=1;sheng_panel_putc('A');
 assert(begin_count==1 && end_count==1 && sheng_panel_characters==1 && newline_count==1);
 assert(pixels[0]==0xffffffff && pixels[1]==0xffffffff && pixels[2]==0xff000000);
 sheng_framebuffer.owner=1;pixels[0]=0x12345678;sheng_panel_putc('A');
 assert(pixels[0]==0x12345678 && begin_count==1 && end_count==1);
}
'''
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        (path / 'panel.c').write_text(harness)
        subprocess.run([shutil.which('cc'), '-std=c11', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', str(path / 'panel.c'), '-o', str(path / 'panel')], check=True)
        subprocess.run([str(path / 'panel')], check=True)
