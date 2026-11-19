#!/usr/bin/env python3
"""Bind the owned GOP backend to the original IOMobileFramebufferLegacy of the iPadOS 27 (24A446) fileset.

The class ABI (vtable targets and diversities, instance size, field offsets, original bindings) comes from the reviewed fixture
legacy-display-24A446.json. The kernel is arm64e: every vtable entry of the owned subclass must carry the diversity the original
callers authenticate with, so every original slot is declared under a name whose discriminator is that slot's diversity. Original
code is bound at its fileset address plus the collection slide; no original instruction or vtable is changed.
"""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
from kernel_target import ARCH, IOS_VERSION, TRIPLE
# The OS profile's build selects the class fixtures: the reviewed iPadOS 27 ones, or the macOS 27.3 ones that
# gen-board-abi.py writes from the symbols of the macOS collection.
BUILD = os.environ.get('UNIXNU_IPSW_BUILD') or '24A446'
MACOS = os.environ.get('UNIXNU_XNU_TARGET') == 'macosx'
if ARCH != 'arm64' or IOS_VERSION.split('.')[0] != '27' or (BUILD, MACOS) not in (('24A446', False), ('26C5035r', True)):
    raise SystemExit('This Legacy display builder supports arm64e iPadOS 27 (24A446) and macOS 27.3 (26C5035r).')
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build/native-legacy-display'
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / 'kits/XnuPrelink'))
ABI_PATH = Path(__file__).resolve().with_name(f'legacy-display-{BUILD}.json')
abi = json.loads(ABI_PATH.read_text())
# IOService's vtable length in this kernel and the pure-virtual target of the collection: fixed for iPadOS 27, recorded
# in the fixture for macOS.
BASE = abi.get('base_slots', 168)
slide = int(abi['collection_slide'], 16)
# Original targets are fixed collection addresses. XnuPrelink recomputes the
# BTI/B adapter displacement after placing native text; no prior build is needed.
slots = [(int(t, 16), int(d, 16)) for t, d in abi['slots']]
D, R, N = abi['display'], abi['request'], abi.get('notify', {})


def run(cmd, **kw):
    return subprocess.check_output([str(x) for x in cmd], timeout=300, **kw)


def require(v, why):
    if not v:
        raise SystemExit(why)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def discriminator(name):
    """clang ptrauth_string_discriminator: SipHash-2-4 with the fixed key, modulo 0xffff, plus one."""
    mask = (1 << 64) - 1
    rotl = lambda x, b: ((x << b) | (x >> (64 - b))) & mask  # noqa: E731
    key = bytes([0xb5, 0xd4, 0xc9, 0xeb, 0x79, 0x10, 0x4a, 0x79, 0x6f, 0xec, 0x8b, 0x1b, 0x42, 0x87, 0x81, 0xd4])
    k0, k1 = struct.unpack('<2Q', key)
    v = [0x736f6d6570736575 ^ k0, 0x646f72616e646f6d ^ k1, 0x6c7967656e657261 ^ k0, 0x7465646279746573 ^ k1]

    def rounds(n):
        for _ in range(n):
            v[0] = (v[0] + v[1]) & mask; v[1] = rotl(v[1], 13) ^ v[0]; v[0] = rotl(v[0], 32)
            v[2] = (v[2] + v[3]) & mask; v[3] = rotl(v[3], 16) ^ v[2]
            v[0] = (v[0] + v[3]) & mask; v[3] = rotl(v[3], 21) ^ v[0]
            v[2] = (v[2] + v[1]) & mask; v[1] = rotl(v[1], 17) ^ v[2]; v[2] = rotl(v[2], 32)
    data = name.encode()
    full = len(data) & ~7
    for i in range(0, full, 8):
        m = struct.unpack_from('<Q', data, i)[0]
        v[3] ^= m; rounds(2); v[0] ^= m
    b = len(data) << 56 & mask
    for i, c in enumerate(data[full:]):
        b |= c << (8 * i)
    v[3] ^= b; rounds(2); v[0] ^= b; v[2] ^= 0xff; rounds(4)
    return (v[0] ^ v[1] ^ v[2] ^ v[3]) % 0xffff + 1


# Prototypes of the original slots the owned backend overrides or calls; every other slot is an opaque dispatch target.
PROTOTYPES = {
    'isMainDisplay': 'bool isMainDisplay()', 'getDotPitch': 'IOReturn getDotPitch(uint32_t *)',
    'supportedFrameInfo': 'IOReturn supportedFrameInfo(uint32_t,uint32_t *,char *,uint32_t *)=0',
    'getDisplayDimensions': 'void getDisplayDimensions(uint32_t *,uint32_t *)', 'startHardware': 'bool startHardware()',
    'kickSwapQueue': 'void kickSwapQueue()', 'completeCurrentSwap': 'void completeCurrentSwap()',
    'validateSwap': 'bool validateSwap(const OriginalLegacySwapRequest *)',
    'getRefreshAttributes': 'bool getRefreshAttributes(uint64_t *,bool)',
    'collectCompletionReports': 'IOReturn collectCompletionReports(uint32_t,void *,uint32_t *,uint32_t)=0',
    'applyStateRequest': 'int applyStateRequest(OriginalLegacyStateRequest *,bool)',
    # Slot 366: swap_wait_gated calls it (this only, result logged) after a swap waited 5 s, then cancels its own queue (slot 367).
    'cancelSwapsAfterTimeout': 'bool cancelSwapsAfterTimeout()',
    # Slot 316 initializes optional family state and returns void; x0 is not an IOSurface result.
    'create_default_fb_surface': 'void create_default_fb_surface()'}
