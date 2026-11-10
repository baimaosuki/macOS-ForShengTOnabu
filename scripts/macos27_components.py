#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Resolve the macOS 27.3 restore components a nabu root image is built from.

The 27.3 restore IPSW is 25 GiB and every payload that matters is either inside
the zip as a stored (method 0) member or as a deflate member, so the components
are read with range requests instead of a full download. This resolver does the
bookkeeping that has to come before any byte is fetched:

  * pick the BuildIdentity for one device class out of ~200 identities,
  * record the logical name, member path, size and manifest digest of every
    component a desktop root needs,
  * carry the AEA key that the community publishes for this build, because the
    OS, SystemOS and recovery payloads ship as .dmg.aea,
  * refuse a component whose manifest digest length disagrees with its
    HashMethod, and refuse a build that is not the one asked for.

Nothing here trusts a filename: the build number comes from the manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import struct
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

DIGEST_BYTES = {'sha2-256': 32, 'sha2-384': 48, 'sha1': 20}
REQUIRED = ('OS', 'Cryptex1,SystemOS', 'Cryptex1,AppOS', 'KernelCache',
            'StaticTrustCache', 'Cryptex1,SystemTrustCache', 'Cryptex1,AppTrustCache')
# Logical name -> the key field the published table uses for it.
KEY_FIELDS = {'OS': 'RootFSKey', 'Cryptex1,SystemOS': 'SystemOSKey',
              'BaseSystem': 'RecoveryOSKey'}
WIKI = 'https://theapplewiki.com/api.php'
USER_AGENT = 'nabu-macos-port/1.0 (range-request resolver)'


class ComponentError(ValueError):
    """The requested component set cannot be resolved from this manifest."""


def identities(manifest: dict) -> list[dict]:
    found = manifest.get('BuildIdentities')
    if not isinstance(found, list) or not found:
        raise ComponentError('manifest carries no BuildIdentities')
    return found


def pick_identity(manifest: dict, device_class: str, build: str) -> dict:
    matches = [entry for entry in identities(manifest)
               if entry.get('Info', {}).get('DeviceClass') == device_class]
    if not matches:
        raise ComponentError('no identity for device class %r' % device_class)
    got = {entry['Info'].get('BuildNumber') for entry in matches}
    if got != {build}:
        raise ComponentError('device class %r carries build(s) %r, not %r'
                             % (device_class, sorted(got), build))
    # A device class can carry several variants of the same build. They describe
    # the same payload set, so the erase install is preferred and the choice is
    # only refused when the payloads really differ.
    preferred = [entry for entry in matches
                 if entry['Info'].get('Variant') == 'Customer Erase Install (IPSW)']
    if len(preferred) == 1:
        return preferred[0]
    paths = {tuple(sorted((name, item.get('Info', {}).get('Path'))
                          for name, item in entry.get('Manifest', {}).items()))
             for entry in matches}
    if len(paths) != 1:
        raise ComponentError('%d identities for %r carry different payloads; '
                             'variants: %r' % (len(matches), device_class,
                                               [entry['Info'].get('Variant') for entry in matches]))
    return matches[0]


def component_table(identity: dict) -> dict[str, dict]:
    manifest = identity.get('Manifest')
    if not isinstance(manifest, dict):
        raise ComponentError('identity carries no Manifest')
    table = {}
    for name in REQUIRED:
        item = manifest.get(name)
        if item is None:
            raise ComponentError('component %r is missing from this identity' % name)
        info = item.get('Info') or {}
        path = info.get('Path')
        digest = item.get('Digest')
        if not path or not digest:
            raise ComponentError('%s: no Path or Digest' % name)
        method = info.get('HashMethod')
        inferred = False
        if method is None:
            # Several entries (KernelCache, StaticTrustCache) omit HashMethod and
            # still carry the digest, so the method is read off the digest length.
            by_length = {size: label for label, size in DIGEST_BYTES.items()}
            method = by_length.get(len(digest))
            inferred = method is not None
            if method is None:
                raise ComponentError('%s: %d-byte digest matches no known hash'
                                     % (name, len(digest)))
        expected = DIGEST_BYTES.get(method)
        if expected is None:
            raise ComponentError('%s: unsupported HashMethod %r' % (name, method))
        if len(digest) != expected:
            raise ComponentError('%s: digest is %d bytes, %s is %d'
                                 % (name, len(digest), method, expected))
        table[name] = {
            'name': name,
            'path': path,
            'hash_method': method,
            'hash_method_inferred': inferred,
            'digest': digest.hex(),
            'trusted': bool(item.get('Trusted')),
            'encrypted': path.endswith('.aea'),
        }
    return table


