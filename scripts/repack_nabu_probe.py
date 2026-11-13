#!/usr/bin/env python3
"""Repack a verified NABU archive with a separately built session probe only.

No devices, CI dispatch, signing or source mutations. All output is new.
"""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import plistlib
import re
import stat
import struct
import subprocess
import tempfile
import zipfile

from prepare_nabu_boot_addon import prepare, validate_addon, check_trust_membership
from prepare_nabu_trustcache import validate_bundle

BASE_RUN = 43394823334
BASE_SHA256 = 'cc69824947a693a7b69bce8eb4b03e06812cbc5959472a10d500b9141c33b773'
PAYLOAD = 'EFI/UniXNU/Payloads/'
ADDON = PAYLOAD + 'kernel.macho.gpuaddon'
TRUST = PAYLOAD + 'kernel.macho.trustcache'
TOML = 'EFI/UniXNU/unixnu.toml'
SOURCE_HASHES = {
    'source_sha256': 'platform/nabu/macos26/bootshim/apfs_boot_util.c',
    'session_probe_script_sha256': 'platform/nabu/macos26/bootshim/session_probe.sh',
    'session_probe_header_sha256': 'platform/nabu/macos26/bootshim/session_probe.h',
    'raw_stacks_header_sha256': 'platform/nabu/macos26/bootshim/raw_stacks.h',
    'entitlements_sha256': 'platform/nabu/macos26/bootshim/entitlements.plist',
    'build_script_sha256': 'scripts/build_nabu_boot_shim.py',
}

PROBE_POLICY = dict(version=6, default='raw-only', symbols_bootarg='nabu-session-symbols=1',
    sample_timeout_seconds=120, raw_clock='CLOCK_UPTIME_RAW', raw_clock_label='mach-uptime',
    rounds=3, after_discovery_seconds=[0,150,300],
    required_targets=['opendirectoryd','loginwindow','WindowServer'], optional_targets=['lsd'],
    discovery_timeout_seconds=480, watchdog_timeout_seconds=515, per_target_timeout_seconds=10,
    frames_per_thread=32, threads='enumerate-all-within-time-bound',
    report_policy='preserve-full-files-relay-bounded-16KiB')
BINARY_MARKERS = (b'version=6', b'sample-skipped reason=online-symbols-disabled',
    b'clock=mach-uptime', b'rounds=3 after_discovery=0,150,300', b'nabu-session-symbols')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def ci_provenance(path, *, expected_run=None, expected_head=None):
    raw = path.read_bytes()
    record = json.loads(raw)
    run, head = record.get('id'), record.get('head_sha', '')
    require(isinstance(run, int) and not isinstance(run, bool) and run > 0,
            'CI metadata needs a positive run id')
    require(record.get('status') == 'completed' and record.get('conclusion') == 'success',
            'CI run must be completed successfully')
    require(re.fullmatch('[0-9a-f]{40}', head) is not None, 'CI metadata needs exact head_sha')
    require(expected_run is None or run == expected_run, 'base CI run differs')
    require(expected_head is None or head == expected_head, 'CI head_sha differs from delivery')
    return dict(run_id=run, head_sha=head, metadata_sha256=sha(raw),
                status='completed', conclusion='success', html_url=record.get('html_url'))


def modules(data):
    record = validate_bundle(data)
    offsets = [m['offset'] for m in record['modules']]
    return [data[a:b] for a, b in zip(offsets, offsets[1:] + [len(data)])]