# A macOS fixture carries its own prototypes (the reference labels plus the family hooks it implements).
PROTOTYPES = abi.get('prototypes', PROTOTYPES)
by_slot = {slot: name for name, slot in abi['methods'].items()}
PURE_TARGET = int(abi['pure_target'], 16) if 'pure_target' in abi else slots[214][0]
pure = {i for i, (t, _) in enumerate(slots) if t == PURE_TARGET}
renames = []
for base, (name, suffix) in abi['names'].items():
    mangled = f'_ZN25IOMobileFramebufferLegacy{len(name)}{name}{suffix}'
    slot = abi['methods'].get(base) or int(base[len('opaque'):])
    require(discriminator(mangled) == slots[slot][1], f'Reviewed name {name} has the diversity of slot {slot}')
    renames.append(f'#define {base} {name}\n')
leaves = ' '.join(f'LEAF({i})' for i in abi['leaves'])
# The macOS family's swap request and notification fields (gen-board-abi.py); the iPadOS header has none of them.
MACOS_CONSTANTS = ('#define NATIVE_LEGACY_MACOS 1\n'
    f"static constexpr unsigned kLegacyCurrentSwap={D['current']};\n"
    f"static constexpr unsigned kRequestDropReasons={R.get('drop_reasons', 0)},kRequestSwapInfoA={R.get('swap_info_a', 0)},kRequestSwapInfoB={R.get('swap_info_b', 0)};\n"
    f"static constexpr unsigned kRequestCompleted={R.get('completed', R['masks'] + 4)};\n"
    f"static constexpr unsigned kLegacySwapInfoTime={D.get('swap_info_time', 0)},kLegacySwapInfoHeld={D.get('swap_info_held', 0)};\n"
    f"static constexpr unsigned kNotifyAsyncReference={N.get('async_reference', 0)},kNotifyMask={N.get('mask', 0)},kNotifyLastTime={N.get('last_time', 0)};\n"
    ) if MACOS else ''
header = f'''// SPDX-License-Identifier: BSD-2-Clause-Patent
// Opaque ABI storage for the original iPadOS 27 (24A446) class, generated from {ABI_PATH.name}. Each original slot is declared
// under a name whose pointer-authentication discriminator is the original slot's diversity (the names below), so original
// callers authenticate the owned subclass's entries. Only qualified base prototypes may be called from source.
#pragma once
#include <IOKit/IOService.h>
#include <libkern/libkern.h>
extern "C" OSObject *native_legacy_default_surface_create(OSObject *,uint32_t,uint32_t);
struct OriginalLegacySwapRequest;
struct OriginalLegacyStateRequest {{uint32_t skipRequested,requested,skipPowered,powered;uint8_t changed;uint8_t pad[11];}};
static constexpr unsigned kLegacyVsyncClients={D['vsync_clients']};
static constexpr unsigned kRequestRect0={R['rect0']},kRequestRect1={R['rect1']},kRequestMasks={R['masks']},kRequestOptions={R['options']},kRequestDims={R['dims']},kRequestSurfaces={R['surfaces']};
#define NATIVE_LEGACY_LEAVES(LEAF) {leaves}
#define NATIVE_LEGACY_DEFAULT_SURFACE 1
#define NATIVE_LEGACY_CANCEL_SWAPS 1
{MACOS_CONSTANTS}static constexpr unsigned kLegacyDefaultSurface={D['default_surface']};
{''.join(renames)}class IOMobileFramebufferLegacy : public IOService {{
 OSDeclareAbstractStructors(IOMobileFramebufferLegacy)
 unsigned char originalPrivate[{abi['instance_size']}-sizeof(IOService)];
public:
 bool installDefaultSurface(uint32_t width,uint32_t height) {{
  auto self=reinterpret_cast<unsigned char *>(this);
  OSObject *root=nullptr,*surface=nullptr;
  memcpy(&surface,self+kLegacyDefaultSurface,sizeof(surface));
  if(surface)return true;
  memcpy(&root,self+{D['surface_root']},sizeof(root));
  if(!root)return false;
  surface=native_legacy_default_surface_create(root,width,height);
  if(!surface)return false;
  // Transfer the factory reference to the family; its stop path releases this field.
  memcpy(self+kLegacyDefaultSurface,&surface,sizeof(surface));
  return true;
 }}
 void installBootDimensions(uint32_t width,uint32_t height) {{
  memcpy(reinterpret_cast<unsigned char *>(this)+{D['width']},&width,4);memcpy(reinterpret_cast<unsigned char *>(this)+{D['height']},&height,4);
 }}
 const OriginalLegacySwapRequest *pendingSwapRequest() const {{
  const OriginalLegacySwapRequest *request=nullptr;
  memcpy(&request,reinterpret_cast<const unsigned char *>(this)+{D['head']},sizeof(request));return request;
 }}
 // Tail queue: head {D['head']}, tail pointer {D['tail']}, each request's next {R['next']} and previous-link {R['prev']}; the head moves
 // to the current-swap slot {D['current']} for the original completion body.
 bool activateOnlyPendingSwap(const OriginalLegacySwapRequest *request) {{
  auto self=reinterpret_cast<unsigned char *>(this);
  auto bytes=reinterpret_cast<const unsigned char *>(request);
  const void *current=nullptr,*head=nullptr,*tail=nullptr,*next=nullptr,*previous=nullptr;
  memcpy(&current,self+{D['current']},8);memcpy(&head,self+{D['head']},8);memcpy(&tail,self+{D['tail']},8);
  if(!request || current || head!=request)return false;
  memcpy(&next,bytes+{R['next']},8);memcpy(&previous,bytes+{R['prev']},8);
  if(previous!=self+{D['head']})return false;
  if(next) {{
   const void *link=self+{D['head']};
   memcpy(const_cast<unsigned char *>(reinterpret_cast<const unsigned char *>(next))+{R['prev']},&link,8);
  }} else if(tail!=bytes+{R['next']})return false;
  memcpy(self+{D['current']},&request,8);
  if(next) {{memcpy(self+{D['head']},&next,8);}}
  else {{const void *empty=nullptr,*emptyTail=self+{D['head']};memcpy(self+{D['head']},&empty,8);memcpy(self+{D['tail']},&emptyTail,8);}}
  return true;
 }}
 void free() override;
 bool init(OSDictionary *properties=nullptr) override;
 IOReturn setProperties(OSObject *) override;
 void systemWillShutdown(IOOptionBits) override;
 IOService *probe(IOService *,SInt32 *) override;
 bool start(IOService *) override;
 void stop(IOService *) override;
 IOWorkLoop *getWorkLoop() const override;
 IOReturn newUserClient(task_t,void *,UInt32,IOUserClient **) override;
 IOReturn setPowerState(unsigned long,IOService *) override;
'''
for i in range(BASE, len(slots)):
    if i in by_slot:
        header += f' virtual {PROTOTYPES[by_slot[i]]};\n'
    elif i in pure:
        header += f' virtual __attribute__((noreturn)) void opaque{i}()=0;\n'
    else:
        header += f' virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque{i}();\n'
