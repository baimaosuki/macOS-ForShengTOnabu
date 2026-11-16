#!/usr/bin/env python3
"""Replay the prepared production v5 pager on original 16 KiB cache pages.

Requires a fully prepared XNU tree, so this covers the selected OS and board
patches together. The unmodified full-page routine is the comparison oracle.
"""
import argparse
import ctypes
import hashlib
import json
import mmap
from pathlib import Path
import shutil
import struct
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


COMMON = r'''
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
#define __unused __attribute__((unused))
#ifndef NABU_ARM64E_COMPAT
#define NABU_ARM64E_COMPAT 1
#endif
#define CONFIG_SPTM 0
#define PAGE_SIZE 4096
#define VM_SR_PART_NONE 0xffffU
#define KERN_SUCCESS 0
#define KERN_FAILURE 5
#define DYLD_CACHE_SLIDE_V5_PAGE_ATTR_NO_REBASE 0xffffU
typedef int kern_return_t;
typedef uintptr_t vm_offset_t;
typedef uint64_t mach_vm_offset_t;
struct v5 { uint32_t version,page_size,page_starts_count; uint64_t value_add; uint16_t page_starts[1]; };
typedef struct v5 *vm_shared_region_slide_info_entry_v5_t;
union entries { struct v5 v5; };
struct info { union entries *si_slide_info_entry; uint32_t si_slide; };
typedef struct info *vm_shared_region_slide_info_t;
'''

HARNESS = r'''
/* Returns the first mismatching byte + 1; a negative result is a pager error. */
int replay(const uint8_t *input, uint32_t first, uint32_t slide, uint64_t value_add,
           uint64_t userva, unsigned order, uint64_t *bad_words) {
    uint8_t whole[16384], split[16384];
    union entries e = { .v5 = {5,16384,1,value_add,{(uint16_t)first}} };
    struct info si = {&e,slide};
    memcpy(whole,input,sizeof whole); memcpy(split,input,sizeof split);
    if (vm_shared_region_slide_page_v5(&si,(uintptr_t)whole,userva,0,0)) return -1;
    static const unsigned orders[3][4] = {{0,1,2,3},{3,1,0,2},{2,0,3,1}};
    for (unsigned i=0;i<4;i++) {
        unsigned part=orders[order%3][i]; uint32_t start=first,next;
        /* Replay the real chain-only operation on earlier source parts. */
        while (start!=VM_SR_PART_NONE && start/4096<part) {
            unsigned earlier=start/4096;
            uint8_t source[4096]; memcpy(source,input+earlier*4096,4096);
            int kr=vm_shared_region_slide_part_v5(&si,(uintptr_t)source,0,earlier,start,false,&next,0);
            if (kr || memcmp(source,input+earlier*4096,4096)) return -2;
            start=next;
        }
        if (start==VM_SR_PART_NONE || start/4096>part) continue;
        /* A separate exact 4 KiB allocation makes ASan catch adjacent-part writes. */
        uint8_t target[4096]; memcpy(target,input+part*4096,4096);
        if (vm_shared_region_slide_part_v5(&si,(uintptr_t)target,userva+part*4096,
                                         part,start,true,&next,0)) return -3;
        memcpy(split+part*4096,target,4096);
    }
    for (unsigned i=0;i<16384;i++) if (whole[i]!=split[i]) {
        unsigned slot=i&~7U; memcpy(bad_words,split+slot,8);memcpy(bad_words+1,whole+slot,8);
        return (int)i+1;
    }
    return 0;
}

int main(void) {
    uint8_t input[16384]={0};uint64_t bad[2]={0};
    /* Mixed IA/DA authenticated and tagged plain words, every part boundary. */
    const unsigned offsets[]={0,4088,4096,8184,8192,12280,12288,16376};
    for (unsigned i=0;i<8;i++) {
        uint64_t delta=i==7?0:(offsets[i+1]-offsets[i])/8;
        uint64_t word=(i%3 ? (1ULL<<63)|((uint64_t)(i&1)<<51)|((uint64_t)0x5432<<34)
                                : (0xabULL<<34)) | (delta<<52) | (0x1234+i*8);
        memcpy(input+offsets[i],&word,8);
    }
    for (unsigned order=0;order<3;order++)
        assert(!replay(input,0,0x1fffc000,0x180000000ULL,0x190000000ULL,order,bad));
    union entries e={.v5={5,16384,1,0x180000000ULL,{0}}};struct info si={&e,0};
    uint32_t next=7;uint8_t part[4096]={0};
    assert(vm_shared_region_slide_part_v5(&si,(uintptr_t)part,0,1,0,true,&next,0)==KERN_FAILURE);
    assert(next==VM_SR_PART_NONE);
    assert(vm_shared_region_slide_part_v5(&si,(uintptr_t)part,0,0,1,true,&next,0)==KERN_FAILURE);
    assert(vm_shared_region_slide_part_v5(&si,(uintptr_t)part,0,0,4096,true,&next,0)==KERN_FAILURE);
    puts("PASS: production split/full pager equivalence, three fault orders, mixed auth/tagged words, exact 4 KiB boundaries, read-only peeks and refusal paths");
}
'''


