
#include <stdio.h>
#include <stdlib.h>
#define CONFIG_UNION_MOUNTS 1
#define CONFIG_MACF 1
#define MNT_UNION 32
#define FSOPT_LIST_SNAPSHOT 64
#define VDIR 2
#define ENOTDIR 20
#define ENOENT 2
#define O_EVTONLY 32768
struct mount {int mnt_flag;};
struct vnode {struct mount *v_mount;int v_type;};
typedef struct vnode *vnode_t;
struct fileglob {long fg_offset;int fg_flag;vnode_t data;};
struct fileproc {struct fileglob *fp_glob;};
struct args {int options;};
struct fd_vn_data {long fv_offset;void *fv_buf;int fv_bufallocsiz,fv_bufsiz,fv_bufdone,fv_soff,fv_eoff,fv_eofflag;};
static int passes,mode,traversals,put_count,refs,macs,auths,lookup_error,mac_error,auth_error,ref_error;
static vnode_t lower;
static void kfree_data(void *x,int n) {(void)x;(void)n;}
static int lookup_traverse_union(vnode_t a,vnode_t *b,void *ctx){traversals++;*b=lower;return lookup_error;}
static int mac_vnode_check_readdir(void *c,vnode_t v){macs++;return mac_error;}
static int vnode_authorize(vnode_t v,void *n,int a,void *c){auths++;return auth_error;}
static int vnode_ref_ext(vnode_t v,int f,int n){refs++;return ref_error;}
static void fp_set_data(struct fileproc *f,vnode_t v){f->fp_glob->data=v;}
static void vnode_rele_internal(vnode_t v,int a,int b,int c){}
static void vnode_put(vnode_t v){put_count++;}
static int run(struct fileproc *fp,struct fd_vn_data *fvdata,struct args *uap) {
 vnode_t dvp=fp->fp_glob->data;void *ctx=0;int action=0,error=0,count=0,eofflag=0;
unionread:
 if (++passes>8) return -999;

/* EXTRACTED_ZERO_OFFSET */

 /* VNOP fixture: empty; or one entry at offset 0 and EOF at 77; or ERANGE input. */
 count=(mode==1&&fvdata->fv_offset==0)?1:0;eofflag=(mode!=2&&!count);error=0;

/* EXTRACTED_UNION_RETRY */

 if(count){fvdata->fv_offset=123;fp->fp_glob->fg_offset=123;error=0;}
 else if(!error&&!eofflag)error=34;
 return error?error:count;
}
#define CHECK(x) do{if(!(x)){fprintf(stderr,"failed line %d\n",__LINE__);return 1;}}while(0)
static struct mount top_mount={MNT_UNION},lower_mount={0};
static struct vnode top={&top_mount,VDIR},bottom={&lower_mount,VDIR},same_mount_other={&top_mount,VDIR};
static struct fileglob fg;static struct fileproc fp={&fg};static struct fd_vn_data data;static struct args args;
static void reset(void){fg=(struct fileglob){77,0,&top};data=(struct fd_vn_data){.fv_offset=77};args.options=0;lower=&top;passes=mode=traversals=put_count=refs=macs=auths=lookup_error=mac_error=auth_error=ref_error=0;}
int main(void){int r;
 reset();r=run(&fp,&data,&args);
#ifdef FIXED
 CHECK(r==0&&passes==1&&put_count==1&&refs==0&&fg.data==&top&&fg.fg_offset==77);puts("empty cycle: EOF, lower iocount released");
#else
 CHECK(r==-999&&passes==9);puts("empty cycle: repeated until fixture bound");
#endif
 reset();mode=1;r=run(&fp,&data,&args);
#ifdef FIXED
 CHECK(r==0&&passes==1&&fg.fg_offset==77);puts("nonempty cycle: EOF, no restart");
#else
 CHECK(r==1&&passes==2&&fg.fg_offset==123);puts("nonempty cycle: first entry repeated after restart");
#endif
 reset();lower=&same_mount_other;r=run(&fp,&data,&args);
#ifdef FIXED
 CHECK(r==0&&passes==1&&refs==0);puts("same mount, different vnode: EOF");
#else
 CHECK(r==-999);puts("same mount, different vnode: repeated until fixture bound");
#endif
 reset();lower=&bottom;mode=1;r=run(&fp,&data,&args);CHECK(r==1&&passes==2&&fg.data==&bottom&&macs==1&&auths==1&&refs==1);puts("valid lower: entries, access checks and descriptor switch preserved");
 reset();lower=&bottom;mac_error=13;r=run(&fp,&data,&args);CHECK(r==13&&put_count==1&&refs==0&&auths==0&&fg.data==&top);puts("MAC denial: propagated, descriptor preserved");
 reset();lower=&bottom;auth_error=13;r=run(&fp,&data,&args);CHECK(r==13&&put_count==1&&refs==0);puts("authorization denial: propagated");
 reset();lower=&bottom;ref_error=16;r=run(&fp,&data,&args);CHECK(r==16&&put_count==1&&fg.data==&top);puts("ref failure: propagated and iocount released");
 reset();lookup_error=2;r=run(&fp,&data,&args);CHECK(r==0&&put_count==0);puts("missing lower: ordinary EOF");
 reset();lookup_error=5;r=run(&fp,&data,&args);CHECK(r==5&&put_count==0);puts("lookup error: propagated");
 reset();args.options=FSOPT_LIST_SNAPSHOT;r=run(&fp,&data,&args);CHECK(r==0&&traversals==0);puts("snapshot: no fallback");
 reset();mode=2;r=run(&fp,&data,&args);CHECK(r==34&&traversals==0);puts("small buffer: ERANGE preserved");
 reset();top_mount.mnt_flag=0;r=run(&fp,&data,&args);CHECK(r==0&&traversals==0);puts("nonunion: no fallback");
 return 0;
}
