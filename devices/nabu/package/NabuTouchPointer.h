// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once
#include <stdint.h>

// Report 1: left button, absolute X/Y in the panel's normalized 0..32767 range.
static const uint8_t kNabuPointerDescriptor[] = {
    0x05,0x01,0x09,0x02,0xa1,0x01,0x85,0x01,0x09,0x01,0xa1,0x00,
    0x05,0x09,0x19,0x01,0x29,0x01,0x15,0x00,0x25,0x01,
    0x75,0x01,0x95,0x01,0x81,0x02,0x75,0x07,0x95,0x01,0x81,0x03,
    0x05,0x01,0x09,0x30,0x09,0x31,0x15,0x00,0x26,0xff,0x7f,
    0x75,0x10,0x95,0x02,0x81,0x02,0xc0,0xc0
};
struct NabuPointerContact { int id; uint16_t x,y; };
struct NabuPointerState {
    int primary = -1;
    uint16_t x = 0, y = 0;
    bool down = false, waitAllUp = false;
    bool encode(const NabuPointerContact *contacts, unsigned count, uint8_t out[6]) {
        if (count && !contacts) return false;
        if (!count) waitAllUp = false;
        int found = -1;
        if (primary >= 0) {
            for (unsigned i=0;i<count;++i) if(contacts[i].id==primary) {found=int(i);break;}
        } else if (count && !waitAllUp) { found=0;primary=contacts[0].id; }
        const bool nowDown=found>=0;
        if (nowDown) {
            x=contacts[found].x>32767?32767:contacts[found].x;
            y=contacts[found].y>32767?32767:contacts[found].y;
        } else {
            if(primary>=0) {primary=-1;waitAllUp=count!=0;}
            if(!down)return false;
        }
        down=nowDown;
        out[0]=1;out[1]=nowDown?1:0;
        out[2]=uint8_t(x);out[3]=uint8_t(x>>8);
        out[4]=uint8_t(y);out[5]=uint8_t(y>>8);
        return true;
    }
};