def compile_source(xnu, work, name, sanitize=False):
    text = (xnu / 'osfmk/vm/vm_shared_region.c').read_text()
    code = COMMON + function(text, 'vm_shared_region_slide_page_v5') + function(text, 'vm_shared_region_slide_part_v5') + HARNESS
    source = work / (name + '.c')
    source.write_text(code)
    output = work / (name if sanitize else name + '.so')
    compiler = shutil.which('clang-17') or shutil.which('clang')
    command = [compiler, '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
               '-Wno-unused-parameter', '-Wno-unused-function', str(source), '-o', str(output)]
    command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if sanitize else ['-shared', '-fPIC']
    subprocess.run(command, check=True)
    if sanitize:
        subprocess.run([output], check=True)
        return None
    lib = ctypes.CDLL(str(output))
    lib.replay.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint64,
                           ctypes.c_uint64, ctypes.c_uint, ctypes.c_void_p]
    lib.replay.restype = ctypes.c_int
    return lib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--xnu', required=True, type=Path)
    parser.add_argument('--previous-xnu', type=Path)
    parser.add_argument('--cache', required=True, type=Path)
    parser.add_argument('--work', required=True, type=Path)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    compile_source(args.xnu, args.work, 'sanitized', True)
    current = compile_source(args.xnu, args.work, 'current')
    previous = compile_source(args.previous_xnu, args.work, 'previous') if args.previous_xnu else None
    pages, cases, mappings, mismatch = 0, 0, [], None
    words = (ctypes.c_uint64 * 2)()
    files = sorted(f for f in args.cache.glob('dyld_shared_cache_arm64e*') if ':' not in f.name)
    for path in files:
        if not path.is_file() or path.stat().st_size < 512:
            continue
        with path.open('rb') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            if data[:7] != b'dyld_v1':
                continue
            offset, count = struct.unpack_from('<2I', data, 0x138)
            for i in range(count):
                address, size, fileoff, slideoff, slidebytes, _, _, _ = struct.unpack_from('<6Q2I', data, offset + i*56)
                if not slidebytes:
                    continue
                version, page_size, page_count = struct.unpack_from('<3I', data, slideoff)
                if (version, page_size) != (5, 16384):
                    raise ValueError((path, i, version, page_size))
                value_add, = struct.unpack_from('<Q', data, slideoff+16)
                mappings.append(dict(file=path.name, mapping=i, version=version, page_size=page_size, pages=page_count))
                for page in range(page_count):
                    first, = struct.unpack_from('<H', data, slideoff+24+page*2)
                    if first == 0xffff:
                        continue
                    raw = data[fileoff+page*16384:fileoff+(page+1)*16384]
                    assert len(raw) == 16384 and first < 16384
                    buf = ctypes.create_string_buffer(raw)
                    if previous and mismatch is None:
                        result = previous.replay(buf, first, 0x4000, value_add, address+page*16384+0x4000, 1, words)
                        assert result >= 0, result
                        if result:
                            mismatch = dict(file=path.name, mapping=i, page=page, byte=result-1,
                                            broken_word=hex(words[0]), expected_word=hex(words[1]),
                                            slot_unslid=hex(address+page*16384+((result-1)&~7)))
                    for order, slide in enumerate((0, 0x4000, 0x20000000)):
                        result = current.replay(buf, first, slide, value_add, address+page*16384+slide, order, words)
                        assert result == 0, (path.name, i, page, order, result, list(words))
                        cases += 1
                    pages += 1
        if '.dylddata' in path.name:
            print(f'checked {path.name}: {pages} populated pages', flush=True)
    assert pages and mappings
    assert previous is None or mismatch, 'the previous production source must reproduce a real-cache mismatch'
    report = dict(passed=True, source_sha256=hashlib.sha256((args.xnu/'osfmk/vm/vm_shared_region.c').read_bytes()).hexdigest(),
                  mappings=mappings, populated_pages=pages, replay_cases=cases,
                  sanitizers=['ASan','UBSan'], orders=[[0,1,2,3],[3,1,0,2],[2,0,3,1]],
                  previous_source_negative_control=mismatch, hardware_tested=False)
    (args.work/'verification.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='mappings'}, indent=2))


if __name__ == '__main__':
    main()
