
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <errno.h>
#include <string.h>
#include <sys/types.h>
#define CONFIG_UNION_MOUNTS 1
#define CONFIG_MACF 1
#define UIO_USERSPACE64 0
#define UIO_USERSPACE32 1
#define UIO_READ 0
#define FREAD 1
#define VDIR 2
#define MNT_UNION 32
#define VNODE_READDIR_EXTENDED 1
#define ARG_VNODE1 0
#define AUDIT_ARG(...) ((void)0)
#define UIO_STACKBUF(name,n) char name[32]
#define GETDIRENTRIES_MAXBUFSIZE (128*1024*1024U)
typedef uintptr_t user_addr_t;typedef size_t user_size_t;typedef ssize_t user_ssize_t;
struct vnode;struct mount {int mnt_flag;struct vnode *mnt_vnodecovered;};struct vnode {struct mount *v_mount;int v_type;int v_flag;struct vnode *v_parent;const char *v_name;};typedef struct vnode *vnode_t;
struct fileglob {int fg_flag;off_t fg_offset;vnode_t data;};struct fileproc {struct fileglob *fp_glob;};struct vfs_context {int dummy;};
struct uio {off_t off;size_t resid;};typedef struct uio *uio_t;
static struct mount topmount={MNT_UNION},lowermount={0};static struct vnode top={&topmount,VDIR},bottom={&lowermount,VDIR},sameother={&topmount,VDIR};
static struct fileglob fg;static struct fileproc file={&fg};static struct vfs_context ctx;static struct uio u;
static vnode_t lower;static int reads,lookups,puts_count,refs,opens,closes,mode,name_error,getref_error;
static void *vfs_context_proc(void *v){return v;}static int proc_is64bit(void*v){return 1;}static struct vfs_context *vfs_context_current(void){return &ctx;}
static void *vfs_context_ucred(void*v){return v;}static void vn_offset_lock(void*v){}static void vn_offset_unlock(void*v){}static void file_drop(int fd){}
static int fp_getfvp(void*p,int fd,struct fileproc **f,vnode_t*v){*f=&file;*v=fg.data;return 0;}static vnode_t fp_get_data(struct fileproc*f){return f->fp_glob->data;}
static int mac_file_check_change_offset(void*p,void*f){return 0;}static int vnode_getwithref(vnode_t v){return getref_error;}static void vnode_put(vnode_t v){puts_count++;}
static int mac_vnode_check_readdir(void*c,vnode_t v){return 0;}static int mac_vnode_check_open(void*c,vnode_t v,int f){return 0;}

static int vnode_isdir(vnode_t v){return v->v_type==VDIR;}static int vnode_ref(vnode_t v){refs++;return 0;}
static int VNOP_OPEN(vnode_t v,int f,void*c){opens++;return 0;}static void VNOP_CLOSE(vnode_t v,int f,void*c){closes++;}static void vnode_rele(vnode_t v){}
static void fp_set_data(struct fileproc*f,vnode_t v){f->fp_glob->data=v;}
static uio_t uio_createwithbuffer(int n,off_t off,int space,int rw,void*b,size_t len){u.off=off;return &u;}
static void uio_addiov(uio_t u,user_addr_t b,size_t n){u->resid=n;}static off_t uio_offset(uio_t u){return u->off;}static size_t uio_resid(uio_t u){return u->resid;}
static int backend(vnode_t v,uio_t u,int flags,int*eof,int*n,void*c){reads++;if(reads>8)return ELOOP;*eof=1;*n=0;if(mode&&u->off==0){u->resid-=32;u->off=77;*n=1;}return 0;}
#define vnode_readdir64 backend
#define VNOP_READDIR backend

#define VROOT 1
#define NULLVP NULL
#define MAXPATHLEN 128
#define LOOKUP 0
#define OP_LOOKUP 0
#define USEDVP 0
#define UIO_SYSSPACE 0
#define CAST_USER_ADDR_T(x) ((uintptr_t)(x))
typedef struct vfs_context *vfs_context_t;
struct nameidata {vnode_t ni_vp;vnode_t ni_dvp;};
#define NDINIT(nd,...) memset((nd),0,sizeof(*(nd)))
static int allocs,frees;
static char *namei_alloc(size_t n){allocs++;return malloc(n);}
static void namei_free(char*p,size_t n){frees++;free(p);}
static const char *vnode_getname(vnode_t v){return v->v_name;}
static void vnode_putname(const char *n){}
static int namei(struct nameidata*n){lookups++;n->ni_vp=lower;return name_error;}
static void nameidone(struct nameidata*n){}
static struct vnode top_root={&topmount,VDIR,VROOT,0,"/"};
/* EXTRACTED_UNION_HELPER */
/* EXTRACTED_GETDIRENTRIES */
#define CHECK(x) do{if(!(x)){fprintf(stderr,"failed line %d: %s\n",__LINE__,#x);exit(1);}}while(0)
static void reset(vnode_t next){fg=(struct fileglob){FREAD,77,&top};lower=next;reads=lookups=puts_count=refs=opens=closes=mode=name_error=getref_error=allocs=frees=0;top.v_parent=&top_root;top.v_name="directory";top.v_flag=0;sameother.v_parent=&top_root;sameother.v_name="directory";topmount.mnt_flag=MNT_UNION;bottom.v_type=VDIR;}
static void run(int mode_in,vnode_t lower_in){reset(lower_in);mode=mode_in;ssize_t n=-1;int eof=0;off_t off=-1;int e=getdirentries_common(3,0,4096,&n,&off,&eof,VNODE_READDIR_EXTENDED);CHECK(allocs==frees);
#ifndef FIXED
 if(lower!=&bottom){if(mode)CHECK(e==0&&n==32&&reads==2);else CHECK(e==ELOOP&&reads==9);return;}
#else
 if(lower!=&bottom){CHECK(e==0&&n==0&&reads==1&&refs==0&&opens==0&&closes==0&&fg.fg_offset==77);return;}
#endif
 CHECK(e==0&&n==32&&reads==2&&fg.data==&bottom&&refs==1&&opens==1&&closes==1);}
int main(void){vnode_t out=(void*)1;int e;
 run(0,&top);run(1,&top);run(0,&sameother);run(1,&bottom);
 reset(&bottom);name_error=EIO;e=lookup_traverse_union(&top,&out,&ctx);CHECK(e==EIO&&out==0&&puts_count==0&&allocs==frees);
 reset(&bottom);bottom.v_type=1;e=lookup_traverse_union(&top,&out,&ctx);CHECK(e==ENOENT&&out==0&&puts_count==1&&allocs==frees);
 reset(&top);e=lookup_traverse_union(&top,&out,&ctx);
#ifdef FIXED
 CHECK(e==ENOENT&&out==0&&puts_count==1&&allocs==frees);
#else
 CHECK(e==0&&out==&top&&puts_count==0&&allocs==frees);
#endif
 reset(&bottom);top_root.v_mount->mnt_vnodecovered=&bottom;e=lookup_traverse_union(&top_root,&out,&ctx);CHECK(e==0&&out==&bottom&&allocs==0);
 getref_error=EIO;e=lookup_traverse_union(&top_root,&out,&ctx);CHECK(e==ENOENT&&allocs==0);
 reset(&bottom);top.v_name=0;e=lookup_traverse_union(&top,&out,&ctx);CHECK(e==ENOENT&&out==0&&allocs==frees);
 puts("actual helper + getdirentries: 10 cases passed");return 0;}