header += f'}};\nstatic_assert(sizeof(IOMobileFramebufferLegacy)=={abi["instance_size"]},"Actual original instance extent");\n'
(OUT / 'OriginalLegacyFramebufferABI.h').write_text(header)
print(run(['python3', ROOT / 'scripts/build-arm-driver.py', 'NativeLegacyDisplay'], text=True), end='')



def macho(data):
    """Load commands, segments and sections of an arm64e relocatable object."""
    magic, cpu, _, _, count = struct.unpack_from('<5I', data)
    require(magic == 0xfeedfacf and cpu == 0x100000c, 'arm64 Mach-O object')
    commands, segments, offset = [], [], 32
    for _ in range(count):
        command, size = struct.unpack_from('<2I', data, offset)
        commands.append((command, offset, size))
        if command == 0x19:
            nsects = struct.unpack_from('<I', data, offset + 64)[0]
            sections = []
            for i in range(nsects):
                at = offset + 72 + 80 * i
                va, length, fileoff = struct.unpack_from('<QQI', data, at + 32)
                sections.append({'name': data[at:at + 16].rstrip(b'\0').decode(), 'va': va, 'size': length,
                                 'fileoff': fileoff, 'command_offset': at})
            segments.append({'sections': sections})
        offset += size
    return {'commands': commands, 'segments': segments}


obj = OUT / 'NativeLegacyDisplay.o'
raw = obj.read_bytes()
image = macho(raw)
st = next(p for c, p, _ in image['commands'] if c == 2)
sy, count, strings, _ = struct.unpack_from('<4I', raw, st + 8)
symbols = []
for i in range(count):
    ix, kind, section, _, va = struct.unpack_from('<IBBHQ', raw, sy + i * 16)
    symbols.append((raw[strings + ix:raw.index(b'\0', strings + ix)].decode(), va, kind))
vtable = next(va for name, va, _ in symbols if name == '__ZTV19NativeLegacyDisplay')
entries = {}
for segment in image['segments']:
    for sec in segment['sections']:
        if sec['va'] <= vtable < sec['va'] + sec['size']:
            ro, rc = struct.unpack_from('<2I', raw, sec['command_offset'] + 56)
            for i in range(rc):
                offset, bits = struct.unpack_from('<2I', raw, ro + i * 8)
                # extern, 8 bytes, ARM64_RELOC_AUTHENTICATED_POINTER
                if bits >> 27 & 1 and bits >> 25 & 3 == 3 and bits >> 28 == 0:
                    word = struct.unpack_from('<Q', raw, sec['fileoff'] + offset)[0]
                    entries[sec['va'] + offset] = (symbols[bits & 0xffffff][0], word)
compiled = [entries.get(vtable + 16 + i * 8) for i in range(len(slots))]
require(all(compiled) and vtable + 16 + len(slots) * 8 not in entries, f'Exact owned {len(slots)}-entry vtable')
for i, (name, word) in enumerate(compiled):
    require(word == 0, f'Slot {i} ({name}): plain arm64 pointer relocation')
family = range(*(int(x, 16) for x in abi['family_text'])) if 'family_text' in abi else range(0xfffffe000a27c000, 0xfffffe000a29a000)
bindings = {name: int(va, 16) + slide for name, va in abi['bindings'].items()}
owned, native = [], []
for i, (name, _) in enumerate(compiled):
    if name.startswith(('__ZN19NativeLegacyDisplay', '__ZNK19NativeLegacyDisplay')):
        owned.append(i)
    elif '25IOMobileFramebufferLegacy' in name:
        require(i not in pure, f'No unresolved original abstract target at slot {i}')
        require(bindings.get(name, slots[i][0] + slide) == slots[i][0] + slide, 'One original address per name')
        bindings[name] = slots[i][0] + slide
    else:
        require(i < BASE and slots[i][0] not in family, f'Slot {i} ({name}) is inherited unchanged by the original family')
        native.append(i)