def verify_shim_signature(data, entitlement_bytes):
    """Verify every CodeDirectory's actual pages and embedded special slots.

    Ordinary ARM64 ad-hoc signing is paired with an explicit new trust entry.
    This is content integrity, not a claim of an Apple/platform CMS identity.
    """
    require(len(data) >= 48, 'truncated shim Mach-O')
    magic, cpu, subtype, kind, count, command_bytes = struct.unpack_from('<6I', data)
    require((magic, cpu, subtype & 0xffffff, kind) ==
            (0xfeedfacf, 0x100000c, 0, 2), 'shim must be an ordinary ARM64 executable')
    require(0 < count <= 4096 and command_bytes <= len(data) - 32, 'invalid Mach-O commands')
    at, signatures = 32, []
    for _ in range(count):
        require(at + 8 <= 32 + command_bytes, 'truncated Mach-O command')
        cmd, size = struct.unpack_from('<II', data, at)
        require(size >= 8 and size % 8 == 0 and at + size <= 32 + command_bytes,
                'invalid Mach-O command extent')
        if cmd == 0x1d:
            require(size == 16, 'invalid code-signature command')
            signatures.append(struct.unpack_from('<II', data, at + 8))
        at += size
    require(at == 32 + command_bytes and len(signatures) == 1, 'requires one signature command')
    offset, size = signatures[0]
    require(offset >= at and size >= 12 and offset + size == len(data),
            'signature must cover the exact file tail')
    magic, length, count = struct.unpack_from('>III', data, offset)
    require(magic == 0xfade0cc0 and length <= size and 12 + count * 8 <= length,
            'invalid signature superblob')
    require(not any(data[offset + length:]), 'nonzero trailing signature padding')
    blobs, extents = {}, []
    for i in range(count):
        slot, relative = struct.unpack_from('>II', data, offset + 12 + 8 * i)
        require(slot not in blobs and relative >= 12 + count * 8 and relative + 8 <= length,
                'duplicate or invalid signature blob')
        _, n = struct.unpack_from('>II', data, offset + relative)
        require(n >= 8 and relative + n <= length, 'signature blob exceeds superblob')
        extents.append((relative, relative + n))
        blobs[slot] = data[offset + relative:offset + relative + n]
    extents.sort()
    require(all(a[1] <= b[0] for a, b in zip(extents, extents[1:])), 'overlapping signature blobs')
    require(0 in blobs and 5 in blobs and 7 in blobs, 'requires CodeDirectory, XML and DER entitlements')
    require(struct.unpack_from('>I', blobs[5])[0] == 0xfade7171 and
            struct.unpack_from('>I', blobs[7])[0] == 0xfade7172,
            'invalid entitlement blob magic')
    expected = plistlib.loads(entitlement_bytes)
    require(plistlib.loads(blobs[5][8:]) == expected, 'embedded entitlements differ from source plist')
    require(expected.get('com.apple.private.bindfs-allow') is True and
            expected.get('com.apple.system-task-ports.read') is True and
            expected.get('com.apple.system-task-ports.read.safe') is True,
            'missing required boot/probe entitlements')
    directories = []
    for slot, cd in blobs.items():
        if slot != 0 and not 0x1000 <= slot <= 0x1005:
            continue
        require(len(cd) >= 44, 'truncated CodeDirectory')
        magic, n, version, flags, hashes, ident, special, pages, limit = struct.unpack_from('>9I', cd)
        hs, ht, platform, logpage = struct.unpack_from('>4B', cd, 36)
        require(magic == 0xfade0c02 and n == len(cd) and 0x20100 <= version <= 0x20600,
                'invalid CodeDirectory')
        require(ht in (1, 2, 3, 4) and hs == {1:20, 2:32, 3:20, 4:48}[ht],
                'unsupported CodeDirectory hash')
        require(0 < logpage <= 20 and special <= 64 and flags & 2,
                'requires page-hashed ad-hoc CodeDirectory')
        require(len(cd) >= 48 and struct.unpack_from('>I', cd, 44)[0] == 0,
                'scatter signatures are unsupported')
        if version >= 0x20300:
            require(len(cd) >= 64, 'truncated extended CodeDirectory')
            wide = struct.unpack_from('>Q', cd, 56)[0]
            if wide:
                limit = wide
        require(limit == offset and pages == math.ceil(limit / (1 << logpage)) and pages > 0,
                'CodeDirectory does not cover every executable page')
        require(hashes >= 44 + special * hs and hashes + pages * hs <= len(cd),
                'CodeDirectory hash slots out of bounds')
        require(44 <= ident < hashes - special * hs and b'\0' in cd[ident:hashes - special * hs],
                'invalid CodeDirectory identifier')
        identifier = cd[ident:cd.index(b'\0', ident)].decode()
        require(identifier == 'org.nabu.apfs-boot-layout', 'unexpected shim signing identifier')
        algo = {1:'sha1', 2:'sha256', 3:'sha256', 4:'sha384'}[ht]
        digest = lambda value: hashlib.new(algo, value).digest()[:hs]
        for page in range(pages):
            actual = digest(data[page * (1 << logpage):min((page + 1) * (1 << logpage), limit)])
            require(actual == cd[hashes + page * hs:hashes + (page + 1) * hs],
                    'shim signature page hash mismatch')
        for number in range(1, special + 1):
            stored = cd[hashes - number * hs:hashes - (number - 1) * hs]
            if number in blobs:
                require(stored == digest(blobs[number]), 'signature special-slot hash mismatch')
            else:
                require(not any(stored), 'unverifiable external signature special slot')
        require(special >= 7, 'entitlements must be covered by CodeDirectory')
        directories.append(dict(slot=slot, hash_type=ht, cdhash=digest(cd)[:20].hex(),
                                code_pages=pages, special_slots=special, identifier=identifier))
    require(directories, 'no verified CodeDirectories')
    return dict(all_code_directories_and_pages_verified=True, embedded_entitlements_verified=True,
                xml_entitlement_blob_sha256=sha(blobs[5]), der_entitlement_blob_sha256=sha(blobs[7]),
                code_directories=directories)


