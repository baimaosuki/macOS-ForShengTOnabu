"""Compile the actual post-patch lifecycle helper, including RAM-only output."""
import ast
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LifecycleDiagnostics(unittest.TestCase):
    def test_actual_postimage_selection_budgets_and_console_handoff(self):
        compiler = next((shutil.which(name) for name in ('clang', 'clang-19', 'clang-18', 'clang-17', 'gcc') if shutil.which(name)), None)
        if not compiler or not shutil.which('patch'):
            self.skipTest('requires a C compiler and patch')
        baseline = ROOT / 'platform/nabu/macos26/desktop/patches/18-macos-boot-process-trace.patch'
        source = '\n'.join(line[1:] for line in baseline.read_text().splitlines()
                           if line.startswith('+') and not line.startswith('+++'))
        # Reuse only the host kernel API substitutes; this test compiles the
        # actual new patch's postimage, not a copied implementation.
        tree = ast.parse((ROOT / 'tests/test_nabu_boot_process_trace.py').read_text())
        common = next(ast.literal_eval(node.value) for node in ast.walk(tree)
                      if isinstance(node, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == 'common' for t in node.targets))
        capture = r'''
static int disableConsoleOutput=1;
static unsigned appends;
static char captured[200000];
static size_t captured_bytes;
void nabu_capture_append(const char *text,unsigned size){
 assert(disableConsoleOutput); assert(size && text[size-1]=='\n');
 assert(memchr(text,'\n',size)==text+size-1); /* one logical record */
 assert(captured_bytes+size<sizeof(captured));
 memcpy(captured+captured_bytes,text,size);captured_bytes+=size;
 captured[captured_bytes]=0; appends++;
}
'''
        checks = r'''
static void image(proc_t p,const char *name,const char *path,const char *args,size_t size,int argc){
 p->p_comm=name;struct image_params im={args,args+size,"executable_path=/fallback",path,argc};
 nabu_lifecycle_exec(p,&im);
}
int main(void){
 present=1;enabled=1;now=123;
 struct proc p={123,1,100,"SecurityAgent",0,0};
 const char args[]="SecurityAgent\0--mode\0login\0";
 assert(nabu_life_kind("SecurityAgent")==24 && nabu_life_kind("authd")==25);
 assert(nabu_life_kind("lsd")==26 && nabu_life_kind("launchservicesd")==27);
 assert(!nabu_life_kind("SecurityAgent.extra") && !nabu_life_kind("ViewBridge"));
 const char *names[]={"SecurityAgent","authd","lsd","launchservicesd"};
 for(unsigned i=0;i<4;i++){
  p.uid=100+i;image(&p,names[i],"/System/test",args,sizeof(args)-1,3);
  p.p_xstat=0;nabu_lifecycle_exit(&p);
 }
 assert(appends==8 && !refs && !cred_refs);
 assert(strstr(captured,"NABU-PROC t_ns=123 event=exec pid=123"));
 assert(strstr(captured,"argv=[SecurityAgent][--mode][login]"));
 const char *labels[]={"com.apple.security.agent","com.apple.security.agent.login",
                      "com.apple.lsd","com.apple.coreservices.launchservicesd"};
 for(unsigned i=0;i<4;i++){
  assert(nabu_life_proxy_kind(labels[i])==28+i);
  char proxy[160];memcpy(proxy,"xpcproxy",9);strcpy(proxy+9,labels[i]);
  p.uid=200+i;image(&p,"xpcproxy","/usr/libexec/xpcproxy",proxy,10+strlen(labels[i]),2);
  nabu_lifecycle_exit(&p);
 }
 assert(appends==16 && !nabu_life_proxy_kind("com.apple.ViewBridge"));
 assert(!nabu_life_proxy_kind("com.apple.security.agent.extra"));
 // Exhaust SecurityAgent's exec bucket; authd has its own remaining budget.
 for(unsigned i=0;i<10;i++){p.uid=300+i;image(&p,"SecurityAgent","/sa",args,sizeof(args)-1,3);}
 unsigned before=appends;
 p.uid=400;image(&p,"authd","/authd",args,sizeof(args)-1,3);assert(appends==before+1);
 now=6000000123ULL;p.uid=401;before=appends;
 image(&p,"SecurityAgent","/sa",args,sizeof(args)-1,3);assert(appends==before+1);
 assert(strstr(captured,"suppressed=7"));
 struct reason reason={3,0xabc,4};p.p_exit_reason=&reason;p.p_xstat=SIGABRT;
 nabu_lifecycle_exit(&p);assert(strstr(captured,"signal=6 core=0 reason_ns=3 reason_code=0xabc reason_flags=0x4"));
 // Maximum copied path and argv fit in the single bounded output buffer.
 char path[MAXPATHLEN],arg[900];memset(path,'p',sizeof(path)-1);path[sizeof(path)-1]=0;
 memset(arg,'a',sizeof(arg)-1);arg[sizeof(arg)-1]=0;
 p.uid=402;p.p_xstat=0;p.p_exit_reason=0;before=appends;
 image(&p,"lsd",path,arg,sizeof(arg),1);assert(appends==before+1);
 assert(strstr(captured,"argv_truncated=1"));assert(strstr(captured,path));
 path_error=5;p.uid=403;image(&p,"launchservicesd","bad",args,sizeof(args)-1,3);
 assert(strstr(captured,"path=/fallback path_error=5"));
 assert(!refs && !cred_refs);
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            target = folder / 'bsd/kern/kern_exec.c'
            target.parent.mkdir(parents=True)
            target.write_text(source + '\n')
            patch = ROOT / 'platform/nabu/macos26/desktop/lifecycle-diagnostics.patch'
            applied = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(patch)],
                                     cwd=folder, capture_output=True, text=True)
            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            postimage = target.read_text()
            helper = postimage[postimage.index('#define NABU_LIFE_SLOTS'):postimage.index('\n#endif')]
            emit = helper[helper.index('nabu_life_emit('):helper.index('\nstatic void\nnabu_lifecycle_exec')]
            self.assertNotIn('printf(', emit.replace('snprintf(', ''))
            src, binary = folder / 'trace.c', folder / 'trace'
            src.write_text(common + capture + helper + checks)
            build = subprocess.run([compiler, '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                                    '-Wno-format', '-fsanitize=address,undefined',
                                    str(src), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([binary], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
