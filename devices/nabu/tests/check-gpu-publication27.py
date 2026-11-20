#!/usr/bin/env python3
"""Replay the signed plugin's actual mode gate and production ESP integration."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib

DEVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEVICE / 'scripts'))
from nabu_gpu_addon_contract import (METAL_PUBLISH_MARKER, METAL_PUBLISH_CONTENT,
                                    metal_publication_contract)


def function(source, signature):
    start = source.index(signature)
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--esp', type=Path, required=True,
                        help='Existing CPU delivery, before GPU integration')
    parser.add_argument('--signed-userspace', type=Path, required=True)
    parser.add_argument('--plugin-source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    integration = DEVICE / 'scripts/integrate-gpu-esp27.py'
    spec = importlib.util.spec_from_file_location('gpu_integration', integration)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    signed = module.decode((args.signed_userspace / 'gpuaddon.bin').read_bytes())
    assert METAL_PUBLISH_MARKER not in signed
    try:
        metal_publication_contract(signed)
    except ValueError:
        pass
    else:
        raise AssertionError('The old diagnostic-only addon was accepted for production')

    plugin_source = args.plugin_source.read_text()
    # Execute the shipped plugin's exact dladdr/path/access logic. The test
    # executable lives at the same bundle-relative path as the Darwin binary.
    gate = '\n'.join(function(plugin_source, signature) for signature in
                     ('static bool requestedPath(', 'static bool requested()'))
    assert 'if (publish::requested()) return publish::deviceForPort(port);' in plugin_source
    results = {}
    with tempfile.TemporaryDirectory(prefix='nabu-metal-publication-') as temporary:
        root = Path(temporary)
        binary = root / 'GPUBundles/NativeAdrenoMetal.bundle/Contents/MacOS/NativeAdrenoMetal'
        binary.parent.mkdir(parents=True)
        cpp = root / 'mode.cpp'
        cpp.write_text('#include <cstdio>\n#include <cstring>\n#include <dlfcn.h>\n#include <unistd.h>\n'
                       + gate + '\nint main(){return requested()?0:2;}\n')
        subprocess.run(['c++', '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', str(cpp), '-ldl', '-o', str(binary)], check=True)
        def mode():
            return subprocess.run([str(binary)], capture_output=True, text=True, timeout=10).returncode
        assert mode() == 2, 'Absent marker must select diagnostic-only mode'
        lookalike = root / 'GPUBundles/NativeAdrenoMetal.publish.disabled'
        lookalike.write_bytes(b'1\n')
        assert mode() == 2, 'A similarly named file must not enable publication'
        marker = root / METAL_PUBLISH_MARKER
        marker.write_bytes(METAL_PUBLISH_CONTENT[1])
        marker.chmod(METAL_PUBLISH_CONTENT[0])
        assert mode() == 0, 'Exact readable sibling must enable deviceForPort'
        results['actual_plugin_gate_executed'] = True

        esp = root / 'esp'
        payload = Path('EFI/UniXNU/Payloads')
        for relative in (payload / 'kernel.macho.gpuaddon', payload / 'kernel.macho.trustcache',
                         Path('EFI/UniXNU/unixnu.toml')):
            destination = esp / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(args.esp / relative, destination)
        before = module.decode((esp / payload / 'kernel.macho.gpuaddon').read_bytes())
        old_trust = (esp / payload / 'kernel.macho.trustcache').read_bytes()
        old_cpu = next(entry for entry in tomllib.loads((esp / 'EFI/UniXNU/unixnu.toml').read_text())['entry']
                       if entry['id'] == 'xnu')
        report_path = args.output / 'integration.json'
        command = [sys.executable, str(integration), '--esp', str(esp),
                   '--signed-userspace', str(args.signed_userspace), '--output', str(report_path)]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=90, check=True)
        (args.output / 'run.log').write_text(completed.stdout)
        actual = module.decode((esp / payload / 'kernel.macho.gpuaddon').read_bytes())
        assert all(actual[name] == value for name, value in before.items())
        assert all(actual[name] == value for name, value in signed.items())
        assert set(actual) == set(before) | set(signed) | {METAL_PUBLISH_MARKER}
        results['production_contract'] = metal_publication_contract(actual)
        for bad in ((0o600, b'1\n'), (0o644, b'')):
            invalid = dict(actual)
            invalid[METAL_PUBLISH_MARKER] = bad
            try:
                metal_publication_contract(invalid)
            except ValueError:
                pass
            else:
                raise AssertionError('Bad publication marker passed the production contract')
        new_config = tomllib.loads((esp / 'EFI/UniXNU/unixnu.toml').read_text())
        assert next(entry for entry in new_config['entry'] if entry['id'] == 'xnu') == old_cpu
        assert 'nabu-gpu=1' in next(entry for entry in new_config['entry'] if entry['id'] == 'a640-metal')['args'].split()
        assert 'data-reset=1' not in next(entry for entry in new_config['entry'] if entry['id'] == 'a640-metal')['args'].split()
        def modules(raw):
            import struct
            count = struct.unpack_from('<I', raw)[0]
            offsets = struct.unpack_from('<' + 'I' * count, raw, 4)
            return [raw[start:(offsets[index + 1] if index + 1 < count else len(raw))]
                    for index, start in enumerate(offsets)]
        previous = modules(old_trust)
        current = modules((esp / payload / 'kernel.macho.trustcache').read_bytes())
        assert current[:len(previous)] == previous
        assert (args.signed_userspace / 'owner-gpu.trustcache').read_bytes() in current
        results.update(signed_executables_and_bundle_unchanged=True,
                       original_addon_entries_preserved=True, trust_modules_preserved=True,
                       cpu_entry_unchanged=True, no_data_reset=True,
                       signed_addon_sha256=hashlib.sha256((args.signed_userspace / 'gpuaddon.bin').read_bytes()).hexdigest())
    results.update(passed=True, sanitizers=['ASan', 'UBSan'], hardware_tested=False)
    (args.output / 'verification.json').write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
