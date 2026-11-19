#!/usr/bin/env python3
"""Check complete-archive reuse and rejection of incompatible or partial caches."""
import argparse,copy,hashlib,importlib.util,json,shutil,tempfile
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--archive-dir',type=Path)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    script=Path(__file__).resolve().parents[1]/'scripts/build-nabu-m2v27.py'
    spec=importlib.util.spec_from_file_location('m2v_builder',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    policy=module.bridge_cache_policy(a.stage,{'rustc':'qualified-rust','clang':'qualified-clang'})
    checks=[]
    with tempfile.TemporaryDirectory(prefix='nabu-bridge-cache-') as temporary:
        root=Path(temporary);cache=root/'cache';source=root/'source';source.mkdir()
        name=module.BRIDGE_ARCHIVE
        if a.archive_dir:
            shutil.copyfile(a.archive_dir/name,source/name)
            shutil.copyfile(a.archive_dir/(name+'.json'),source/(name+'.json'))
        else:
            (source/name).write_bytes(b'!<arch>\ncache-test-payload')
            (source/(name+'.json')).write_text(json.dumps({'sha256':hashlib.sha256((source/name).read_bytes()).hexdigest()}))
        assert module.cached_bridge(cache,policy) is None;checks.append('absent cache')
        module.save_bridge(cache,policy,source)
        assert module.cached_bridge(cache,policy)==cache/(name+'.json');checks.append('exact complete archive')
        for field in ('abi','rust_target','rust_target_cpu','rust_target_features','rust_lto','rust_codegen_units'):
            changed=copy.deepcopy(policy);changed['target'][field]='changed'
            assert module.cached_bridge(cache,changed) is None
        checks.append('ABI, target, CPU features and LTO changes')
        changed=copy.deepcopy(policy);changed['tools']['rustc']='other version'
        assert module.cached_bridge(cache,changed) is None;checks.append('compiler version change')
        changed=copy.deepcopy(policy);first=next(iter(changed['sources']));changed['sources'][first]='changed'
        assert module.cached_bridge(cache,changed) is None;checks.append('source change')
        with (cache/name).open('r+b') as stream:stream.write(b'corrupt!')
        assert module.cached_bridge(cache,policy) is None;checks.append('corrupt archive')
        module.save_bridge(cache,policy,source)
        (cache/(name+'.json')).unlink()
        assert module.cached_bridge(cache,policy) is None;checks.append('interrupted metadata publication')
        module.save_bridge(cache,policy,source)
        (cache/'policy.json').write_text('{partial')
        assert module.cached_bridge(cache,policy) is None;checks.append('partial policy')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(dict(passed=True,checks=checks,real_archive=bool(a.archive_dir),hardware_tested=False),indent=2)+'\n')
    print('PASS complete Rust archive cache: exact reuse, incompatible inputs and corruption')

if __name__=='__main__':main()
