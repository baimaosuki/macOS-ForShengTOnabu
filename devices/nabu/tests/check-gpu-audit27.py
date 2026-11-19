#!/usr/bin/env python3
"""Replay the prepared kernel's actual addon path routing and render-server policy."""
import argparse,json,shutil,subprocess,tempfile
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('--xnu',type=Path,required=True)
p.add_argument('--output',type=Path,required=True);a=p.parse_args()
d=Path(__file__).resolve().parents[1];g=d/'package/gpu/a640'
source=(a.xnu/'bsd/kern/kern_exec.c').read_text()
begin=source.index('const bool bundle_file =',source.index('native_gpu_addon_install(proc_t'))
end=source.index('#else',begin)
route=source[begin:end]
assert 'const bool shader_cache_app =' in route
publication=(g/'NativeAdrenoPublish.cpp').read_text()
for anchor in ('nabuGpuRenderServer(caller)','!nabuGpuRenderServer(who)',
               'nabuGpuRenderServer(name_)','!nabuGpuRenderServer(opener)'):
    assert anchor in publication
prefix=r'''
#include <cassert>
#include <cstdio>
#include <cstring>
#include "NabuGpuClientPolicy.h"
using vfs_context_t=int;
static int vfs_context_kernel(){return 7;}
struct Route {char path[256]{};int context;};
static Route installed(const char *name){
 Route result;char (&path)[256]=result.path;vfs_context_t ctx=3;
'''
tail=r'''
 result.context=file_ctx;return result;
}
int main(){
 for(const char *name:{"WindowServer","backboardd"})assert(nabuGpuRenderServer(name));
 for(const char *name:{"WindowServerHelper","backboardd2","Finder",""})assert(!nabuGpuRenderServer(name));
 assert(!nabuGpuRenderServer(nullptr));
 const char *app="Applications/Shader Cache.app/Contents/MacOS/ShaderCacheRegister";
 auto output=installed(app);
 assert(!strcmp(output.path,"/Applications/Shader Cache.app/Contents/MacOS/ShaderCacheRegister") && output.context==7);
 output=installed("Applications/Shader Cache.app/Contents/_CodeSignature/CodeResources");
 assert(!strcmp(output.path,"/Applications/Shader Cache.app/Contents/_CodeSignature/CodeResources") && output.context==7);
 output=installed("GPUBundles/NativeAdrenoMetal.bundle/Contents/Info.plist");
 assert(!strcmp(output.path,"/Library/GPUBundles/NativeAdrenoMetal.bundle/Contents/Info.plist") && output.context==7);
 output=installed("Library/LaunchAgents/org.unixnu.ShaderCache.register.plist");
 assert(!strcmp(output.path,"/Library/LaunchAgents/org.unixnu.ShaderCache.register.plist") && output.context==7);
 for(const char *name:{"bootshim/apfs_boot_util","Applications/Other.app/a","Applications/Shader Cache.app2/a"}){
  output=installed(name);char expected[256];snprintf(expected,sizeof expected,"/private/var/ngpu/%s",name);
  assert(!strcmp(output.path,expected) && output.context==3);
 }
 puts("PASS actual prepared-kernel addon routing: signed app/LaunchAgent paths agree, other destinations preserved; exact macOS/iPadOS render-server policy");
}
'''
a.output.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='nabu-gpu-audit-') as temporary:
    root=Path(temporary);cpp=root/'test.cpp';cpp.write_text('#include <initializer_list>\n'+prefix+route+tail)
    command=[shutil.which('clang++') or 'c++','-std=c++17','-O1','-g','-Wall','-Wextra','-Werror',
             '-fsanitize=address,undefined','-I',str(g),str(cpp),'-o',str(root/'test')]
    subprocess.run(command,check=True)
    result=subprocess.run([str(root/'test')],check=True,capture_output=True,text=True)
    (a.output/'run.log').write_text(result.stdout);print(result.stdout,end='')
(a.output/'verification.json').write_text(json.dumps(dict(passed=True,
    prepared_kernel_routing_executed=True,shader_cache_application='/Applications/Shader Cache.app',
    other_addon_destinations_preserved=True,render_servers=['WindowServer','backboardd'],
    prefix_lookalikes_refused=True,sanitizers=['ASan','UBSan'],hardware_tested=False),indent=2)+'\n')
