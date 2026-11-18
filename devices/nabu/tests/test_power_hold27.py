"""Execute the production display power overrides without changing native PM ABI."""
from pathlib import Path
import ast,json,shutil,subprocess,tempfile
ROOT=Path(__file__).resolve().parents[3]
DRIVER=ROOT/'devices/nabu/drivers/GopFramebuffer'

def test_native_power_slot_and_owned_hold(tmp_path):
    source=(DRIVER/'NativeLegacyDisplay.cpp').read_text()
    start=source.index('    IOReturn requestApiPower(')
    end=source.index('    // The explicit offline system-client',start)
    methods=source[start:end]
    assert 'IOPMAckImplied' in methods
    fixture=json.loads((ROOT/'devices/nabu/scripts/legacy-display-26C5035r.json').read_text())
    assert fixture['methods']['requestApiPower']==345
    assert fixture['slots'][345][0]=='0xfffffe000ac2efbc'
    assert fixture['slots'][345][1]=='0x9852'
    name,suffix=fixture['names']['requestApiPower']
    assert suffix=='EmP9IOService'
    builder=ast.parse((ROOT/'devices/nabu/scripts/build-nabu-display27.py').read_text())
    fn=next(n for n in builder.body if isinstance(n,ast.FunctionDef) and n.name=='discriminator')
    ns={'struct':__import__('struct')}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'discriminator','exec'),ns)
    mangled=f'_ZN25IOMobileFramebufferLegacy{len(name)}{name}{suffix}'
    assert ns['discriminator'](mangled)==0x9852
    assert fixture['bindings']['_'+mangled]=='0xfffffe000ac2efbc'
    (tmp_path/'power-methods.inc').write_text(methods)
    path=tmp_path/'power.cpp'
    path.write_text(r'''
#include <assert.h>
#include <stdint.h>
#include <stdarg.h>
using IOReturn=int;
const int kIOReturnUnsupported=-1,kIOReturnSuccess=0,IOPMAckImplied=0;
struct IOService{};
static unsigned logs,pmLogs,probes,apiCalls,pmCalls;
static unsigned long apiOrdinal,pmOrdinal;
static void IOLog(const char*,...){++logs;}
static void native_legacy_pm_observed(unsigned,int,unsigned long){++pmLogs;}
struct Base {
 virtual IOReturn requestApiPower(unsigned long n,IOService*){++apiCalls;apiOrdinal=n;return 37;}
 virtual IOReturn setPowerState(unsigned long n,IOService*){++pmCalls;pmOrdinal=n;return 0;}
};
using IOMobileFramebufferLegacy=Base;
struct Display:Base {
 bool primary=true,bootMapping=true,logicalPM=true,nabuSleepRejected=false;
 void nabuStageProbe(unsigned){++probes;}
#include "power-methods.inc"
};
int main(){
 Display d;IOService service;
 assert(d.requestApiPower(0,&service)==kIOReturnUnsupported);
 assert(d.nabuSleepRejected && !apiCalls && logs==1);
 assert(d.requestApiPower(0,&service)==kIOReturnUnsupported && logs==1);
 assert(d.setPowerState(0,&service)==IOPMAckImplied && !pmCalls);
 assert(d.requestApiPower(1,&service)==37 && apiCalls==1 && apiOrdinal==1 && !d.nabuSleepRejected);
 assert(d.setPowerState(1,&service)==0 && pmCalls==1 && pmOrdinal==1 && probes==1);
 d.primary=false;
 assert(d.requestApiPower(0,&service)==37 && apiCalls==2 && apiOrdinal==0);
 assert(d.setPowerState(0,&service)==0 && pmCalls==2 && pmOrdinal==0);
 d.primary=true;d.bootMapping=false;d.logicalPM=false;
 assert(d.requestApiPower(0,&service)==37 && apiCalls==3);
 assert(d.setPowerState(0,&service)==0 && pmCalls==3);
}
''')
    binary=tmp_path/'power'
    subprocess.run([shutil.which('clang++') or 'c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',str(tmp_path),str(path),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True)

if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='nabu-power-hold-') as directory:
        test_native_power_slot_and_owned_hold(Path(directory))
    print('original slot/diversity, API-off rejection and PM acknowledgement checks passed')