require(pure <= set(owned), 'Every original abstract slot is implemented by the owned backend')
undefined = {n for n, _, k in symbols if k == 1 and 'IOMobileFramebufferLegacy' in n}
require(undefined <= set(bindings), 'Every original-class import is bound: ' + ', '.join(sorted(undefined - set(bindings))))

asm = OUT / 'original-bindings.S'
# Absolute symbols get no fixup: a pointer to one is neither signed nor moved. Every original function is therefore reached through
# an owned stub in kernel text (the vtable signs and moves it like any native code), whose branch lands on the original. Data
# symbols are reached through GOT entries, which keep their absolute value.
DATA = {'__ZN25IOMobileFramebufferLegacy10gMetaClassE', '__ZTV25IOMobileFramebufferLegacy'}
text, addresses = '.text\n.p2align 2\n', ''
for name, va in sorted(bindings.items()):
    target = f'{va:#x}'
    if name in DATA:
        # Reached through GOT entries: plain pointers that the emitter does not move.
        addresses += f'.globl {name}\n.set {name}, {va:#x}\n'
    else:
        # The absolute target lives in the other object, so the branch stays a relocation the linker resolves.
        addresses += f'.globl {name}.original\n.set {name}.original, {target}\n'
        text += f'.globl {name}\n{name}:\n bti c\n b {name}.original\n'
asm.write_text(text)
(OUT / 'original-addresses.S').write_text(addresses)
sdk = run(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], text=True).strip()
run(['xcrun', 'clang', '-target', TRIPLE, '-isysroot', sdk, '-c', asm, '-o', OUT / 'original-bindings.o'])
run(['xcrun', 'clang', '-target', TRIPLE, '-isysroot', sdk, '-c', OUT / 'original-addresses.S', '-o', OUT / 'original-addresses.o'])
llvm = Path(run(['brew', '--prefix', 'llvm'], text=True).strip()) / 'bin'
late = OUT / 'NativeLegacyDisplay.late.o'
run([llvm / 'llvm-objcopy', '--remove-section=__DATA,__mod_init_func', '--globalize-symbol=__GLOBAL__sub_I_NativeLegacyDisplay.cpp', obj, late])

