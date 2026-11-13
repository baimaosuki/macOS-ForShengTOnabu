#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>

struct proc { const char *p_comm; int pid, ppid; };
typedef struct proc *proc_t;
struct image_params { const char *ip_startargv, *ip_endargv; int ip_argc; };
struct task { unsigned kernel_holds, user_holds; int active, pid; };
typedef struct task *task_t;
typedef int kern_return_t;
#define KERN_SUCCESS 0
#define relaxed 0
#define os_atomic_inc(p, order) (++*(p))
#define os_atomic_inc_orig(p, order) ((*(p))++)
static unsigned present, value, logs, holds, parent_waits;
static char journal[20000];
static int PE_parse_boot_argn(const char *name, void *out, unsigned size) {
    assert(!strcmp(name, "nabu-minimal-boot") && size == sizeof(uint32_t));
    if (present) *(uint32_t *)out = value;
    return present;
}
static int proc_getpid(proc_t p) { return p->pid; }
static int proc_ppid(proc_t p) { return p->ppid; }
static kern_return_t task_hold_and_wait(task_t task, bool conclave) {
    assert(!conclave && task->pid != 1 && !parent_waits);
    if (!task->active) return 5;
    task->kernel_holds++; holds++; return 0;
}
static void nabu_capture_append(const char *line, unsigned size) {
    assert(size && line[size-1] == '\n');
    assert(strlen(journal)+size < sizeof(journal));
    strncat(journal, line, size); logs++;
}
#include "minimal_boot.h"

static struct image_params image(const char *s, size_t n, int argc) {
    return (struct image_params){s, s+n, argc};
}

