"""Canonical nabu ESP menu, installed after signed GPU addon integration."""
import hashlib
import os
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ALIGNED_CONFIG = ROOT / 'config/unixnu.aligned.toml'
ALIGNED_SHA256 = 'd636e6d5025d3b010f525590d8f33c10f1a1334f34365979f0092082474e2ea0'
MENU_PATH = Path('EFI/UniXNU/unixnu.toml')


def public_args(args):
    return {value for value in args.split() if not value.startswith('nabu-log-token=')}


def allocation_token(*configs):
    """An ESP write-allocation identity, not an account/API credential.

    Keep it local, and never print or regenerate it without provisioning the
    matching log-file allocation. Conflicting deployment inputs must fail.
    """
    found = set()
    for config in configs:
        if config is None:
            continue
        for entry in config.get('entry', []):
            values = [arg.partition('=')[2] for arg in entry.get('args', '').split()
                      if arg.startswith('nabu-log-token=')]
            if len(values) > 1:
                raise ValueError('Duplicate local log allocation identity')
            for value in values:
                if not re.fullmatch('[0-9a-fA-F]{32}', value) or int(value, 16) == 0:
                    raise ValueError('Invalid local log allocation identity')
                found.add(value.lower())
    if len(found) > 1:
        raise ValueError('Conflicting local log allocation identities')
    return next(iter(found), None)


def validate(raw, reference=None):
    config = tomllib.loads(raw.decode('utf-8'))
    entries = config.get('entry', [])
    ids = [entry['id'] for entry in entries]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate nabu menu entry')
    if (config.get('version') != 1 or config.get('boot', {}).get('default') != 'a640-metal'
            or config.get('ui', {}).get('scale') != 3 or ids[:2] != ['a640-metal', 'a640-metal-init']):
        raise ValueError('Expected GPU default/first entry, GPU init second and UI scale3')
    by_id = {entry['id']: entry for entry in entries}
    for key in ('a640-metal', 'xnu', 'coldstart', 'install', 'reset', 'shutdown'):
        if key not in by_id:
            raise ValueError(f'Missing nabu entry: {key}')
    for entry in entries:
        if entry.get('type') == 'xnu':
            tokens = entry['args'].split()
            if 'data-reset=1' in tokens or len(entry['args'].encode()) >= 1024:
                raise ValueError('Data reset must be confirmation-only; boot args must fit the loader')
            if 'data-persist=2' not in tokens:
                raise ValueError('Persistent Data must remain enabled')
    gpu, init = by_id['a640-metal'], by_id['a640-metal-init']
    for key in ('args', 'path', 'initrd', 'type'):
        if init.get(key) != gpu.get(key):
            raise ValueError('GPU initialization entry must reuse the ordinary GPU payload/args')
    for key in ('install', 'a640-metal-init'):
        entry = by_id[key]
        if (not entry.get('confirm') or entry.get('confirm_args') != ['data-reset=1']
                or len(entry.get('confirm_titles', [])) != 1):
            raise ValueError('Data initialization needs an explicit loader confirmation')
    if 'nabu-gpu=1' not in gpu['args'].split() or 'nabu-gpu=1' in by_id['xnu']['args'].split():
        raise ValueError('Expected GPU entry and CPU fallback')
    # Do not replace arguments with an out-of-date template when future driver
    # packages need different options. Ordering of equivalent args is harmless.
    if reference is not None:
        for entry in reference.get('entry', []):
            current = by_id.get(entry.get('id'))
            if current is not None and entry.get('type') == 'xnu':
                if public_args(current['args']) != public_args(entry['args']):
                    raise ValueError(f'Aligned config args differ from payload: {entry["id"]}')
                for field in ('path', 'initrd', 'type'):
                    if current.get(field) != entry.get(field):
                        raise ValueError(f'Aligned config payload differs: {entry["id"]}/{field}')
    return config


def install(esp, reference=None, local_boot_config=None, without_persistent_log=False):
    raw = ALIGNED_CONFIG.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != ALIGNED_SHA256:
        raise ValueError('Canonical aligned menu changed; review and update its pinned checksum')
    config = validate(raw, reference)
    configured = local_boot_config or os.environ.get('NABU_LOCAL_BOOT_CONFIG')
    local = tomllib.loads(Path(configured).read_text()) if configured else None
    token = None if without_persistent_log else allocation_token(reference, local)
    wants_log = any('nabu-log=1' in entry.get('args', '').split() for entry in config['entry'])
    if wants_log and token is None and not without_persistent_log:
        raise ValueError('Log allocation identity required: preserve an existing ESP menu or set NABU_LOCAL_BOOT_CONFIG')
    # The published template has no device identity. Materialize it only in
    # the local artifact, preserving the existing allocation and menu layout.
    if token:
        raw = raw.replace(b'nabu-log=1 ', b'nabu-log=1 nabu-log-token=' + token.encode() + b' ')
    if without_persistent_log:
        raw = raw.replace(b'nabu-log=1 ', b'nabu-log=0 ')
        if reference is not None:
            import copy
            reference = copy.deepcopy(reference)
            for entry in reference.get('entry', []):
                if 'args' in entry:
                    entry['args'] = ' '.join('nabu-log=0' if v == 'nabu-log=1' else v
                                             for v in entry['args'].split())
    config = validate(raw, reference)
    menu = Path(esp) / MENU_PATH
    menu.parent.mkdir(parents=True, exist_ok=True)
    menu.write_bytes(raw)
    if menu.read_bytes() != raw:
        raise OSError('Aligned menu readback mismatch')
    return dict(path=MENU_PATH.as_posix(), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                template_sha256=digest, log_allocation_identity_preserved=token is not None,
                persistent_log_enabled=not without_persistent_log,
                default=config['boot']['default'], ui_scale=config['ui']['scale'],
                entry_order=[entry['id'] for entry in config['entry']],
                data_reset_confirmation_only=True)
