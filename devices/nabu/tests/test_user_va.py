#!/usr/bin/env python3
"""Compile the prepared board headers and production pmap range check.

The optional previous tree is a negative control for the real 456 GiB fault.
No expected TCR or VM ceiling is substituted for the selected source headers.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


def function(text, name):
    start = text.index('\n' + name + '(') + 1
    start = text.rfind('\n', 0, start - 1) + 1
    brace = text.index('{', start)
    depth, at = 1, brace + 1
    while depth:
        depth += (text[at] == '{') - (text[at] == '}')
        at += 1
    return text[start:at] + '\n'


def probe(xnu, work, name, macos=True):
    compiler = shutil.which('clang-17') or shutil.which('clang')
    macros = ['T0SZ_BOOT', 'T1SZ_BOOT', 'TCR_EL1_BOOT', 'ARM_4K_TT_L1_SHIFT',
              'ARM_4K_TT_L1_INDEX_MASK', 'MACH_VM_MAX_ADDRESS_RAW', 'ARM_TT_ROOT_SHIFT']
    code = '#include <arm64/proc_reg.h>\n#include <mach/arm/vm_param.h>\n'
    code += ''.join(f'NABU_EXPR_{m} {m}\n' for m in macros)
    command = [compiler, '-E', '-P', '-x', 'c', '-', '-D__arm64__=1',
               '-DKERNEL=1', '-DKERNEL_PRIVATE=1', '-DXNU_KERNEL_PRIVATE=1',
               '-D_MACH_MACHINE_H_=1', '-DARM64_BOARD_CONFIG_BCM2837=1',
               '-DXNU_PLATFORM_iPhoneOS=1', f'-DXNU_TARGET_OS_OSX={int(macos)}',
               '-I' + str(xnu / 'osfmk'), '-I' + str(xnu / 'pexpert')]
    expanded = subprocess.check_output(command, input=code, text=True)
    expressions = dict(re.findall(r'^NABU_EXPR_(\w+) (.+)$', expanded, re.M))
    assert set(expressions) == set(macros)
    text = (xnu / 'osfmk/arm/pmap/pmap.c').read_text()
    check = re.search(r'if \(__improbable\(\(v < pmap->min\).*?\n\t}', text, re.S)[0]
    root = re.search(r'const struct page_table_attr pmap_pt_attr_4k = \{.*?\.pta_root_level = (.*?),', text, re.S)[1]
    source = '#include <stdint.h>\n#include <stdio.h>\n#include <stdbool.h>\n'
    source += ''.join(f'#define {key} {value}\n' for key, value in expressions.items())
    source += '''
#define __unused __attribute__((unused))
#define __improbable(x) (x)
#define panic(...) return false
typedef uint64_t vm_map_size_t;
typedef struct { uint64_t min, max; } *pmap_t;
typedef unsigned pt_attr_t;
#define pt_attr_pagezero_size(p) 4096ULL
'''
    source += function(text, 'pmap_user_va_bits') + function(text, 'pmap_user_va_size')
    source += 'static bool admitted(pmap_t pmap, uint64_t v) {\n'
    source += '  __unused const pt_attr_t *pt_attr = 0;\n' + check + '\n return true;\n}\n'
    source += 'static unsigned root_level = ' + root + ';\n'
    source += '''
int main(void) {
    struct { uint64_t min, max; } storage = {0, 0};
    pmap_t p = (void *)&storage; p->max = pmap_user_va_size(p);
    uint64_t panic_va = 0x721a004000ULL;
    unsigned accepted = 0, total = 0;
    /* Every 2 MiB boundary in xzone, and its last addressable user page. */
    for (uint64_t v = 455ULL << 30; v < (512ULL << 30); v += 2ULL << 20) {
        total++; accepted += admitted(p, v);
    }
    printf("{\\"bits\\":%u,\\"t0sz\\":%llu,\\"t1sz\\":%llu,"
           "\\"tcr\\":%llu,\\"pmap_limit\\":%llu,\\"vm_limit\\":%llu,"
           "\\"root_level\\":%u,\\"root_bytes\\":%llu,\\"boot_root_shift\\":%llu,"
           "\\"panic_admitted\\":%u,\\"pagezero_rejected\\":%u,"
           "\\"pmap_end_rejected\\":%u,\\"vm_last_page_admitted\\":%u,"
           "\\"xzone_samples\\":%u,\\"xzone_admitted\\":%u}\\n",
           pmap_user_va_bits(p), T0SZ_BOOT, T1SZ_BOOT, TCR_EL1_BOOT,
           (unsigned long long)p->max, MACH_VM_MAX_ADDRESS_RAW, root_level,
           (((p->max - 1) >> ARM_4K_TT_L1_SHIFT) + 1) * 8ULL, (unsigned long long)ARM_TT_ROOT_SHIFT,
           admitted(p, panic_va), !admitted(p, 0), !admitted(p, p->max),
           admitted(p, MACH_VM_MAX_ADDRESS_RAW - 4096), total, accepted);
}
'''
    path, binary = work / (name + '.c'), work / name
    path.write_text(source)
    subprocess.run([compiler, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-parameter', str(path), '-o', str(binary)], check=True)
    return json.loads(subprocess.check_output([binary], text=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--xnu', required=True, type=Path)
    parser.add_argument('--previous-xnu', type=Path)
    parser.add_argument('--work', required=True, type=Path)
    args = parser.parse_args()
    args.xnu, args.work = args.xnu.resolve(), args.work.resolve()
    args.work.mkdir(parents=True, exist_ok=True)
    current = probe(args.xnu, args.work, 'current')
    assert (current['bits'], current['t0sz'], current['t1sz'], current['root_level'],
            current['root_bytes'], current['boot_root_shift']) == (39, 25, 25, 1, 4096, 30), current
    assert current['pmap_limit'] == 512 << 30
    assert current['vm_limit'] == (512 << 30) - (32 << 20)
    for key in ('panic_admitted', 'pagezero_rejected', 'pmap_end_rejected', 'vm_last_page_admitted'):
        assert current[key], key
    assert current['xzone_admitted'] == current['xzone_samples'] == 29184
    ios = probe(args.xnu, args.work, 'ios-control', macos=False)
    assert ios['bits'] == 38 and ios['root_bytes'] == 2048 and not ios['panic_admitted'], ios
    previous = None
    if args.previous_xnu:
        previous = probe(args.previous_xnu.resolve(), args.work, 'previous')
        assert previous['bits'] == 38 and not previous['panic_admitted']
        assert previous['vm_limit'] > previous['pmap_limit'], previous
    paths = ('osfmk/arm64/proc_reg.h', 'osfmk/mach/arm/vm_param.h', 'osfmk/arm/pmap/pmap.c')
    report = dict(passed=True, current=current, ios_control=ios, previous_negative_control=previous,
                  source_hashes={p: hashlib.sha256((args.xnu / p).read_bytes()).hexdigest() for p in paths},
                  hardware_tested=False)
    (args.work / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
