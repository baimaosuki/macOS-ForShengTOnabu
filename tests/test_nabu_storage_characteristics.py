"""Run actual staged publication and original IOStorage solid-state consumers."""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from nabu_storage_source import adapt_nabu_ram_medium, stage


def test_staged_ufs_ram_and_original_storage_consumers():
    driver = (ROOT / 'sources/macos26/IOStorageFamily/IOBlockStorageDriver.cpp').read_text()
    start = driver.index('        OSDictionary *dictionary = OSDynamicCast(OSDictionary, getProvider()->getProperty(kIOPropertyDeviceCharacteristicsKey));')
    consumer = driver[start:driver.index('        _ejectable = false;', start)]
    bsd = (ROOT / 'sources/macos26/IOStorageFamily/IOMediaBSDClient.cpp').read_text()
    start = bsd.index('        case DKIOCISSOLIDSTATE:')
    consumer_bsd = bsd[bsd.index('            OSDictionary * dictionary', start):bsd.index('        } break;', start)]
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        source, evidence = stage(ROOT, work / 'nabu', board='nabu')
        assert evidence['patches'][-1]['path'] == 'patches/UniXNU/0048-nabu-solid-state-media.patch'
        ufs = source.read_text()
        begin = ufs.index('bool NativeUfsBlock::start (IOService *provider)')
        end = ufs.index('\n}\n', begin) + 3
        actual_start = ufs[begin:end]
        assert actual_start.index('nabuSetSolidStateMedium') < actual_start.index('registerService')
        ram = adapt_nabu_ram_medium((ROOT / 'UniXNU/drivers/NativeRamBlock/NativeRamBlock.cpp').read_text())
        begin = ram.index('bool NativeRamBlock::start(IOService *provider)')
        end = ram.index('  IORegistryEntry *map', begin)
        ram_prefix = ram[begin:end] + '  registerService(); return true;\n}\n'
        # Both real start call sites execute the same failure handling before
        # their children can match. Remaining RAM initialization is not modeled.
        fixture = r'''
#include <cassert>
#include <cstdint>
#include <map>
#include <string>
#define NABU_STORAGE_CHARACTERISTICS_HOST_TEST 1
#define OSDynamicCast(type, object) dynamic_cast<type *>(object)
#define kIOPropertyDeviceCharacteristicsKey "Device Characteristics"
#define kIOPropertyMediumTypeKey "Medium Type"
#define kIOPropertyMediumTypeSolidStateKey "Solid State"
static bool dictionaryFailure, stringFailure, publishFailure;
struct OSObject {virtual ~OSObject()=default; virtual void release() {}};
struct OSString:OSObject {
 std::string value;explicit OSString(const char *s):value(s){}
 static OSString *withCString(const char *s){return stringFailure?nullptr:new OSString(s);}
 bool isEqualTo(const char *s){return value==s;}
};
struct OSDictionary:OSObject {
 std::map<std::string,OSObject *> entries;
 static OSDictionary *withCapacity(unsigned){return dictionaryFailure?nullptr:new OSDictionary;}
 static OSDictionary *withDictionary(OSDictionary *old){return dictionaryFailure?nullptr:new OSDictionary(*old);}
 bool setObject(const char *key,OSObject *value){entries[key]=value;return true;}
 OSObject *getObject(const char *key){return entries.count(key)?entries[key]:nullptr;}
};
static int gIOServicePlane;
struct IOService:OSObject {
 IOService *provider=nullptr;OSObject *characteristics=nullptr;
 virtual OSObject *getProperty(const char *s){assert(std::string(s)==kIOPropertyDeviceCharacteristicsKey);return characteristics;}
 OSObject *getProperty(const char *s,int){OSObject *v=getProperty(s);return v?v:provider?provider->getProperty(s,gIOServicePlane):nullptr;}
 bool setProperty(const char *s,OSObject *v){assert(std::string(s)==kIOPropertyDeviceCharacteristicsKey);if(publishFailure)return false;characteristics=v;return true;}
};
#include "NabuStorageCharacteristics.h"
struct IOBlockStorageDevice:IOService {
 bool started=false,stopped=false,registered=false;
 bool start(IOService *p){provider=p;started=true;return true;}
 void stop(IOService *){stopped=true;}
 void registerService(){assert(characteristics);registered=true;}
};
struct NativeUfsBlock:IOBlockStorageDevice {
 bool bounce=true,windowLock=true,online=false;
 bool start(IOService *);
};
struct NativeRamBlock:IOBlockStorageDevice {bool start(IOService *);};
''' + actual_start + ram_prefix + r'''
struct Consumer {
 IOService *provider;bool _solidState=false;
 IOService *getProvider(){return provider;}
 void read(){
''' + consumer + r'''
 }
};
static bool bsdConsumer(IOService *media){
 struct {IOService *media;} entry{media};auto *minor=&entry;
 uint32_t output=99;void *data=&output;
''' + consumer_bsd + r'''
 return output;
}
int main(){
 IOService root;
 NativeUfsBlock ufs;assert(ufs.start(&root));assert(ufs.registered && ufs.online);
 Consumer c{&ufs};c.read();assert(c._solidState);
 IOService media;media.provider=&ufs;IOService partition;partition.provider=&media;
 assert(bsdConsumer(&partition));
 NativeRamBlock ram;assert(ram.start(&root));assert(ram.registered);
 c={&ram};c.read();assert(c._solidState);
 OSDictionary old;OSString serial("board-serial");old.setObject("Serial Number",&serial);
 IOService existing;existing.characteristics=&old;assert(nabuSetSolidStateMedium(&existing));
 auto *changed=dynamic_cast<OSDictionary *>(existing.characteristics);
 assert(changed!=&old && changed->getObject("Serial Number")==&serial);
 assert(!old.getObject(kIOPropertyMediumTypeKey));
 for(unsigned failure=0;failure<3;failure++){
  dictionaryFailure=failure==0;stringFailure=failure==1;publishFailure=failure==2;
  NativeUfsBlock bad;assert(!bad.start(&root));assert(bad.stopped && !bad.registered && !bad.online);
  NativeRamBlock badram;assert(!badram.start(&root));assert(badram.stopped && !badram.registered);
 }
}
'''
        test = work / 'test.cpp'
        test.write_text(fixture)
        binary = work / 'test'
        subprocess.run(['c++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=undefined', '-I', str(source.parent), str(test), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
        sheng, _ = stage(ROOT, work / 'sheng', board='sheng')
        assert 'NabuStorageCharacteristics' not in sheng.read_text()