int main(void) {
    struct proc proxy = {"xpcproxy", 101, 1};
    struct task task = {0, 0, 1, 101};
    const char denied[] = "xpcproxy\0com.apple.endpointsecurityd\0";
    struct image_params im = image(denied, sizeof(denied)-1, 2);
    assert(!nabu_minimal_boot_apply(&proxy, &im, &task) && !holds && !logs);
    present=1; value=0;
    assert(!nabu_minimal_boot_apply(&proxy, &im, &task) && !holds);
    value=2; assert(!nabu_minimal_boot_apply(&proxy, &im, &task) && !holds);
    value=1;
    for(unsigned i=0;i<sizeof(nabu_minimal_boot_allowlist)/sizeof(*nabu_minimal_boot_allowlist);i++) {
        char args[256]; memcpy(args,"xpcproxy",9);
        strcpy(args+9,nabu_minimal_boot_allowlist[i]);
        struct image_params allow=image(args,10+strlen(args+9),2);
        assert(!nabu_minimal_boot_apply(&proxy,&allow,&task) && !holds);
    }
    /* Regression: actual 25F71 job labels observed parked on the device.
       These expectations are independent of iterating the production list. */
    const char *session_dependencies[]={"com.apple.bsd.dirhelper","com.apple.logind",
        "com.apple.mobile.usermanagerd","com.apple.UserEventAgent-System",
        "com.apple.UserEventAgent-LoginWindow","com.apple.UserEventAgent-Aqua",
        "com.apple.xpc.smd","com.apple.loginwindow"};
    for(unsigned i=0;i<sizeof(session_dependencies)/sizeof(*session_dependencies);i++) {
        char args[256];memcpy(args,"xpcproxy",9);strcpy(args+9,session_dependencies[i]);
        struct image_params allow=image(args,10+strlen(args+9),2);
        assert(!nabu_minimal_boot_apply(&proxy,&allow,&task) && !holds && !logs);
    }
    /* Real MS capture regression and the explicitly requested namespaces. */
    const char *dynamic_jobs[]={
        "com.apple.loginwindow.49A8D542-60C9-45F4-B0A0-7FECD5AD98D4",
        "com.apple.security.agent.login.49a8d542-60c9-45f4-b0a0-7fecd5ad98d4",
        "com.apple.loginwindow.session", "com.apple.WindowServer.session",
        "com.apple.SecurityAgent.session", "com.apple.coreservices.example",
        "com.apple.cfprefsd.xpc.agent.session", "com.apple.distnoted.xpc.agent.session",
        "com.apple.iconservices.iconservicesagent.session", "com.apple.tccd.system.session",
        "com.apple.sandboxd", "com.apple.diskarbitrationd",
        "com.apple.iconservices.iconservicesd", "com.apple.iconservices.iconservicesagent"};
    for(unsigned i=0;i<sizeof(dynamic_jobs)/sizeof(*dynamic_jobs);i++) {
        char args[256];memcpy(args,"xpcproxy",9);strcpy(args+9,dynamic_jobs[i]);
        struct image_params allow=image(args,10+strlen(args+9),2);
        assert(!nabu_minimal_boot_apply(&proxy,&allow,&task) && !holds && !logs);
    }
    const char *blocked[]={"com.apple.cloudd","com.apple.apsd","com.apple.metadata.mds",
        "com.apple.mds_stores","com.apple.trustd","com.apple.syspolicyd",
        "com.apple.endpointsecurityd","com.apple.Kerberos.digest-service",
        "com.apple.softwareupdated","com.apple.mobileassetd","com.apple.biomed",
        "com.apple.duetexpertd","com.apple.contextstored","com.apple.locationd",
        "com.apple.airportd","com.apple.audio.coreaudiod","com.apple.appleh13camerad",
        "com.apple.symptomsd","com.apple.watchdogd","com.apple.lsd.extra",
        "com.apple.unknown", "com.apple.mobile.keybagd", "com.apple.keybagd",
        "com.apple.bluetoothd", "com.apple.endpointsecurity.endpointsecurityd",
        "com.apple.mobile.softwareupdated", "com.apple.loginwindowFake.session",
        "com.apple.coreservicesFake.example", "com.apple.cfprefsdFake.example",
        "com.apple.distnotedFake.example", "com.apple.iconservicesFake.example"};
    for(unsigned i=0;i<sizeof(blocked)/sizeof(*blocked);i++) {
        char args[256];memcpy(args,"xpcproxy",9);strcpy(args+9,blocked[i]);
        struct image_params block=image(args,10+strlen(args+9),2);
        unsigned before=holds;
        assert(!nabu_minimal_boot_apply(&proxy,&block,&task) && holds==before+1);
    }
    assert(strstr(journal,"label=com.apple.Kerberos.digest-service mode=kernel-hold"));
    /* This header applies to proxies regardless of their parent, not to direct
       service images and not to the launchd parent task. */
    struct proc other={"endpointsecurity",103,1};unsigned before=holds;
    assert(!nabu_minimal_boot_apply(&other,&im,&task) && holds==before);
    proxy.ppid=44;assert(!nabu_minimal_boot_apply(&proxy,&im,&task) && holds==before+1);
    /* Fail closed only for actual xpcproxy's missing/truncated/empty labels. */
    const char empty[]="xpcproxy\0\0", truncated[]="xpcproxy\0com.apple.lsd";
    im=image(empty,sizeof(empty)-1,2);assert(!nabu_minimal_boot_apply(&proxy,&im,&task));
    im=image(truncated,sizeof(truncated)-1,2);assert(!nabu_minimal_boot_apply(&proxy,&im,&task));
    im=(struct image_params){NULL,NULL,0};assert(!nabu_minimal_boot_apply(&proxy,&im,&task));
    im=image(denied,sizeof(denied)-1,1);assert(!nabu_minimal_boot_apply(&proxy,&im,&task));
    im=image(denied,sizeof(denied)-1,2);
    task.active=0;assert(nabu_minimal_boot_apply(&proxy,&im,&task)==EIO);
    assert(nabu_minimal_boot_hold_failures==1);task.active=1;
    /* A user resume drains only user holds; independent kernel hold persists. */
    task.user_holds=1;task.user_holds--;assert(task.kernel_holds && !task.user_holds);
    for(unsigned i=0;i<100;i++) assert(!nabu_minimal_boot_apply(&proxy,&im,&task));
    assert(logs==65 && nabu_minimal_boot_parked>100 && !parent_waits);
    assert(strstr(journal,"further-records-suppressed"));
    puts("minimal boot policy: exact jobs, requested namespaces, noncore denies, hold and bounded log passed");
    return 0;
}