# The board probe and the owned support code, compiled with the kernel's own C++ flags.
r = json.loads((ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json').read_text().strip().rstrip(','))
source = ROOT / 'kits/nabu/LegacyDisplayProbe.cpp'


def compile_cpp(src, dst, extra=()):
    cmd, args, i = [], r['arguments'], 0
    while i < len(args):
        value = args[i]
        if value == r['file']:
            cmd.append(str(src))
        elif value == '-o':
            cmd += ['-o', str(dst)]
            i += 1
        elif value in ('-MF', '-MT', '-MJ'):
            i += 1
        elif value == '-Xclang' and args[i + 1].startswith('-W'):
            i += 1
        elif not value.startswith('-W'):
            cmd.append(value)
        i += 1
    cmd += ['-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unnecessary-virtual-specifier', '-Wno-c99-designator',
            '-Wno-reorder-init-list', '-Wno-deprecated-enum-enum-conversion', '-I', str(ROOT / '.cache/xnu-work/osfmk'), *extra]
    run(cmd, cwd=r['directory'])


def compile_c(src, dst, recipe='osfmk/DEVELOPMENT/startup.o.json'):
    """A C board core with the flags of one kernel C file (osfmk for the cores, bsd for the VFS readers)."""
    rc = json.loads((ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837' / recipe).read_text().strip().rstrip(','))
    cmd, args, i = [], rc['arguments'], 0
    while i < len(args):
        value = args[i]
        if value == rc['file']:
            cmd.append(str(src))
        elif value in ('-o', '-MF', '-MT', '-MJ'):
            if value == '-o':
                cmd += ['-o', str(dst)]
            i += 1
        elif value == '-Xclang' and args[i + 1].startswith('-W'):
            i += 1
        elif not value.startswith('-W'):
            cmd.append(value)
        i += 1
    cmd += ['-Wall', '-Wextra', '-Werror', '-mgeneral-regs-only', '-I', str(ROOT / '.cache/xnu-work/osfmk'), '-I', str(ROOT / 'kits/sheng')]
    run(cmd, cwd=rc['directory'])


compile_cpp(source, OUT / 'LegacyDisplayProbe.o')
support = OUT / 'LegacyDisplaySupport27.cpp'
support.write_text('''// SPDX-License-Identifier: BSD-2-Clause-Patent
// Generated by build-gop-legacy-display-27.py. The iPadOS 27 IOSurface has no allocation-size getter: bound the copy by the
// surface's memory descriptor instead. Board probes not yet built for iPadOS 27 start nothing.
#include <IOKit/IOMemoryDescriptor.h>
#include <libkern/c++/OSDictionary.h>
#include <libkern/c++/OSNumber.h>
extern "C" IOMemoryDescriptor *native_legacy_surface_descriptor(OSObject *);
extern "C" OSObject *native_legacy_surface_create(OSObject *,void *,OSDictionary *);
extern "C" IOReturn native_legacy_surface_allocate(OSObject *);
extern "C" uint64_t native_legacy_surface_width(OSObject *),native_legacy_surface_height(OSObject *);
extern "C" uint64_t native_legacy_surface_stride(OSObject *);
extern "C" uint32_t native_legacy_surface_format(OSObject *),native_legacy_surface_planes(OSObject *);
extern "C" OSObject *native_legacy_default_surface_create(OSObject *root,uint32_t width,uint32_t height) {
    if(!root || !width || !height || width>16384 || height>16384)return nullptr;
    const uint64_t stride=(uint64_t(width)*4+127)&~uint64_t(127);
    const uint64_t bytes=(stride*height+4095)&~uint64_t(4095);
    OSDictionary *properties=OSDictionary::withCapacity(6);
    if(!properties)return nullptr;
    const char *keys[]={"IOSurfaceWidth","IOSurfaceHeight","IOSurfaceBytesPerRow",
                        "IOSurfaceBytesPerElement","IOSurfacePixelFormat","IOSurfaceAllocSize"};
    const uint64_t values[]={width,height,stride,4,0x42475241,bytes};
    bool valid=true;
    for(unsigned i=0;i<6 && valid;++i) {
        OSNumber *number=OSNumber::withNumber(values[i],64);
        valid=number && properties->setObject(keys[i],number);
        if(number)number->release();
    }
    OSObject *surface=valid?native_legacy_surface_create(root,nullptr,properties):nullptr;
    properties->release();
    if(!surface){printf("sheng-fb: default surface factory failed\\n");return nullptr;}
    IOReturn status=native_legacy_surface_allocate(surface);
    IOMemoryDescriptor *memory=native_legacy_surface_descriptor(surface);
    valid=status==kIOReturnSuccess && memory && memory->getLength()>=bytes &&
          native_legacy_surface_width(surface)==width && native_legacy_surface_height(surface)==height &&
          native_legacy_surface_stride(surface)==stride && native_legacy_surface_format(surface)==0x42475241 &&
          native_legacy_surface_planes(surface)==0;
    printf("sheng-fb: default surface %p status 0x%x valid %u %ux%u stride %llu bytes %llu\\n",
           surface,status,valid,width,height,stride,bytes);
    if(!valid){surface->release();return nullptr;}
    return surface;
}
extern "C" uint64_t native_legacy_surface_allocation(OSObject *surface) {
    IOMemoryDescriptor *memory=native_legacy_surface_descriptor(surface);return memory?memory->getLength():0;
}
// The board diagnostic page is not drawn on iPadOS 27: the keystore service's extension rows go nowhere.
extern "C" {char boot_progress_ext[8][48];}
// No boot progress animation runs on iPadOS 27: the scanout is always free and no pulse anchors to the framebuffer.
extern "C" int boot_progress_prepare_scanout(void) {return 1;}
extern "C" void boot_progress_cancel_scanout(void) {}
extern "C" void boot_progress_stage(const char *) {}
extern "C" int boot_progress_pulse_anchor(uintptr_t,uint64_t,unsigned,unsigned,unsigned) {return 0;}
''')
compile_cpp(support, OUT / 'LegacyDisplaySupport27.o')
# The keystore state service (AppleKeyStoreState) and its probe, unchanged from iPadOS 17.
keystore_source = ROOT / 'kits/nabu/KeyStoreProbe.cpp'
compile_cpp(keystore_source, OUT / 'KeyStoreProbe.o')
print(run(['python3', ROOT / 'scripts/build-arm-driver.py', 'NativeKeyStoreEvents'], text=True), end='')
keyevents = ROOT / 'build/apple-key-store-state'
keyevents_late = keyevents / 'NativeKeyStoreEvents.late.o'
run([llvm / 'llvm-objcopy', '--remove-section=__DATA,__mod_init_func', '--globalize-symbol=__GLOBAL__sub_I_NativeKeyStoreEvents.cpp',
     keyevents / 'NativeKeyStoreEvents.o', keyevents_late])
print(run(['python3', ROOT / 'scripts/build-arm-driver.py', 'NativeGOPPresenter'], text=True), end='')
gop = ROOT / 'build/native-gop-presenter'
gop_late = gop / 'NativeGOPPresenter.late.o'
run([llvm / 'llvm-objcopy', '--remove-section=__DATA,__mod_init_func', '--globalize-symbol=__GLOBAL__sub_I_NativeGOPPresenter.cpp',
     gop / 'NativeGOPPresenter.o', gop_late])

def object_symbols(data):
    commands = macho(data)['commands']
    st = next(p for c, p, _ in commands if c == 2)
    sy, count, strings, _ = struct.unpack_from('<4I', data, st + 8)
    found = []
    for i in range(count):
        ix, kind, _, _, va = struct.unpack_from('<IBBHQ', data, sy + i * 16)
        found.append((data[strings + ix:data.index(b'\0', strings + ix)].decode(), va, kind))
    return found


def object_vtable(data, found, name, count):
    """[(symbol, signed word)] of the first count entries of vtable name (authenticated pointer relocations)."""
    va = next(v for n, v, _ in found if n == name)
    entries = {}
    for segment in macho(data)['segments']:
        for sec in segment['sections']:
            if sec['va'] <= va < sec['va'] + sec['size']:
                ro, rc = struct.unpack_from('<2I', data, sec['command_offset'] + 56)
                for i in range(rc):
                    offset, bits = struct.unpack_from('<2I', data, ro + i * 8)
                    if bits >> 27 & 1 and bits >> 25 & 3 == 3 and bits >> 28 == 0:
                        entries[sec['va'] + offset] = (found[bits & 0xffffff][0],
                                                       struct.unpack_from('<Q', data, sec['fileoff'] + offset)[0])
    return [entries.get(va + 16 + i * 8) for i in range(count)]



DEVICE = Path(os.environ.get("UNIXNU_DEVICE", ROOT))
KITS = ROOT / "kits/nabu"
board_dir = OUT / "board"
board_dir.mkdir(exist_ok=True)
input_objects = []
# macOS: the board's Ethernet controller en0 (ShengEthernet.cpp), an IOEthernetController of the original IONetworkingFamily compiled with
# the macOS SDK's public family headers (after the kernel's own, so only IOKit/network comes from there). Its vtable must be the
# collection's slot for slot (net-ether-<build>.json): every entry signed with the original diversity, every inherited entry bound to
# the original target, every family import bound to the collection's symbol.
if MACOS:
    ETH_OUT = board_dir / 'ethernet'
    ETH_OUT.mkdir(parents=True, exist_ok=True)
    eth_abi = json.loads(Path(__file__).resolve().with_name(f'net-ether-{BUILD}.json').read_text())
    require(eth_abi['kernelcache_sha256'] == abi['kernelcache_sha256'], 'Ethernet fixture of the same collection')
    macsdk = run(['xcrun', '--sdk', 'macosx', '--show-sdk-path'], text=True).strip()
    # The SDK declares IONetworkController's reserved slots 2-5 unused; the shipped family uses them (outputStart,
    # setInputPacketPollingEnable, pollInputPackets, networkInterfaceNotification). Apple's published IONetworkingFamily headers
    # declare them under __PRIVATE_SPI__, so those headers, as IOKit/network, come first.
    ionet = ROOT / '.cache/IONetworkingFamily-188'
    if not (ionet / 'IONetworkController.h').is_file():
        subprocess.run(['git', 'clone', '-q', '--depth', '1', '--branch', 'IONetworkingFamily-188',
                        'https://github.com/apple-oss-distributions/IONetworkingFamily.git', str(ionet)], check=True)
    ionet_include = ETH_OUT / 'include/IOKit'
    ionet_include.mkdir(parents=True, exist_ok=True)
    if not (ionet_include / 'network').exists():
        (ionet_include / 'network').symlink_to(ionet)
    # The family's instance sizes as the collection has them: the source asserts the SDK headers give the same.
    compile_cpp(KITS / 'ShengEthernet.cpp', ETH_OUT / 'ShengEthernet.o',
                ('-I', str(ETH_OUT / 'include'), '-idirafter', f'{macsdk}/System/Library/Frameworks/Kernel.framework/Headers',
                 f'-DSHENG_ETHERNET_CONTROLLER_SIZE={eth_abi["instance_size"]}',
                 f'-DSHENG_ETHERNET_INTERFACE_SIZE={eth_abi["interface_instance_size"]}'))
    eth_raw = (ETH_OUT / 'ShengEthernet.o').read_bytes()
    eth_found = object_symbols(eth_raw)
    eth_pure = int(eth_abi['pure_target'], 16)
    eth_bindings = {n: int(v, 16) + slide for n, v in eth_abi['bindings'].items()}
    family = tuple(f'{len(c)}{c}' for c in ('IONetworkController', 'IOEthernetController', 'IONetworkMedium', 'IONetworkInterface',
                                           'IOEthernetInterface', 'IOOutputQueue'))
    eth_used = {n for n, _, k in eth_found if k == 1 and any(c in n for c in family)}

    def family_vtable(cls, base, slots):
        """Check the vtable of cls, a subclass of the family's class base, against base's slots in the collection; the slots cls overrides."""
        vt = f'__ZTV{len(cls)}{cls}'
        own = (f'__ZN{len(cls)}{cls}', f'__ZNK{len(cls)}{cls}')
        entries = object_vtable(eth_raw, eth_found, vt, len(slots) + 1)
        require(all(entries[:len(slots)]) and not entries[len(slots)], f'{vt}: exactly the {len(slots)} {base} entries')
        override_slot = {}
        for i, (name, word) in enumerate(entries[:len(slots)]):
            require(word == 0,
                    f'{vt} slot {i} ({name}) signed with the original diversity {slots[i][1]:#x}')
            if name.startswith(own):
                override_slot[name[len(own[0]) if name.startswith(own[0]) else len(own[1]):]] = i
            elif any(c in name for c in family):
                require(slots[i][0] != eth_pure, f'{vt} slot {i}: the abstract original slot is implemented')
                require(eth_bindings.setdefault(name, slots[i][0] + slide) == slots[i][0] + slide,
                        f'{vt} slot {i} ({name}) is the original entry')
            else:
                # The kernel's own classes (OSObject, IOService): the kernel defines these, the link resolves them.
                require(i < BASE, f'{vt} slot {i} ({name}) is a kernel method within the IOService slots')
        for name in sorted(eth_used - set(eth_bindings)):
            # A qualified call of a base method the family class does not override itself (IOEthernetController::start is
            # IONetworkController's): the original entry of the slot this class overrides with the same method.
            tail = next((name[len(q):] for q in (f'__ZN{len(base)}{base}', f'__ZNK{len(base)}{base}') if name.startswith(q)), None)
            if tail in override_slot:
                eth_bindings[name] = slots[override_slot[tail]][0] + slide
        eth_used.update(e[0] for e in entries[:len(slots)] if not e[0].startswith(own) and any(c in e[0] for c in family))
        return vt

    eth_slots = [(int(t, 16), int(d, 16)) for t, d in eth_abi['slots']]
    interface_slots = [(int(t, 16), int(d, 16)) for t, d in eth_abi['interface_slots']]
    eth_vt = family_vtable('ShengEthernet', 'IOEthernetController', eth_slots)
    interface_vt = family_vtable('ShengWifiInterface', 'IOEthernetInterface', interface_slots)
    for name in sorted(eth_used - set(eth_bindings)):
        require(False, f'{name}: bound to the collection')
    eth_text, eth_addresses = '.text\n.p2align 2\n', ''
    for name in sorted(eth_used):
        va = eth_bindings[name]
        if name.endswith('10gMetaClassE') or name.startswith('__ZTV'):
            eth_addresses += f'.globl {name}\n.set {name}, {va:#x}\n'
        else:
            eth_addresses += f'.globl {name}.original\n.set {name}.original, {va:#x}\n'
            eth_text += f'.globl {name}\n{name}:\n bti c\n b {name}.original\n'
    (ETH_OUT / 'eth-bindings.S').write_text(eth_text)
    (ETH_OUT / 'eth-addresses.S').write_text(eth_addresses)
    for stem in ('eth-bindings', 'eth-addresses'):
        run(['xcrun', 'clang', '-target', TRIPLE, '-isysroot', sdk, '-c', ETH_OUT / (stem + '.S'), '-o', ETH_OUT / (stem + '.o')])
    eth_late = ETH_OUT / 'ShengEthernet.late.o'
    run([llvm / 'llvm-objcopy', '--remove-section=__DATA,__mod_init_func', '--globalize-symbol=__GLOBAL__sub_I_ShengEthernet.cpp',
         ETH_OUT / 'ShengEthernet.o', eth_late])
    input_objects += [eth_late, ETH_OUT / 'eth-bindings.o', ETH_OUT / 'eth-addresses.o']
    print(f'Ethernet: {len(eth_used)} original bindings, {eth_vt} matches the original {len(eth_slots)} diversities, '
          f'{interface_vt} the {len(interface_slots)} of IOEthernetInterface')
# macOS: the software AES-CBC provider (UniXNU drivers/SoftwareAES, NativeAES.cpp), an IOAESAccelerator of the original
# IOCryptoAcceleratorFamily. AppleImage4's start waits for an IOAESAccelerator service without a limit (kmod_expert_finalize), holding its
# expert lock; this board has no AES engine. Its vtable must be the collection's slot for slot (software-aes-<build>.json), as Ethernet's.
if MACOS:
    AES_OUT = board_dir / 'software-aes'
    AES_OUT.mkdir(parents=True, exist_ok=True)
    aes_abi = json.loads(Path(__file__).resolve().with_name(f'software-aes-{BUILD}.json').read_text())
    require(aes_abi['kernelcache_sha256'] == abi['kernelcache_sha256'], 'AES fixture of the same collection')
    require(int(aes_abi['instance_size'], 16) == 0x120, 'IOAESAccelerator instance size of OriginalAESABI.h')
    compile_cpp(ROOT / 'drivers/SoftwareAES/NativeAES.cpp', AES_OUT / 'NativeAES.o', ('-DNATIVE_AES_DARWIN26=1',))
    aes_raw = (AES_OUT / 'NativeAES.o').read_bytes()
    aes_found = object_symbols(aes_raw)
    aes_slots = [(int(t, 16), int(d, 16)) for t, d in aes_abi['slots']]
    aes_pure = int(aes_abi['pure_target'], 16)
    aes_bindings = {n: int(v, 16) + slide for n, v in aes_abi['bindings'].items()}
    aes_vt = '__ZTV9NativeAES'
    aes_family = ('19IOCryptoAccelerator', '16IOAESAccelerator')
    aes_entries = object_vtable(aes_raw, aes_found, aes_vt, len(aes_slots) + 1)
    require(all(aes_entries[:len(aes_slots)]) and not aes_entries[len(aes_slots)], f'{aes_vt}: exactly the {len(aes_slots)} IOAESAccelerator entries')
    aes_own = ('__ZN9NativeAES', '__ZNK9NativeAES')
    for i, (name, word) in enumerate(aes_entries[:len(aes_slots)]):
        require(word == 0,
                f'{aes_vt} slot {i} ({name}) signed with the original diversity {aes_slots[i][1]:#x}')
        if name.startswith(aes_own):
            continue
        if any(c in name for c in aes_family):
            require(aes_slots[i][0] != aes_pure, f'{aes_vt} slot {i}: the abstract original slot is implemented')
            require(aes_bindings.setdefault(name, aes_slots[i][0] + slide) == aes_slots[i][0] + slide,
                    f'{aes_vt} slot {i} ({name}) is the original entry')
        else:
            require(i < BASE, f'{aes_vt} slot {i} ({name}) is a kernel method within the IOService slots')
    aes_used = {n for n, _, k in aes_found if k == 1 and any(c in n for c in aes_family)}
    require(not (aes_used - set(aes_bindings)), f'AES imports bound to the collection: {sorted(aes_used - set(aes_bindings))}')
    aes_used |= {e[0] for e in aes_entries[:len(aes_slots)] if not e[0].startswith(aes_own) and any(c in e[0] for c in aes_family)}
    aes_text, aes_addresses = '.text\n.p2align 2\n', ''
    for name in sorted(aes_used):
        va = aes_bindings[name]
        if name.endswith('10gMetaClassE') or name.startswith('__ZTV'):
            aes_addresses += f'.globl {name}\n.set {name}, {va:#x}\n'
        else:
            aes_addresses += f'.globl {name}.original\n.set {name}.original, {va:#x}\n'
            aes_text += f'.globl {name}\n{name}:\n bti c\n b {name}.original\n'
    (AES_OUT / 'aes-bindings.S').write_text(aes_text)
    (AES_OUT / 'aes-addresses.S').write_text(aes_addresses)
    for stem in ('aes-bindings', 'aes-addresses'):
        run(['xcrun', 'clang', '-target', TRIPLE, '-isysroot', sdk, '-c', AES_OUT / (stem + '.S'), '-o', AES_OUT / (stem + '.o')])
    aes_late = AES_OUT / 'NativeAES.late.o'
    run([llvm / 'llvm-objcopy', '--remove-section=__DATA,__mod_init_func', '--globalize-symbol=__GLOBAL__sub_I_NativeAES.cpp',
         AES_OUT / 'NativeAES.o', aes_late])
    compile_cpp(KITS / 'SoftwareAesProbe.cpp', AES_OUT / 'SoftwareAesProbe.o', ())
    input_objects += [aes_late, AES_OUT / 'aes-bindings.o', AES_OUT / 'aes-addresses.o', AES_OUT / 'SoftwareAesProbe.o']
    print(f'AES: {len(aes_used)} original bindings, {aes_vt} matches the original {len(aes_slots)} diversities')

# Board-owned NT36523 transport; bind this OS's native HID base, never 21H16 offsets.
if MACOS:
    import importlib.util
    touch_spec=importlib.util.spec_from_file_location('nabu_input27',Path(__file__).with_name('build-nabu-input27.py'))
    touch=importlib.util.module_from_spec(touch_spec);touch_spec.loader.exec_module(touch)
    input_objects += touch.build(ROOT,OUT,compile_cpp,run,llvm,TRIPLE,sdk,macho)

archive = OUT / 'libNativeLegacyDisplay.late.a'
archive.unlink(missing_ok=True)
run(['xcrun', 'ar', 'rcs', archive, late, OUT / 'original-bindings.o', OUT / 'original-addresses.o', OUT / 'LegacyDisplayProbe.o', OUT / 'LegacyDisplaySupport27.o', gop_late,
     OUT / 'KeyStoreProbe.o', keyevents_late, *input_objects])
sources = [ROOT / 'drivers/GopFramebuffer/NativeLegacyDisplay.cpp', ROOT / 'drivers/GopFramebuffer/NativeLegacyRequest.h',
           ROOT / 'drivers/GopFramebuffer/NativeGOPPresenter.cpp', ROOT / 'drivers/GopFramebuffer/NativeGOPWire.h', ROOT / 'drivers/GopFramebuffer/NativeHalfCodes.h',
           source, ABI_PATH, keystore_source, ROOT / 'drivers/AppleKeyStoreState/NativeKeyStoreEvents.cpp',
           KITS / 'ShengEthernet.cpp', KITS / 'SoftwareAesProbe.cpp', ROOT / 'drivers/SoftwareAES/NativeAES.cpp', Path(__file__)]
sources += [KITS/'NabuInputHardware.cpp',KITS/'NabuInputHardware.h',KITS/'NabuInputStart.cpp',KITS/'NabuPowerKey.cpp',KITS/'NabuPowerReport.h',KITS/'NabuSpmiV5.h',KITS/'NabuUsbHost.cpp',KITS/'NabuUsb2Core.h',KITS/'NabuUsbRole.h',KITS/'NabuUsbSmmu.h',KITS/'NabuUsbDma.h',KITS/'NabuRpmh.h',Path(__file__).with_name('build-nabu-input27.py'),Path(__file__).with_name('build-nabu-usb27.py'),Path(__file__).with_name('usb-xhci-26C5035r.json'),KITS/'NabuTouch.cpp',KITS/'NabuTouchPointer.h',Path(__file__).with_name('build-nabu-touch27.py'),Path(__file__).with_name('hid-device-26C5035r.json')]
sources += sorted((ROOT / 'drivers/GopFramebuffer').glob('nabu_*.h'))
sources += sorted((ROOT / 'drivers/GopFramebuffer').glob('nabu_*.inc'))
sources += [ROOT / 'drivers/GopFramebuffer/gop_layers.h']
def source_name(p):
    return str(p.relative_to(ROOT))
(OUT / 'build-report.json').write_text(json.dumps(dict(
    source_sha256={source_name(p):sha(p) for p in sources},
    generated_abi_sha256=sha(OUT / 'OriginalLegacyFramebufferABI.h'),
    kernelcache_sha256=abi['kernelcache_sha256'], compiled_object_sha256=sha(obj), archive_sha256=sha(archive),
    bindings={n:hex(v) for n,v in bindings.items()}, virtual_entries=len(slots), owned_entries=owned,
    inherited_native_entries=native, original_bindings='fixed-collection-addresses', original_instructions_modified=False,
    architecture='arm64', os_build='26C5035r', device_tested=False),indent=2)+'\n')
print('NABU27 CPU display, original Ethernet/keystore/AES bindings compiled')
