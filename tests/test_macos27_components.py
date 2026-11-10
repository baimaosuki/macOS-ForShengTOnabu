#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Host tests for the macOS 27.3 component resolver.

The network is replaced by canned manifest, central-directory and wiki data, so
the rules that decide what a desktop root is built from are exercised offline.
"""
from __future__ import annotations

import plistlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import macos27_components as resolver  # noqa: E402

BUILD = '26C5035r'
OS_PATH = '141-22233-021.dmg.aea'
SYSTEM_PATH = '141-22145-021.dmg.aea'
APP_PATH = '141-22106-021.dmg'
MANIFEST = {
    'OS': (OS_PATH, 'sha2-384', 48),
    'Cryptex1,SystemOS': (SYSTEM_PATH, 'sha2-384', 48),
    'Cryptex1,AppOS': (APP_PATH, 'sha2-384', 48),
    'KernelCache': ('kernelcache.release.mac14j', None, 48),
    'StaticTrustCache': ('Firmware/%s.trustcache' % OS_PATH, None, 48),
    'Cryptex1,SystemTrustCache': ('Firmware/%s.trustcache' % SYSTEM_PATH, 'sha2-384', 48),
    'Cryptex1,AppTrustCache': ('Firmware/%s.trustcache' % APP_PATH, 'sha2-384', 48),
}
SIZES = {OS_PATH: 11_408_506_880, SYSTEM_PATH: 2_483_027_968, APP_PATH: 54_525_952,
         'kernelcache.release.mac14j': 33_622_670,
         'Firmware/%s.trustcache' % OS_PATH: 445_681,
         'Firmware/%s.trustcache' % SYSTEM_PATH: 3_503,
         'Firmware/%s.trustcache' % APP_PATH: 2_135}
WIKI = ("""{{keys
 | Build                 = 26C5035r
 | RootFS                = %s
 | RootFSKey             = KgjuAbxol/OFVUlvyKrpdr6dw7I2RNtgUHppVwU3cLQ=
 | SystemOS              = %s
 | SystemOSKey           = twjOTDvNKLIvzACpFyYdrYcMkWuYzo0GIcmxcUDOMf4=
 | SEPFirmwareKey        = Unknown
}}""" % (OS_PATH, SYSTEM_PATH))


def _manifest(identities: list[dict] | None = None) -> dict:
    if identities is None:
        identities = [_identity('j180dap', 'Mac14,8', 'Customer Erase Install (IPSW)'),
                      _identity('j180dap', 'Mac14,15', 'macOS Customer', paths=MANIFEST)]
    return {'BuildIdentities': identities}


def _identity(device: str, model: str, variant: str, paths: dict | None = None) -> dict:
    table = {}
    for name, (path, method, length) in (paths or MANIFEST).items():
        info = {'Path': path}
        if method:
            info['HashMethod'] = method
        table[name] = {'Info': info, 'Digest': bytes(range(length)), 'Trusted': True}
    return {'Info': {'DeviceClass': device, 'BuildNumber': BUILD,
                     'Variant': variant, 'ProductMarketingVersion': '27.3',
                     'ContentEncoding': 'aea'},
            'Ap,ProductType': model, 'Manifest': table}


def _install(manifest: dict, wiki: str = WIKI) -> Path:
    tmp = Path(tempfile.mkdtemp())
    path = tmp / 'BuildManifest.plist'
    path.write_bytes(plistlib.dumps(manifest))
    resolver.central_directory = lambda url: {
        name: {'size': size, 'method': 0 if name.endswith('.aea') else 8,
               'compressed': size}
        for name, size in SIZES.items()}
    resolver._wikitext = lambda title: wiki if title.endswith('(Mac14,15)') else None
    return path


def test_resolves_the_seven_desktop_components() -> None:
    manifest = _install(_manifest())
    record = resolver.resolve(manifest, 'https://example.invalid/x.ipsw', 'j180dap', BUILD)
    names = [entry['name'] for entry in record['components']]
    assert names == sorted(MANIFEST), names
    os_entry = next(e for e in record['components'] if e['name'] == 'OS')
    assert os_entry['size'] == SIZES[OS_PATH]
    assert os_entry['encrypted'] is True
    assert os_entry['ipsw_member']['aea_key'].startswith('KgjuAbxol')
    assert record['build'] == BUILD and record['variant'] == 'Customer Erase Install (IPSW)'


def test_missing_hash_method_is_read_off_the_digest() -> None:
    manifest = _install(_manifest())
    record = resolver.resolve(manifest, 'https://example.invalid/x.ipsw', 'j180dap', BUILD)
    cache = next(e for e in record['components'] if e['name'] == 'KernelCache')
    assert cache['hash_method'] == 'sha2-384' and cache['hash_method_inferred'] is True
    os_entry = next(e for e in record['components'] if e['name'] == 'OS')
    assert os_entry['hash_method_inferred'] is False


def test_wrong_build_is_refused() -> None:
    manifest = _install(_manifest())
    try:
        resolver.resolve(manifest, 'https://example.invalid/x.ipsw', 'j180dap', '25F71')
    except resolver.ComponentError as exc:
        assert '25F71' in str(exc), exc
        return
    raise AssertionError('a build number mismatch was accepted')


def test_unknown_device_class_is_refused() -> None:
    manifest = _install(_manifest())
    try:
        resolver.resolve(manifest, 'https://example.invalid/x.ipsw', 'd221ap', BUILD)
    except resolver.ComponentError as exc:
        assert 'd221ap' in str(exc), exc
        return
    raise AssertionError('an unknown device class was accepted')


def test_missing_component_is_refused() -> None:
    partial = dict(MANIFEST)
    del partial['Cryptex1,AppOS']
    broken = _manifest([_identity('j180dap', 'Mac14,15', 'Customer Erase Install (IPSW)', partial)])
    manifest = _install(broken)
    try:
        resolver.resolve(manifest, 'https://example.invalid/x.ipsw', 'j180dap', BUILD)
    except resolver.ComponentError as exc:
        assert 'Cryptex1,AppOS' in str(exc), exc
        return
    raise AssertionError('a component set without AppOS was accepted')


def test_key_page_must_name_the_manifest_payload() -> None:
    other = WIKI.replace(OS_PATH, '094-56453-087.dmg.aea')
    manifest = _install(_manifest(), wiki=other)
    try:
        resolver.resolve(manifest, 'https://example.invalid/x.ipsw', 'j180dap', BUILD)
    except resolver.ComponentError as exc:
        assert OS_PATH in str(exc), exc
        return
    raise AssertionError('a key table for another payload was accepted')


def test_ambiguous_payloads_are_refused() -> None:
    different = dict(MANIFEST)
    different['OS'] = ('141-99999-001.dmg.aea', 'sha2-384', 48)
    # Two erase-install identities for one device class whose payloads disagree:
    # nothing in the manifest says which one a desktop root should be built from.
    broken = _manifest([_identity('j180dap', 'Mac14,8', 'Customer Erase Install (IPSW)'),
                        _identity('j180dap', 'Mac14,15', 'Customer Erase Install (IPSW)', different)])
    manifest = _install(broken)
    try:
        resolver.resolve(manifest, 'https://example.invalid/x.ipsw', 'j180dap', BUILD)
    except resolver.ComponentError as exc:
        assert 'different payloads' in str(exc), exc
        return
    raise AssertionError('two payload sets under one variant were accepted')


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith('test_')]
    failures = 0
    for test in tests:
        try:
            test()
            print('  %-56s ok' % test.__name__)
        except Exception as exc:                               # noqa: BLE001
            failures += 1
            print('  %-56s FAIL %s' % (test.__name__, exc))
    print('%d/%d component-resolver cases passed' % (len(tests) - failures, len(tests)))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