def verify_shim(shim_dir, source_root, ci):
    build_bytes = (shim_dir / 'build.json').read_bytes()
    build = json.loads(build_bytes)
    raw = (shim_dir / 'apfs_boot_util').read_bytes()
    require(build.get('sha256') == sha(raw), 'shim binary differs from build record')
    require(build.get('probe_policy') == PROBE_POLICY, 'shim probe policy differs')
    require(all(marker in raw for marker in BINARY_MARKERS), 'shim is missing actual probe policy markers')
    require(build.get('architecture') == 'arm64' and
            build.get('identifier') == 'org.nabu.apfs-boot-layout' and
            build.get('target') == 'arm64-apple-macos26.5', 'shim build target differs')
    sources = {}
    for key, path in SOURCE_HASHES.items():
        content = (source_root / path).read_bytes()
        require(build.get(key) == sha(content), 'shim source hash differs: ' + path)
        committed = subprocess.run(['git', '-C', str(source_root), 'show',
            ci['head_sha'] + ':' + path], check=True, capture_output=True).stdout
        require(content == committed, 'shim source differs from successful CI head: ' + path)
        sources[path] = sha(content)
    signature = verify_shim_signature(raw, (source_root / SOURCE_HASHES['entitlements_sha256']).read_bytes())
    hashes = {cd['cdhash'] for cd in signature['code_directories']}
    require(build.get('cdhash') in hashes, 'build CDHash differs from actual signature')
    return dict(ci=ci, build_record_sha256=sha(build_bytes), binary_sha256=sha(raw),
                source_sha256=sources, probe_policy=build['probe_policy'], signature=signature)


def safe_members(z):
    names = [i.filename for i in z.infolist()]
    require(len(names) == len(set(names)), 'duplicate ZIP member')
    for info in z.infolist():
        p = PurePosixPath(info.filename)
        require(not p.is_absolute() and '..' not in p.parts and '\\' not in info.filename and
                '\x00' not in info.filename and not info.flag_bits & 1, 'unsafe ZIP member')
        require(not stat.S_ISLNK(info.external_attr >> 16), 'ZIP symlinks are unsupported')
        lower = info.filename.lower()
        require(not lower.endswith(('data.dbr', 'root_image.bin', 'xnu2log.bin', '.log')) and
                not any(x.lower() in ('data', 'root', 'logs') for x in p.parts),
                'base archive contains Data/root/log payload')
    return names


