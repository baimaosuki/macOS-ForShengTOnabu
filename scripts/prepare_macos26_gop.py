#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare the pinned, default-off macOS 26.5 GOP display companion."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import urllib.request


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def prepare(root: Path, destination: Path, *, board: str = "sheng") -> dict:
    if board not in ("sheng", "nabu"):
        raise ValueError("unsupported GOP board")
    manifest = json.loads((root / 'config/macos26-gop-reference.json').read_text())
    repository, revision = manifest['repository'], manifest['revision']
    if repository != 'Droid-VM/macOS-DroidVM' or not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('requires a pinned display reference revision')
    inputs = destination / 'inputs' / revision
    inputs.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name in ('DroidVMDisplay.cpp', 'IOMobileFramebufferABI.h'):
        entry = manifest['files'][name]
        if entry['path'] != 'overlay/kext/DroidVMDisplay/' + name:
            raise ValueError('unexpected display reference path')
        cached = inputs / name
        if cached.exists():
            data = cached.read_bytes()
        else:
            url = f'https://raw.githubusercontent.com/{repository}/{revision}/{entry["path"]}'
            with urllib.request.urlopen(url, timeout=45) as response:
                data = response.read((1 << 20) + 1)
        if len(data) > 1 << 20:
            raise ValueError('display reference exceeds the input size bound')
        data = data.rstrip(b'\n') + b'\n'
        if digest(data) != entry['canonical_sha256']:
            raise ValueError(f'pinned display reference hash mismatch: {name}')
        cached.write_bytes(data)
        (destination / name).write_bytes(data)
        hashes[name] = digest(data)
    patch = root / 'platform/sheng/macos26/display/0001-native-gop-display.patch'
    subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(patch.resolve())],
                   cwd=destination, check=True, capture_output=True, text=True)
    if digest((destination / 'IOMobileFramebufferABI.h').read_bytes()) != manifest['adapted_header_sha256']:
        raise ValueError('paired framebuffer header adaptation is missing or changed')
    source = destination / 'NativeGOPDisplay.cpp'
    (destination / 'DroidVMDisplay.cpp').replace(source)
    board_inputs = {}
    if board == 'nabu':
        # Reuse only the reviewed 25F71 ABI and swap lifecycle. The current
        # GopFramebuffer OriginalMobileFramebufferABI is iOS17, not this family.
        text = source.read_text()
        marker = '    IOMemoryDescriptor *memory = IOMemoryDescriptor::withPhysicalAddress('
        if text.count(marker) != 1:
            raise ValueError('paired scanout mapping entry changed')
        if text.count('"5348454E-472D-474F-5000-000000000001"') != 1:
            raise ValueError('paired display identity changed')
        text = text.replace('#include "gop_scanout.h"',
                            '#include "gop_scanout.h"\n#include "nabu_display_policy.h"')
        text = text.replace(marker, '    if (!nabuDisplayConsoleValid(fVideo.v_baseAddr, fVideo.v_width, fVideo.v_height, fVideo.v_rowBytes)) return false;\n' + marker)
        text = text.replace('"5348454E-472D-474F-5000-000000000001"', 'NABU_DISPLAY_UUID')
        text = text.replace('Sheng Boot Framebuffer', 'Xiaomi Pad 5 Boot Framebuffer')
        text = text.replace('#include "nabu_display_policy.h"', '#include "nabu_display_policy.h"\n#include "nabu_cpu_present.h"\n#include "nabu_display_trace.h"\n#include "nabu_source_maps.h"\n#include "nabu_vsync25.h"')
        text = text.replace('    void copySurface(IOSurface *);',
                            '    bool copyLayer(IOSurface *, const NabuGopRect &, const NabuGopRect &, bool, unsigned);\n    void failCurrentSwap(IOMFBSwapIORequest *);')
        text = text.replace('    uint32_t fSurfaceFormat = 0;\n    uint64_t fSourceWidth = 0, fSourceHeight = 0;\n', '')
        text = text.replace('    uint64_t fLayerShape = 0;',
                            '    uint32_t *fComposition = nullptr;\n    NabuGopSourceMap fSourceMaps[4]{};\n    NabuGopSourceReaper *fSourceReaper = nullptr;\n    uint64_t fCompleted = 0, fCopyFailures = 0, fVsyncSent = 0, fSwapInfoSendReports = 0;')
        text = text.replace('    fScanout = reinterpret_cast<uint8_t *>(fScanoutMap->getVirtualAddress());', '    fScanout = reinterpret_cast<uint8_t *>(fScanoutMap->getVirtualAddress());\n    fSourceReaper = NabuGopSourceReaper::create();\n    if (!fSourceReaper) { OSSafeReleaseNULL(fScanoutMap); return false; }')
        text = text.replace('        IOLog("NativeGOPDisplay: IOMobileFramebufferLegacy::start failed\\n");', '        IOLog("NativeGOPDisplay: IOMobileFramebufferLegacy::start failed\\n");\n        IOMemoryMap *empty[4]{}; fSourceReaper->finish(empty); fSourceReaper = nullptr;')
        text = text.replace('    IOMobileFramebufferLegacy::stop(provider);', '    IOMobileFramebufferLegacy::stop(provider);\n    if (fSourceReaper) {\n        IOMemoryMap *maps[4]{};\n        for (unsigned i = 0; i < 4; i++) { maps[i] = fSourceMaps[i].mapping; fSourceMaps[i] = {}; }\n        fSourceReaper->finish(maps); fSourceReaper = nullptr;\n    }')
        # enable() alone leaves a new IOTimerEventSource at deadline zero.
        # Start the callback chain only after the family has finished start(),
        # independently of the first userspace swap. The callback and native
        # power/queue operations share the same work-loop gate.
        started = '    IOLog("NativeGOPDisplay: started, display %ux%u\\n", display_width(), display_height());\n    return true;'
        if text.count(started) != 1:
            raise ValueError('paired framebuffer start completion changed')
        text = text.replace(started, '''    IOLog("NativeGOPDisplay: started, display %ux%u\\n", display_width(), display_height());
    IOReturn armed = fVBL ? fVBL->setTimeoutMS(16) : kIOReturnNotReady;
    IOLog("NABU-DISPLAY: refresh-bootstrap result=%#x\\n", armed);
    if (armed != kIOReturnSuccess) {
        stop(provider);
        return false;
    }
    return true;''')
        fired = '    static_cast<NativeGOPDisplay *>(owner)->swap_begin_gated();'
        if text.count(fired) != 1:
            raise ValueError('paired framebuffer timer callback changed')
        text = text.replace(fired, '''    auto *display = static_cast<NativeGOPDisplay *>(owner);
    if (!display->fTicks) IOLog("NABU-DISPLAY: first-timer-fired\\n");
    // Only actual timer firings are refresh events. The family's gate also
    // serializes native registration/removal and stop with this callback.
    if (display->fDisplayOn && nabuVsync25TickGated(display, 16000000u)) {
        ++display->fVsyncSent;
        if (display->fVsyncSent <= 4 || display->fVsyncSent % 625 == 0)
            IOLog("NABU-DISPLAY: native-vsync sent=%llu count=%llu clock=%llu interval=%llu\\n",
                  (unsigned long long)display->fVsyncSent,
                  (unsigned long long)nabu_vsync25::load<uint64_t>(display, nabu_vsync25::kCount),
                  (unsigned long long)nabu_vsync25::load<uint64_t>(display, nabu_vsync25::kLastMach),
                  (unsigned long long)nabu_vsync25::load<uint64_t>(display, nabu_vsync25::kInterval));
    }
    display->swap_begin_gated();''')
        notified = '    powerstate_notify(true, on);'
        if text.count(notified) != 1:
            raise ValueError('paired framebuffer power notification changed')
        text = text.replace(notified, '''    if (!fFrames) IOLog("NABU-DISPLAY: power-notify-begin on=%u clients=%u\\n", on, at<void *>(this, 0x17c8) != nullptr);
    powerstate_notify(true, on);
    if (!fFrames) IOLog("NABU-DISPLAY: power-notify-done on=%u\\n", on);''')
        completion_time = '    if (at<uint16_t>(this, kSwapInfoHeld)) {'
        if text.count(completion_time) != 1:
            raise ValueError('paired framebuffer completion report changed')
        text = text.replace(completion_time, '''    if (fCompleted < 4)
        IOLog("NABU-DISPLAY: swap-info swap=%u clock=%llu interval=%llu count=%llu type3=%u type5=%u held=%u\\n",
              swapID, (unsigned long long)info->words[0],
              (unsigned long long)nabu_vsync25::load<uint64_t>(this, nabu_vsync25::kInterval),
              (unsigned long long)nabu_vsync25::load<uint64_t>(this, nabu_vsync25::kCount),
              at<void *>(this, nabu_vsync25::kType3Head) != nullptr,
              at<void *>(this, nabu_vsync25::kType5Head) != nullptr,
              at<uint16_t>(this, kSwapInfoHeld));
''' + completion_time)
        completion_send = '''    return userClientSendAsyncResult(&at<io_user_reference_t>(request, kNotifyAsyncReference), kIOReturnSuccess, args, count,
                                     kIOUserNotifyOptionCanDrop);'''
        if text.count(completion_send) != 1:
            raise ValueError('paired framebuffer completion send changed')
        text = text.replace(completion_send, '''    IOReturn result = userClientSendAsyncResult(&at<io_user_reference_t>(request, kNotifyAsyncReference), kIOReturnSuccess, args, count,
                                     kIOUserNotifyOptionCanDrop);
    if (++fSwapInfoSendReports <= 8)
        IOLog("NABU-DISPLAY: swap-info-send swap=%u mask=%#x words=%u clock=%llu result=%#x\\n",
              info->swapID, mask, count, (unsigned long long)info->words[0], result);
    return result;''')
        text = text.replace('            dropSwap_gated(swap, false);', '            failCurrentSwap(swap);')
        begin = text.index('// Copy the compatible scene before completing the swap.')
        end = text.index('static constexpr uint32_t kFormatBGRA', begin)
        text = text[:begin] + text[end:]
        begin = text.index('void NativeGOPDisplay::copySurface(')
        end = text.index('static bool appendNumber(', begin)
        text = text[:begin] + '#include "nabu_cpu_present.inc"\n\n' + text[end:]
        text = text.replace('    if (fHalfToByte)\n        IOFree(fHalfToByte, 65536);',
                            '    if (fComposition) IOFree(fComposition, uint64_t(fVideo.v_width) * fVideo.v_height * 4);\n    fComposition = nullptr;\n    if (fHalfToByte)\n        IOFree(fHalfToByte, 65536);')
        heartbeat = '    if (++fTicks % 625 == 0 && fSubmitted + fFrames + fDropped + fStalled != fReported)'
        if text.count(heartbeat) != 1:
            raise ValueError('paired framebuffer heartbeat entry changed')
        text = text.replace(heartbeat, '''    unsigned sessionLog = 0;
    if (((fTicks < 7500 && (fTicks == 0 || fTicks % 625 == 624)) ||
         (fTicks >= 7500 && fTicks < 75000 && fTicks % 3750 == 3749)) &&
        PE_parse_boot_argn("nabu-session-log", &sessionLog, sizeof(sessionLog)) && sessionLog == 1)
        IOLog("NABU-DISPLAY: vbl=%llu on=%u power=%u/%u submitted=%llu shown=%llu wait_failures=%llu queue=%p current=%p vsync_clients=%u\\n",
              fTicks, fDisplayOn, at<uint32_t>(this, kSystemPowerState), at<uint32_t>(this, kAPIPowerState),
              fSubmitted, fFrames, fWaitFailures, at<IOMFBSwapIORequest *>(this, kSwapQueueFirst),
              at<IOMFBSwapIORequest *>(this, kCurrentSwap), at<void *>(this, 0x1808) != nullptr);
''' + heartbeat)
        # All board diagnostics bypass the console, including its first power
        # notification and the successful copy/completion after console disable.
        text = text.replace('IOLog(', 'nabuDisplayLog(')
        source.write_text(text)
        for name in ('nabu_display_policy.h', 'nabu_gop_scanout.h', 'nabu_cpu_present.h', 'nabu_cpu_present.inc', 'nabu_source_maps.h', 'nabu_display_trace.h', 'nabu_vsync25.h'):
            path = root / 'platform/nabu/macos26/display' / name
            shutil.copyfile(path, destination / name)
            board_inputs[name] = digest(path.read_bytes())
    scanout = root / 'platform/sheng/macos26/display/gop_scanout.h'
    shutil.copyfile(scanout, destination / scanout.name)
    layers = root / f'platform/{board}/macos26/display/gop_layers.h'
    shutil.copyfile(layers, destination / layers.name)
    owner = root / 'platform/sheng/macos26/log/framebuffer_owner.h'
    shutil.copyfile(owner, destination / owner.name)
    evidence = dict(board=board, board_input_sha256=board_inputs,
                    display_uuid='4E414255-2D47-4F50-8000-000000000001' if board == 'nabu' else '5348454E-472D-474F-5000-000000000001',
                    reference=f'https://github.com/{repository}/tree/{revision}',
                    input_sha256=hashes, patch_sha256=digest(patch.read_bytes()),
                    source_sha256=digest(source.read_bytes()),
                    header_sha256=digest((destination / 'IOMobileFramebufferABI.h').read_bytes()),
                    scanout_sha256=digest(scanout.read_bytes()),
                    layer_selection_sha256=digest(layers.read_bytes()),
                    abi=manifest['abi'],
                    build='25F71', default_off=True, boot_argument='native-gop-display=1',
                    supported_cpus=[1, 2, 4, 8], framebuffer_owner_sha256=digest(owner.read_bytes()),
                    gpu_acceleration=False, publishes_vm_identity=False,
                    runtime_qualified=False, source=str(source))
    if board == 'nabu':
        evidence['interface_reference'] = {
            'UniXNU': '268bfa1c587db5ee616e955cdfd97a281500a6fb',
            'UniXNU-sheng': '81f43ebaaa3317a5917b76093f7bc44c4949bc02',
            'lifecycle': 'package/LegacyDisplayProbe.cpp',
            'abi_reused': False,
            'reason': 'mainline iOS17 ABI differs from paired 25F71 473-slot family',
        }
        evidence['compositor_qualified'] = False
        evidence['session_heartbeat'] = 'nabu-session-log=1; software callback, queue, actual copy and completion counters'
        evidence['refresh_bootstrap'] = 'arm 16ms after successful native family start; callback entry precedes queue work; power changes preserve cadence'
        evidence['diagnostic_channel'] = 'bounded direct RAM append, timestamp/console_disabled/owner; no console sink or IOLog'
        evidence['native_vsync'] = '25F71 original note_vbl_ISR for type3 or type5 subscribers; swap_notify_gated for type5 only; gated actual timer firing, display on; 16000000ns seed; native continuous clock'
        evidence['cpu_presentation'] = '25F71 persistent planes; source/destination rectangles; opaque scene planes; plane-1 premultiplied cursor; source-backed staging before completion'
        evidence['scanout_geometry'] = dict(base=0x9c000000, width=1600,
                                            height=2560, stride=6400, rotation=0)
    (destination / 'source.json').write_text(json.dumps(evidence, indent=2) + '\n')
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--board', choices=('sheng', 'nabu'), default='sheng')
    args = parser.parse_args()
    print(json.dumps(prepare(Path(__file__).resolve().parents[1], args.output.resolve(), board=args.board), indent=2))
