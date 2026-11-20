#!/usr/bin/env python3
"""Replay actual NT36523 decoding/polling over scripted transport and time."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

DEVICE = Path(__file__).resolve().parents[1]


def function(text, signature):
    start = text.index(signature)
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


PEER = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <array>
#include <vector>
#include "NabuTouchPointer.h"
#define TLOG(...) ((void)0)
static uint32_t nabu_touch_stats[8]{};
static uint32_t gXMax=1600,gYMax=2560,gSwapXY=0,gInvertX=0,gInvertY=0;
constexpr unsigned kFingers=10;
struct Frame{unsigned count=0;int ids[kFingers],xs[kFingers],ys[kFingers];};
struct Firmware{};
struct Sample{std::array<uint8_t,80> data{};bool latch=false,ok=true;};
static std::vector<Sample> script;
static size_t sampleIndex=0;
static uint64_t now=1;
static bool cleared=false;
struct Done{};
struct Irq {bool base=true;
 uint32_t r(unsigned reg){assert(reg==0xc);return !cleared && script[sampleIndex].latch?1:0;}
 void w(unsigned reg,unsigned value){assert(reg==0xc && !value);cleared=true;}
};
static uint64_t mach_absolute_time(){return now;}
static void absolutetime_to_nanoseconds(uint64_t ticks,uint64_t *out){*out=ticks;}
static uint64_t deadlineUs(uint64_t us){return now+us*1000;}
static bool ntRead(uint32_t address,uint8_t *out,unsigned bytes){
 assert(address==0x2fe00 && bytes<=80);if(!script[sampleIndex].ok)return false;
 memcpy(out,script[sampleIndex].data.data(),bytes);return true;
}
static bool ntDownload(const Firmware&){assert(false && "unexpected watchdog reload");return false;}
static void IOSleep(unsigned ms){now+=uint64_t(ms)*1000000;cleared=false;if(++sampleIndex==script.size())throw Done{};}
struct Hid {
 NabuPointerState pointer;std::vector<std::array<uint8_t,6>> reports;
 void deliver(const Frame &frame){NabuPointerContact contacts[10];
  for(unsigned i=0;i<frame.count;++i)contacts[i]={frame.ids[i],uint16_t(frame.xs[i]),uint16_t(frame.ys[i])};
  std::array<uint8_t,6> report{};if(pointer.encode(contacts,frame.count,report.data()))reports.push_back(report);
 }
};
'''
TEST = r'''
static Sample contact(unsigned x,unsigned y,bool latch=false){Sample result;result.latch=latch;
 result.data[0]=(1<<3)|1;result.data[1]=x>>4;result.data[2]=y>>4;result.data[3]=(x<<4)|(y&15);
 uint8_t sum=0;for(unsigned i=0;i<64;++i)sum+=result.data[i];result.data[64]=uint8_t(-sum);return result;
}
static Sample lift(){return Sample{};}
static void run(Hid &hid){sampleIndex=0;now=1;cleared=false;memset(nabu_touch_stats,0,sizeof nabu_touch_stats);
 try{replay(&hid);}catch(const Done&){}
}
int main(){
 // Repeated valid stationary data with missing IRQs must keep a drag held.
 script.assign(150,contact(100,200));script[0].latch=true;
 Hid held;run(held);assert(held.reports.size()==1 && held.pointer.down);
 // IRQ bursts at unchanged coordinates do not make duplicate HID traffic.
 script.assign(150,contact(100,200,true));Hid repeated;run(repeated);
 assert(repeated.reports.size()==1 && repeated.pointer.down);
 // A real movement and release each reach the pointer consumer exactly once.
 script.assign(50,contact(100,200,true));script.insert(script.end(),50,contact(200,300,true));
 script.insert(script.end(),50,lift());Hid drag;run(drag);
 assert(drag.reports.size()==3 && drag.reports[0][1]==1 && drag.reports[1][1]==1 && drag.reports[2][1]==0);
 assert(!drag.pointer.down);
 // Bad checksums are refused; prolonged invalid traffic releases a stuck
 // press, and a later identical but valid packet may acquire it again.
 Sample bad=contact(100,200,true);bad.data[64]^=1;
 script.assign(1,contact(100,200,true));script.insert(script.end(),60,bad);
 script.insert(script.end(),40,contact(100,200,true));script.insert(script.end(),20,lift());
 Hid recovered;run(recovered);assert(nabu_touch_stats[3]>=60);
 assert(recovered.reports.size()==4 && recovered.reports[0][1]==1 && recovered.reports[1][1]==0 &&
  recovered.reports[2][1]==1 && recovered.reports[3][1]==0);
 // Events use the stock 64+checksum layout, including coordinate limits.
 auto edge=contact(1600,2560);Frame decoded;bool watchdog=false;
 assert(decode(edge.data.data(),decoded,watchdog,0) && decoded.count==1 && decoded.xs[0]==32767 && decoded.ys[0]==32767);
 auto outside=contact(1601,2560);assert(decode(outside.data.data(),decoded,watchdog,0) && !decoded.count);
 puts("PASS actual decoder/poll loop: stationary hold, duplicate IRQs, drag/lift, invalid checksum timeout and recovery");
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline-ref')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    source = DEVICE / 'package/NabuTouch.cpp'
    pointer = DEVICE / 'package/NabuTouchPointer.h'
    inputs = [('current', source.read_text(), pointer.read_text(), True)]
    if args.baseline_ref:
        def old(path):
            return subprocess.check_output(['git', 'show', args.baseline_ref + ':' + str(path.relative_to(DEVICE.parents[1]))],
                                           cwd=DEVICE.parents[1], text=True)
        inputs.insert(0, ('before', old(source), old(pointer), False))
    results = []
    with tempfile.TemporaryDirectory(prefix='nabu-touch-events-') as temporary:
        root = Path(temporary)
        for name, text, header, should_pass in inputs:
            work = root / name
            work.mkdir()
            (work / 'NabuTouchPointer.h').write_text(header)
            decoder = function(text, 'int scale(') + '\n' + function(text, 'bool decode(')
            thread = function(text, 'void touchThread(')
            start = thread.index('uint8_t event[')
            loop = 'static void replay(Hid *hid){Irq irq;Firmware fw;\n' + thread[start:-1] + '\n}\n'
            cpp = work / 'test.cpp'
            cpp.write_text(PEER + decoder + loop + TEST)
            executable = work / 'test'
            subprocess.run([shutil.which('clang++') or 'c++', '-std=c++17', '-O1', '-g',
                            '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unused-variable',
                            '-fsanitize=address,undefined', '-I', str(work), str(cpp), '-o', str(executable)], check=True)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            (args.output / (name + '.log')).write_text(result.stdout + result.stderr)
            assert (result.returncode == 0) == should_pass, (name, result.returncode, result.stdout, result.stderr)
            results.append(dict(name=name, returncode=result.returncode, expected_pass=should_pass))
    report = dict(passed=True, actual_poll_loop_and_decoder=True, results=results,
                  stationary_press_preserved=True, repeated_reports_suppressed=True,
                  corrupt_data_never_accepted=True, stale_transport_releases_and_recovers=True,
                  sanitizers=['ASan', 'UBSan'], hardware_tested=False)
    (args.output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