def repack(base_zip, expected_base_sha256, base_ci_metadata, shim_dir,
           shim_ci_metadata, original_trust, source_root, output):
    """Preserve base inputs; write archive, paired metadata and manifest atomically."""
    require(not output.exists(), 'output already exists; original files are never overwritten')
    base_before = base_zip.stat()
    require(output.resolve() != base_zip.resolve(), 'output cannot replace base archive')
    require(expected_base_sha256 == BASE_SHA256, 'requires pinned verified base ZIP SHA-256')
    require(sha(base_zip.read_bytes()) == expected_base_sha256, 'base ZIP SHA-256 mismatch')
    base_ci = ci_provenance(base_ci_metadata, expected_run=BASE_RUN)
    shim_ci = ci_provenance(shim_ci_metadata)
    require(shim_ci['head_sha'] != base_ci['head_sha'] and shim_ci['run_id'] != base_ci['run_id'],
            'new shim requires its own successful CI provenance')
    shim = verify_shim(shim_dir, source_root, shim_ci)
    original = original_trust.read_bytes()
    require(validate_bundle(original)['module_count'] == 3, 'requires original three trust modules')
    with zipfile.ZipFile(base_zip) as archive:
        names = safe_members(archive)
        required = {PAYLOAD + n for n in ('kernel.macho', 'kernel.macho.adt',
                                         'kernel.macho.trustcache', 'kernel.macho.gpuaddon')}
        require(required | {TOML, 'delivery.json', 'README.txt'} <= set(names), 'missing base payload')
        delivery = json.loads(archive.read('delivery.json'))
        require(delivery.get('ci_run') == BASE_RUN and delivery.get('commit') == base_ci['head_sha'],
                'base delivery CI provenance differs')
        declared = delivery.get('payload_sha256', {})
        require({PAYLOAD + n for n in declared} == required, 'unexpected base payload hash manifest')
        for name, digest in declared.items():
            require(sha(archive.read(PAYLOAD + name)) == digest, 'base delivery payload hash mismatch: ' + name)
        require(sha(archive.read(TOML)) == delivery.get('toml_sha256'), 'base TOML hash mismatch')
        require(delivery.get('kernel_sha256') == declared['kernel.macho'], 'base kernel hash differs')
        require(delivery.get('kernel_bytes') == len(archive.read(PAYLOAD + 'kernel.macho')),
                'base kernel byte count differs')
        base_trust = archive.read(TRUST)
        require(modules(base_trust)[:3] == modules(original), 'original trust modules differ from base')
        require(len(modules(base_trust)) == 4, 'base requires exactly three original plus owner trust')
        old_addon = validate_addon(archive.read(ADDON))
        require(old_addon['files'] == 1, 'probe-only repack refuses an OD addon')
        check_trust_membership(old_addon, base_trust)
        require(old_addon['file_sha256'] != shim['binary_sha256'], 'shim is unchanged from base')
        # Build entirely in a sibling temporary directory. Nothing appears at
        # output until the archive has been reread and every member verified.
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.nabu-probe-', dir=output.parent) as temporary:
            stage = Path(temporary)
            addon_record = prepare(shim_dir, original_trust, stage / 'addon')
            addon = (stage / 'addon/kernel.macho.gpuaddon').read_bytes()
            trust = (stage / 'addon/kernel.macho.trustcache').read_bytes()
            new_modules = modules(trust)
            require(new_modules[:3] == modules(original) and len(new_modules) == 4,
                    'new trust did not preserve original three modules')
            expected_entries = sorted(bytes.fromhex(c['cdhash']) + bytes((c['hash_type'], 0, 0, 0))
                                      for c in shim['signature']['code_directories'])
            require(new_modules[3][24:] == b''.join(sorted(set(expected_entries))),
                    'owner trust module must contain exactly the new shim signatures')
            new_hashes = dict(declared)
            new_hashes['kernel.macho.gpuaddon'] = sha(addon)
            new_hashes['kernel.macho.trustcache'] = sha(trust)
            metadata = dict(kind='NABU session probe-only repack', build='25F71',
                desktop_fix=False, desktop_verified=False, device_tested=False,
                purpose='Reduce diagnostic interference; kernel and display behavior unchanged',
                base_delivery=delivery, base_delivery_sha256=sha(archive.read('delivery.json')),
                kernel_provenance=dict(ci=base_ci, payload_sha256=declared['kernel.macho']),
                shim_provenance=shim, payload_sha256=new_hashes, toml_sha256=delivery['toml_sha256'],
                probe_policy=shim['probe_policy'],
                data_or_root_included=False, root_reflash_required=False, data_reflash_required=False)
            readme = ('NABU 25F71 session probe-only repack\n\n'
                'This changes the diagnostic boot shim and its explicit trust module only.\n'
                'Default probe: raw stacks only. Symbolic sample requires separate opt-in.\n'
                'The kernel, ADT, TOML and all other EFI members are byte-for-byte unchanged.\n'
                'This is not a desktop, display, account, Data or root filesystem fix.\n'
                f'Kernel CI: {base_ci["run_id"]} / {base_ci["head_sha"]}\n'
                f'Shim CI: {shim_ci["run_id"]} / {shim_ci["head_sha"]}\n'
                'See delivery.json for separate provenance, source hashes and signature verification.\n'
                'No Data/root/log files are included. Device behavior remains unverified.\n')
            replacements = {ADDON:addon, TRUST:trust, 'delivery.json':
                (json.dumps(metadata, indent=2) + '\n').encode(), 'README.txt':readme.encode()}
            new_zip = stage / 'nabu-session-probe-only.zip'
            before, after = {}, {}
            with zipfile.ZipFile(new_zip, 'w', allowZip64=True) as target:
                target.comment = archive.comment
                for info in archive.infolist():
                    raw = archive.read(info)
                    before[info.filename] = sha(raw)
                    target.writestr(copy.copy(info), replacements.get(info.filename, raw))
            with zipfile.ZipFile(new_zip) as check:
                require(safe_members(check) == names and check.testzip() is None, 'new ZIP integrity mismatch')
                for name in names:
                    after[name] = sha(check.read(name))
                    require(name in replacements or before[name] == after[name],
                            'immutable archive member changed: ' + name)
                    require(not name.startswith('EFI/') or name in (ADDON, TRUST) or
                            before[name] == after[name], 'immutable EFI member changed')
                check_trust_membership(validate_addon(check.read(ADDON)), check.read(TRUST))
            manifest = dict(**metadata, base_archive_sha256=expected_base_sha256,
                archive='nabu-session-probe-only.zip', archive_sha256=sha(new_zip.read_bytes()),
                archive_bytes=new_zip.stat().st_size, changed_members=sorted(replacements),
                member_sha256_before=before, member_sha256_after=after,
                original_trust_sha256=sha(original), trust=addon_record['trust'])
            (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
            (stage / 'kernel-ci-metadata.json').write_bytes(base_ci_metadata.read_bytes())
            (stage / 'shim-ci-metadata.json').write_bytes(shim_ci_metadata.read_bytes())
            (stage / 'base-delivery.json').write_bytes(archive.read('delivery.json'))
            (stage / 'shim-build.json').write_bytes((shim_dir / 'build.json').read_bytes())
            # Only final review artifacts are published; intermediate addon is private.
            import shutil
            shutil.rmtree(stage / 'addon')
            require(not output.exists(), 'output appeared during repack')
            require(sha(base_zip.read_bytes()) == expected_base_sha256 and
                    base_zip.stat().st_size == base_before.st_size,
                    'base archive changed during repack')
            stage.rename(output)
    return manifest


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-zip', type=Path, required=True)
    p.add_argument('--base-sha256', default=BASE_SHA256)
    p.add_argument('--base-ci-metadata', type=Path, required=True)
    p.add_argument('--shim-dir', type=Path, required=True)
    p.add_argument('--shim-ci-metadata', type=Path, required=True)
    p.add_argument('--original-trust', type=Path, required=True)
    p.add_argument('--source-root', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(argv)
    result = repack(a.base_zip, a.base_sha256, a.base_ci_metadata, a.shim_dir,
                    a.shim_ci_metadata, a.original_trust, a.source_root, a.output)
    print(json.dumps(dict(archive=str(a.output / result['archive']),
                          sha256=result['archive_sha256'], desktop_fix=False), indent=2))


if __name__ == '__main__':
    main()