def central_directory(ipsw_url: str) -> dict[str, dict]:
    """Member name -> {size, method} using the archive's central directory."""
    import fetch_ipsw_entry as entry_reader
    size = entry_reader.total_size(ipsw_url)
    entries, _ = entry_reader.central_directory(ipsw_url, size)
    return {entry['name']: {'size': entry['uncompressed'], 'method': entry['method'],
                            'compressed': entry['compressed']}
            for entry in entries}


def _wiki_json(query: dict) -> dict:
    url = '%s?%s' % (WIKI, urllib.parse.urlencode(query))
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.load(response)


def _wikitext(title: str) -> str | None:
    payload = _wiki_json({'action': 'parse', 'page': title, 'format': 'json',
                          'prop': 'wikitext'})
    return payload.get('parse', {}).get('wikitext', {}).get('*')


def _fields(wikitext: str) -> dict[str, str]:
    fields = {}
    for line in wikitext.splitlines():
        if '=' not in line:
            continue
        name, value = line.lstrip(' |').split('=', 1)
        fields[name.strip()] = value.strip()
    return fields


def published_keys(build: str, os_path: str, models: list[str]) -> dict[str, str]:
    """Keys for this build, from the page named after the identity's own model.

    The page is accepted only when its RootFS entry is the payload this manifest
    resolved, so a wrong or renamed page is refused instead of trusted.
    """
    titles = ['Keys:FizzSeedUpdate %s (%s)' % (build, model)
              for model in models if model]
    for title in titles:
        wikitext = _wikitext(title)
        if not wikitext:
            continue
        fields = _fields(wikitext)
        if fields.get('Build') != build or fields.get('RootFS') != os_path:
            continue
        keys = {name: fields[field] for name, field in KEY_FIELDS.items()
                if fields.get(field) and fields[field] != 'Unknown'}
        keys['_page'] = title
        return keys
    raise ComponentError('no published table for %s names the payload %s'
                         % (build, os_path))


def resolve(manifest_path: Path, ipsw_url: str, device_class: str, build: str,
            *, fetch_keys: bool = True) -> dict:
    manifest = plistlib.loads(manifest_path.read_bytes())
    identity = pick_identity(manifest, device_class, build)
    info = identity['Info']
    os_path = table_os = identity['Manifest']['OS']['Info']['Path']
    models = []
    for entry in identities(manifest):
        if entry.get('Manifest', {}).get('OS', {}).get('Info', {}).get('Path') == table_os:
            model = entry.get('Ap,ProductType')
            if model and model not in models:
                models.append(model)
    table = component_table(identity)
    members = central_directory(ipsw_url)
    keys = (published_keys(build, table['OS']['path'], models)
            if fetch_keys else {})
    for entry in table.values():
        member = members.get(entry['path'])
        if member is None:
            raise ComponentError('%s: %s is not a member of this archive'
                                 % (entry['name'], entry['path']))
        entry['size'] = member['size']
        entry['zip_method'] = member['method']
        record = {'path': entry['path'], 'method': member['method'],
                  'compressed': member['compressed'], 'size': member['size']}
        if entry['encrypted']:
            key = keys.get(entry['name'])
            if not key:
                raise ComponentError('%s ships as .aea and no key is published'
                                     % entry['name'])
            record['aea_key'] = key
        entry['ipsw_member'] = record
    return {
        'schema': 'nabu-macos27-components-v1',
        'build': build,
        'product_version': (identity.get('ProductMarketingVersion')
                            or info.get('ProductVersion')),
        'device_class': device_class,
        'variant': info.get('Variant'),
        'content_encoding': info.get('ContentEncoding'),
        'os_disk_image_mb': info.get('OSDiskImageSize'),
        'minimum_system_partition_mb': info.get('MinimumSystemPartition'),
        'ipsw_url': ipsw_url,
        'ipsw_manifest_sha384_of_first_4k': hashlib.sha256(
            manifest_path.read_bytes()[:4096]).hexdigest(),
        'components': sorted(table.values(), key=lambda item: item['name']),
        'digest_is_not_authentication': True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--ipsw-url', required=True)
    parser.add_argument('--device-class', default='j180dap')
    parser.add_argument('--build', default='26C5035r')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--no-keys', action='store_true')
    args = parser.parse_args()
    try:
        record = resolve(args.manifest, args.ipsw_url, args.device_class, args.build,
                         fetch_keys=not args.no_keys)
    except ComponentError as exc:
        print('refused: %s' % exc)
        return 1
    text = json.dumps(record, indent=2) + '\n'
    if args.out:
        args.out.write_text(text)
    print('%s (%s) on %s: %d components'
          % (record['build'], record['variant'], record['device_class'],
             len(record['components'])))
    for entry in record['components']:
        print('  %-24s %-42s %12d %s' % (entry['name'], entry['path'], entry['size'],
                                         'aea' if entry['encrypted'] else 'raw'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
