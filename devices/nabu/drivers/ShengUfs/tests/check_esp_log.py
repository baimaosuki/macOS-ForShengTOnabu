#!/usr/bin/env python3
"""Production NABULOG2 journal and UFS transport under ASan/UBSan."""
from pathlib import Path
import argparse,hashlib,json,os,shutil,struct,subprocess,zlib
BLOCK=4096;CAPACITY=4*1024*1024;SLOT=BLOCK+CAPACITY;FILE=BLOCK+2*SLOT
TOKEN=bytes(range(1,17))
def config():
    b=bytearray(BLOCK);b[:8]=b'NABUCFG2'
    struct.pack_into('<6I',b,8,2,BLOCK,CAPACITY,SLOT,FILE,0);b[32:48]=TOKEN
    struct.pack_into('<I',b,48,BLOCK);struct.pack_into('<I',b,56,zlib.crc32(b));return b
def disk(path,mode):
    first=32;total=76800;fat=128;reserved=32;data=reserved+2*fat
    entries=bytearray(16384);entries[:16]=bytes.fromhex('28732ac11ff8d211ba4b00a0c93ec93b')
    entries[16:32]=bytes(range(16));struct.pack_into('<QQ',entries,32,first,first+total-1);entries[56:64]='esp\0'.encode('utf-16le')
    entries[128:144]=bytes([1])*16;struct.pack_into('<QQ',entries,160,110000,129990)
    header=bytearray(BLOCK);header[:8]=b'EFI PART';struct.pack_into('<II',header,8,0x10000,92)
    struct.pack_into('<QQQQ',header,24,1,129999,6,129998)
    struct.pack_into('<QIII',header,72,2,128,128,zlib.crc32(entries));struct.pack_into('<I',header,16,zlib.crc32(header[:92]))
    boot=bytearray(BLOCK);struct.pack_into('<H',boot,11,BLOCK);boot[13]=1;struct.pack_into('<H',boot,14,reserved);boot[16]=2
    struct.pack_into('<II',boot,32,total,fat);struct.pack_into('<I',boot,44,2);boot[82:87]=b'FAT32';boot[510:512]=b'\x55\xaa'
    fats=bytearray(fat*BLOCK);struct.pack_into('<III',fats,0,0xffffff8,0xfffffff,0xfffffff)
    for i in range(FILE//BLOCK):struct.pack_into('<I',fats,(3+i)*4,3+i+1 if i+1<FILE//BLOCK else 0xfffffff)
    directory=bytearray(BLOCK);directory[:11]=b'XNU2LOG BIN';directory[11]=0x20
    struct.pack_into('<H',directory,26,3);struct.pack_into('<I',directory,28,FILE)
    file_first=first+data+1;c=config()
    if mode==5:c[32]^=1;struct.pack_into('<I',c,56,0);struct.pack_into('<I',c,56,zlib.crc32(c))
    with path.open('wb') as f:
        f.truncate(130000*BLOCK)
        for lba,b in ((1,header),(2,entries),(first,boot),(first+reserved,fats),(first+reserved+fat,fats),(first+data,directory),(file_first,c)):
            f.seek(lba*BLOCK);f.write(b)
        if mode==6:f.seek((first+reserved+fat)*BLOCK);f.write(b'BAD!')
        if mode==7:
            # A second directory entry aliases the journal clusters.
            f.seek((first+data)*BLOCK+32);entry=bytearray(directory[:32]);entry[:11]=b'ALIAS   BIN';f.write(entry)
    return file_first

def validate(container):
    valid=[]
    for slot in range(2):
        h=container[BLOCK+slot*SLOT:BLOCK+slot*SLOT+BLOCK]
        if h[:8]!=b'NABULOG2':continue
        zero=bytearray(h);struct.pack_into('<I',zero,80,0)
        if zlib.crc32(zero)!=struct.unpack_from('<I',h,80)[0]:continue
        n,crc=struct.unpack_from('<II',h,72);payload=container[2*BLOCK+slot*SLOT:2*BLOCK+slot*SLOT+n]
        if zlib.crc32(payload)!=crc:continue
        valid.append((struct.unpack_from('<Q',h,24)[0],h,payload))
    return max(valid,key=lambda v:v[0])
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=True);(out/'verification.json').unlink(missing_ok=True);source=Path(__file__).resolve().parent
    cc=shutil.which('g++') or shutil.which('clang++');assert cc
    env=dict(os.environ,ASAN_OPTIONS='abort_on_error=1')
    (out/'NabuLogBuild.h').write_text('/* test identity is supplied by the harness */\n')
    for name in ('EspLog','RootCommands','EspLogTransport'):
        cmd=[cc,'-std=c++17','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I'+str(out),str(source/(name+'.cpp')),'-o',str(out/name)]
        subprocess.run(cmd,check=True)
    results=[]
    for mode in range(8):
        path=out/f'media-{mode}.img';first=disk(path,mode)
        with path.open('rb') as f:before=f.read((first+1)*BLOCK);f.seek((first+FILE//BLOCK)*BLOCK);after=hashlib.file_digest(f,'sha256').hexdigest()
        r=subprocess.run([str(out/'EspLog'),str(path),str(mode)],capture_output=True,text=True,env=env)
        (out/f'EspLog-{mode}.log').write_text(r.stdout+r.stderr)
        if r.returncode:
            print(r.stdout+r.stderr,flush=True)
            r.check_returncode()
        with path.open('rb') as f:
            assert f.read(len(before))==before
            f.seek((first+FILE//BLOCK)*BLOCK);assert hashlib.file_digest(f,'sha256').hexdigest()==after
            f.seek(first*BLOCK);container=f.read(FILE)
        if mode<5:
            seq,h,payload=validate(container)
            # A failed final sync may leave either slot visible after reboot;
            # the previous commit must remain valid and never be destroyed.
            assert seq in ((1,2) if mode==4 else (2,) if mode==0 else (1,))
            if seq==2:
                stream=b'EARLY\n'+bytes((i*13+7)&255 for i in range(CAPACITY+12345))
                assert payload==stream[-CAPACITY:]
                assert struct.unpack_from('<Q',h,56)[0]==4
                assert struct.unpack_from('<Q',h,88)[0]==len(stream)-CAPACITY
            else:assert payload==b'EARLY\n'
        results.append(dict(mode=mode,passed=True,output=r.stdout.strip()))
        path.unlink()
    r=subprocess.run([str(out/'RootCommands')],capture_output=True,text=True,env=env)
    (out/'RootCommands.log').write_text(r.stdout+r.stderr)
    if r.returncode:
        print(r.stdout+r.stderr,flush=True);r.check_returncode()
    results.append(dict(name='production UFS commands and window gates',passed=True,output=r.stdout.strip()))
    r=subprocess.run([str(out/'EspLogTransport')],capture_output=True,text=True,env=env)
    (out/'EspLogTransport.log').write_text(r.stdout+r.stderr)
    if r.returncode:
        print(r.stdout+r.stderr,flush=True);r.check_returncode()
    results.append(dict(name='production ESP adapter',passed=True,output=r.stdout.strip()))
    (out/'verification.json').write_text(json.dumps(dict(passed=True,sanitizers=['address','undefined'],cases=results),indent=2)+'\n')
    print(json.dumps(dict(passed=True,cases=len(results),sanitizers=['address','undefined'])))
if __name__=='__main__':main()
