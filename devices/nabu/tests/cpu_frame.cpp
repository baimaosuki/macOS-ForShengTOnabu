// SPDX-License-Identifier: GPL-3.0-or-later
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include "nabu_cpu_frame.h"
int main() {
    uint32_t source[12]{}, scene[12]{}, scanout[15]{};
    uint32_t cursor = 0x80800000;
    NabuCpuPlaneView planes[4]{};
    planes[0] = {reinterpret_cast<const uint8_t *>(source), sizeof(source), 16, 4, 3,
                  {0,0,4,3}, {0,0,4,3}, nullptr, true};
    planes[1] = {reinterpret_cast<const uint8_t *>(&cursor), 4, 4, 1, 1,
                  {0,0,1,1}, {1,1,1,1}, nullptr, true};
    // The pointer and allocation stay fixed through all90explicit updates.
    for (uint32_t frame = 1; frame <= 90; ++frame) {
        for (auto &p : source) p = frame;
        assert(nabuCpuCompose(scene, sizeof(scene), 4, 3, 0, planes));
        assert(nabuCpuScanout(reinterpret_cast<volatile uint8_t *>(scanout), sizeof(scanout), 20, scene, 4, 3));
        assert(scanout[0] == (0xff000000U | frame));
        assert(scanout[6] == nabuGopCursorOver(cursor, 0xff000000U | frame));
        assert(scanout[4] == 0 && scanout[9] == 0 && scanout[14] == 0);
    }
    uint32_t before[12]; memcpy(before, scene, sizeof(scene));
    planes[0].bytes = 4;
    assert(!nabuCpuCompose(scene, sizeof(scene), 4, 3, 0, planes));
    assert(!memcmp(before, scene, sizeof(scene)));
    planes[0].bytes = sizeof(source);
    planes[1].active = false;
    assert(nabuCpuCompose(scene, sizeof(scene), 4, 3, 0x123456, planes));
    planes[0].active = false;
    assert(nabuCpuCompose(scene, sizeof(scene), 4, 3, 0x123456, planes));
    for (auto p : scene) assert(p == 0xff123456);
    assert(!nabuCpuScanout(reinterpret_cast<volatile uint8_t *>(scanout), 4, 20, scene, 4, 3));

    uint32_t diagnostic[48]{};
    assert(nabuCpuStageBars(reinterpret_cast<volatile uint8_t *>(diagnostic), sizeof(diagnostic), 24, 4, 8, 7));
    for (unsigned y=0;y<8;y++) {
        assert(diagnostic[y*6]==0xffff0000U && diagnostic[y*6+1]==0xffffff00U && diagnostic[y*6+2]==0xff00ffffU);
        assert(diagnostic[y*6+3]==0 && diagnostic[y*6+4]==0 && diagnostic[y*6+5]==0);
    }
    uint32_t saved[48]; memcpy(saved,diagnostic,sizeof saved);
    assert(!nabuCpuStageBars(reinterpret_cast<volatile uint8_t *>(diagnostic), 4, 24, 4, 8, 7));
    assert(!memcmp(saved,diagnostic,sizeof saved));
    assert(!nabuCpuStageBars(reinterpret_cast<volatile uint8_t *>(diagnostic), sizeof(diagnostic), 24, 4, 7, 7));
    uint32_t shadow[12]{}; uint64_t written=0; bool seen=false;
    assert(nabuCpuScanout(reinterpret_cast<volatile uint8_t *>(scanout),sizeof(scanout),20,scene,4,3,shadow,false,&written,&seen));
    assert(written==48 && seen);
    assert(nabuCpuScanout(reinterpret_cast<volatile uint8_t *>(scanout),sizeof(scanout),20,scene,4,3,shadow,true,&written,&seen));
    assert(written==0);
    scene[5]=0xff112233;
    assert(nabuCpuScanout(reinterpret_cast<volatile uint8_t *>(scanout),sizeof(scanout),20,scene,4,3,shadow,true,&written,&seen));
    assert(written==16 && scanout[6]==0xff112233);
    for(unsigned y=0;y<3;y++)assert(scanout[y*5+4]==0);
}
