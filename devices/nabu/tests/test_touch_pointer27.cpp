// SPDX-License-Identifier: BSD-2-Clause-Patent
#include "../package/NabuTouchPointer.h"
#include <assert.h>
#include <string.h>
int main() {
    NabuPointerState s;uint8_t r[6];
    assert(!s.encode(nullptr,0,r));
    NabuPointerContact c[]={{3,100,200},{7,300,400}};
    assert(s.encode(c,2,r) && r[0]==1 && r[1]==1 && r[2]==100 && r[4]==200);
    assert(!s.encode(c,2,r) && s.down); // identical samples retain the press without duplicate reports
    NabuPointerContact reordered[]={{7,350,450},{3,150,250}};
    assert(s.encode(reordered,2,r) && r[2]==150 && r[4]==250);
    // Lifting the primary must release the mouse, not drag to another finger.
    assert(s.encode(c+1,1,r) && r[1]==0 && r[2]==150 && r[4]==250);
    assert(!s.encode(c+1,1,r));assert(!s.encode(nullptr,0,r));
    c[0]={9,65535,65535};assert(s.encode(c,1,r));
    assert(r[1]==1 && r[2]==255 && r[3]==127 && r[4]==255 && r[5]==127);
    assert(s.encode(nullptr,0,r) && r[1]==0);assert(!s.encode(nullptr,0,r));
    assert(!s.encode(nullptr,1,r));
    // Descriptor: report ID 1, one button, seven padding bits, two absolute axes.
    unsigned reportSize=0,reportCount=0,bits=0;bool absolute=false;
    for(unsigned i=0;i<sizeof(kNabuPointerDescriptor);) {
        uint8_t tag=kNabuPointerDescriptor[i++];unsigned n=tag&3;n=n==3?4:n;
        assert(i+n<=sizeof(kNabuPointerDescriptor));unsigned value=0;
        for(unsigned j=0;j<n;++j)value|=unsigned(kNabuPointerDescriptor[i+j])<<(j*8);
        if(tag==0x75)reportSize=value;
        if(tag==0x95)reportCount=value;
        if(tag==0x81){bits+=reportSize*reportCount;if(reportSize==16)absolute=(value&4)==0;}
        i+=n;
    }
    assert(bits==40 && absolute); // 5-byte payload plus report ID.
}
