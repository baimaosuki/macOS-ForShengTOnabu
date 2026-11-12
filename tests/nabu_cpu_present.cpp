#include <cassert>
#include <cstdint>
#include <algorithm>
#include "../platform/nabu/macos26/display/nabu_cpu_present.h"

int main() {
    const uint32_t red = 0x00ff0000, blue = 0x000000ff, green = 0x0000ff00;
    uint32_t output[8] = {0xdeadbeef, blue, blue, blue, blue, blue, blue, 0xdeadbeef};
    const uint32_t source[] = {green, red, green, red};
    NabuGopRect src{1,0,2,1}, dst{-1,0,4,1}; NabuGopClip clip{};
    assert(nabuGopClip(src,dst,4,1,6,1,clip));
    nabuGopLayerRow(output+1,source,src,dst,clip,nullptr,false);
    // Clipping must preserve the original scaling phase and source crop.
    assert(output[0] == 0xdeadbeef && output[7] == 0xdeadbeef);
    assert(output[1] == (red|0xff000000) && output[2] == (green|0xff000000));
    assert(output[3] == (green|0xff000000) && output[4] == blue);
    assert(!nabuGopClip({-1,0,2,1}, dst,4,1,6,1,clip));
    assert(!nabuGopClip({3,0,2,1}, dst,4,1,6,1,clip));
    assert(!nabuGopClip(src,{0,0,-1,1},4,1,6,1,clip));
    assert(nabuGopClip(src,{INT32_MIN,0,INT32_MAX,1},4,1,6,1,clip));
    assert(clip.x0 >= clip.x1);
    // A scene buffer can have zero alpha and is still copied as opaque.
    assert(nabuGopCursorOver(0,red) == red);
    assert(nabuGopCursorOver(0x80800000,0xff0000ff) == 0xff80007f);
    // Retained root + cursor-only replacement: rebuilding restores old cursor.
    uint32_t root[] = {red,red,red,red}, transparentCursor[] = {0,0xff00ff00};
    NabuGopRect full{0,0,4,1}, cursorSrc{0,0,2,1}, cursorDst{0,0,2,1};
    assert(nabuGopClip(full,full,4,1,4,1,clip));
    nabuGopLayerRow(output,root,full,full,clip,nullptr,false);
    assert(nabuGopClip(cursorSrc,cursorDst,2,1,4,1,clip));
    nabuGopLayerRow(output,transparentCursor,cursorSrc,cursorDst,clip,nullptr,true);
    assert(output[0] == 0xffff0000 && output[1] == 0xff00ff00);
    assert(nabuGopClip(full,full,4,1,4,1,clip));
    nabuGopLayerRow(output,root,full,full,clip,nullptr,false);
    cursorDst.x = 2;
    assert(nabuGopClip(cursorSrc,cursorDst,2,1,4,1,clip));
    nabuGopLayerRow(output,transparentCursor,cursorSrc,cursorDst,clip,nullptr,true);
    assert(output[1] == 0xffff0000 && output[2] == 0xffff0000 && output[3] == 0xff00ff00);
    uint8_t table[65536]{};table[0x3c00]=255;table[0x3800]=128;
    uint16_t half[] = {0x3c00,0x3800,0,0};
    NabuGopRect one{0,0,1,1};
    assert(nabuGopClip(one,one,1,1,1,1,clip));
    nabuGopLayerRow(output,half,one,one,clip,table,false);
    assert(output[0] == 0xffff8000);
}
