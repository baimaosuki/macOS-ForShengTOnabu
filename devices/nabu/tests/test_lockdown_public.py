#!/usr/bin/env python3
"""Exercise the prepared production public-state handler and eight-byte ABI."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--xnu', required=True, type=Path)
p.add_argument('--output', required=True, type=Path)
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=True)
(a.output / 'verification.json').unlink(missing_ok=True)
source = (a.xnu / 'bsd/kern/lockdown_mode.c').read_text()
handler = re.search(r'static int\nsysctl_lockdown_mode_state_public SYSCTL_HANDLER_ARGS\n\{.*?\n\}', source, re.S)
assert handler, 'prepared kernel lacks the production public handler'
registration = re.search(r'SYSCTL_PROC\(_security_mac, OID_AUTO, lockdown_mode_state_public,.*?\);', source, re.S)
assert registration and 'CTLTYPE_QUAD | CTLFLAG_RD | CTLFLAG_LOCKED' in registration[0]
assert 'CTLFLAG_WR' not in registration[0] and 'CTLFLAG_RW' not in registration[0]
assert 'SYSCTL_INT(_security_mac, OID_AUTO, lockdown_mode_state,' in source
fixture = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static int native_state, reads;
static int get_lockdown_mode_state(void) { ++reads; return native_state; }
struct request { unsigned char bytes[16]; size_t capacity, length; int error; };
static int sysctl_out(struct request *r, const void *p, size_t n) {
  r->length=n;
  if(r->error) return r->error;
  if(r->capacity<n) return 12;
  memcpy(r->bytes,p,n); return 0;
}
#define SYSCTL_HANDLER_ARGS (void *oidp, void *arg1, int arg2, struct request *req)
#define SYSCTL_OUT(r,p,n) sysctl_out(r,p,n)
'''
fixture += handler[0]
fixture += r'''
int main(int argc,char **argv) {
  assert(argc==2); FILE *f=fopen(argv[1],"wb"); assert(f);
  int states[]={0,1,0};
  for(unsigned i=0;i<3;i++) {
    struct request r={.capacity=8}; native_state=states[i];
    assert(sysctl_lockdown_mode_state_public(0,0,0,&r)==0);
    uint64_t value=99; memcpy(&value,r.bytes,8);
    assert(r.length==8 && value==(uint64_t)states[i] && reads==(int)i+1);
    assert(fwrite(&value,8,1,f)==1);
  }
  fclose(f);
  struct request short_read={.capacity=4};
  assert(sysctl_lockdown_mode_state_public(0,0,0,&short_read)==12 && short_read.length==8);
  struct request fault={.capacity=8,.error=14};
  assert(sysctl_lockdown_mode_state_public(0,0,0,&fault)==14);
  puts("PASS: production public sysctl: eight-byte 0/1/0 native state, read-only registration, short-buffer and copyout errors");
}
'''
path = a.output / 'handler.c'
path.write_text(fixture)
cc = shutil.which('clang') or shutil.which('gcc')
assert cc
exe = a.output / 'handler'
subprocess.run([cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                str(path), '-o', str(exe)], check=True)
run = subprocess.run([str(exe), str(a.output / 'values.bin')], text=True, capture_output=True,
                     env=dict(os.environ, ASAN_OPTIONS='abort_on_error=1'))
(a.output / 'handler.log').write_text(run.stdout + run.stderr)
print(run.stdout + run.stderr, end='', flush=True)
run.check_returncode()
(a.output / 'verification.json').write_text(json.dumps(dict(passed=True, bytes=8,
    native_states=[0, 1, 0], read_only_registration=True, original_integer_interface_preserved=True,
    short_buffer_error_preserved=True, copyout_error_preserved=True,
    sanitizers=['address', 'undefined']), indent=2) + '\n')
