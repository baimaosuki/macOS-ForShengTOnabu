// SPDX-License-Identifier: GPL-2.0-only
// Command DB v1 and RSC v2.7 layout: Linux cmd-db.c / rpmh-rsc.c.
#pragma once
#include <stdint.h>
#include <stddef.h>
namespace nabu {
inline uint16_t le16(const uint8_t *p){return uint16_t(p[0])|uint16_t(p[1])<<8;}
inline uint32_t le32(const uint8_t *p){return uint32_t(le16(p))|uint32_t(le16(p+2))<<16;}
inline bool cmdDbAddress(const uint8_t *db,size_t bytes,const char *name,uint32_t &address) {
    if(bytes<144 || le32(db+4)!=0x0c0330db)return false;
    unsigned len=0;while(name[len]&&len<8)++len;
    if(!len || (len==8 && name[len]))return false;
    for(unsigned h=0;h<8;++h){
        const uint8_t *header=db+8+h*16;
        unsigned count=le16(header+6),offset=le16(header+2);
        if(size_t(offset)+size_t(count)*24>bytes-144)return false;
        for(unsigned i=0;i<count;++i){
            const uint8_t *entry=db+144+offset+i*24;
            bool equal=true;
            for(unsigned j=0;j<8;++j)if(entry[j]!=(j<len?uint8_t(name[j]):0))equal=false;
            if(equal){address=le32(entry+16);return address!=0;}
        }
    }
    return false;
}
template<class Io> class RpmhTcs {
    Io &io;
public:
    explicit RpmhTcs(Io &b):io(b){}
    bool send(unsigned tcs,uint32_t address,uint32_t data) {
        if(tcs>=2 || !address)return false; // DTS ACTIVE_TCS are 0 and 1.
        const uint32_t base=0xd00+tcs*0x2a0;
        if((io.read(base+0x14)&(1u<<24)) || io.read(base+0x1c))return false;
        uint32_t ctrl=io.read(base+0x14)&~(1u<<24);
        io.write(base+0x14,ctrl);io.write(base+0x14,ctrl&~(1u<<16));
        io.write(0xd08,1u<<tcs); // stale completion IRQ clear
        io.write(base+0x10,1);io.write(base+0x30,8|(1u<<8)|(1u<<16));
        io.write(base+0x34,address);io.write(base+0x38,data);io.write(base+0x1c,1);
        io.write(base+0x14,1u<<16);io.write(base+0x14,(1u<<16)|(1u<<24));
        bool complete=false;
        for(unsigned i=0;i<20000;i+=10){
            if((io.read(0xd04)&(1u<<tcs)) && (io.read(base+0x3c)&(1u<<16))){complete=true;break;}
            io.delayUs(10);
        }
        // Never reuse a still-running TCS after a timeout.
        if(!complete)return false;
        io.write(base+0x14,1u<<16);io.write(base+0x14,0);
        io.write(base+0x1c,0);io.write(0xd08,1u<<tcs);
        return true;
    }
};
}
