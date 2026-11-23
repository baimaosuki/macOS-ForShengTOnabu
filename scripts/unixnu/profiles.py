"""Layered ARM64, SoC, OS, and device configuration."""
from copy import deepcopy
import os
import re
from pathlib import Path

from .config import load_toml
from .util import Error


def merge(base, overlay):
    result = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def resolve_os(ctx, wanted):
    """--os 17, 26 or 27 (the OS major version) or a full profiles/os directory name.
    A bare version resolves within the iPadOS profiles; prefix it with macos- (macos-27,
    macos-27.3) or ipados- (ipados-27) when several OS families ship the same major."""
    names = sorted(path.name for path in (ctx.root / 'profiles/os').iterdir() if path.is_dir())
    if wanted in names:
        return wanted
    family, version = 'ipados-', wanted
    for prefix in ('ipados-', 'macos-'):
        if wanted.startswith(prefix):
            family, version = prefix, wanted[len(prefix):]
            break
    matches = [name for name in names
               if name.startswith(f'{family}{version}.') or name.startswith(f'{family}{version}-')]
    if len(matches) != 1:
        raise Error(f'--os {wanted}: expected one of {", ".join(names)} or 17, 26, 27, macos-27')
    return matches[0]


def load(ctx, allow_fetch=False):
    from . import feeds
    board = ctx.raw_board.get('board', {})
    soc_name, os_name = board.get('soc_source'), board.get('os_profile')
    if getattr(ctx.args, 'os', None):
        os_name = resolve_os(ctx, ctx.args.os)
    if not soc_name or not os_name:
        raise Error('schema_version = 2 requires [board] soc_source and os_profile')
    if Path(os_name).name != os_name or os_name in ('.', '..'):
        raise Error('[board] os_profile must be a profile directory name')
    ctx.soc = feeds.resolve_soc(ctx, soc_name, allow_fetch)
    os_root = ctx.root / 'profiles/os' / os_name
    arch_name = board.get('arch', load_toml(os_root / 'profile.toml').get('os', {}).get('arch', 'arm64'))
    if arch_name not in ('arm64', 'arm64e'):
        raise Error(f'{os_name}: [os] arch must be arm64 or arm64e')
    ctx.profile_roots = {'arch': ctx.root / 'profiles/arch' / arch_name, 'soc': ctx.soc.path,
                         'os': os_root, 'device': ctx.device}
    ctx.profile_files = [ctx.profile_roots['arch'] / 'profile.toml', ctx.soc.path / 'soc.toml',
                         ctx.profile_roots['os'] / 'profile.toml', ctx.device / 'board.toml']
    result = {}
    for path in ctx.profile_files:
        result = merge(result, load_toml(path))
    # board.toml [os_overrides."<profile>"] holds the board values that differ for one OS profile (kernel archives, kernel
    # arguments); the table of the selected profile is merged over the board, the others are dropped.
    overrides = result.pop('os_overrides', {})
    chosen = deepcopy(overrides.get(os_name, {}))
    removed = chosen.pop('remove', [])
    result = merge(result, chosen)
    for dotted in removed:   # keys the board sets for other profiles and this one does not have
        *parents, leaf = dotted.split('.')
        node = result
        for key in parents:
            node = node.get(key, {})
        node.pop(leaf, None)
    result.setdefault('board', {})['os_profile'] = os_name
    root_profile = getattr(ctx.args, 'root_profile', None) or os.environ.get('UNIXNU_ROOT_PROFILE')
    if root_profile:
        overlay = load_toml(Path(root_profile).expanduser().resolve())
        if not {'root'} <= set(overlay) <= {'root', 'payload'} or not isinstance(overlay['root'], dict):
            raise Error('root profile needs [root] and optionally [payload] text_fixups')
        expected = {name + suffix for name in ('os', 'systemos', 'app')
                    for suffix in ('_volume_uuid', '_image_sha256')}
        if set(overlay['root']) != expected:
            raise Error('root profile needs all three image hashes and volume UUIDs')
        import uuid
        for key, value in overlay['root'].items():
            if not isinstance(value, str):
                raise Error('root profile values must be strings')
            if key.endswith('_image_sha256') and not re.fullmatch('[0-9a-f]{64}', value):
                raise Error('root profile has an invalid image SHA256')
            if key.endswith('_volume_uuid'):
                try:
                    valid = str(uuid.UUID(value)).upper()
                except ValueError as error:
                    raise Error('root profile has an invalid volume UUID') from error
                if valid != result.get('root', {}).get(key, '').upper():
                    raise Error('root profile must preserve this OS profile\'s volume UUIDs')
        result['root'] = merge(result.get('root', {}), overlay['root'])
        if 'payload' in overlay:
            payload = overlay['payload']
            if set(payload) != {'text_fixups'} or not isinstance(payload['text_fixups'], list):
                raise Error('root profile [payload] may override only text_fixups')
            pattern = r'[A-Za-z0-9_.\-]{1,63},[0-9a-fA-F]{1,16},(?:[0-9a-fA-F]{2}){1,16},(?:[0-9a-fA-F]{2}){1,16}'
            for fixup in payload['text_fixups']:
                if not isinstance(fixup, str) or not re.fullmatch(pattern, fixup):
                    raise Error('root profile has an invalid signed-text fixup')
                if len(fixup.split(',')[2]) != len(fixup.split(',')[3]):
                    raise Error('signed-text fixup changes the instruction range length')
            result['payload']['text_fixups'] = payload['text_fixups']
    return result


def resolve_path(ctx, value, modules=None):
    """Resolve profile-prefixed paths and component:ID/path references."""
    if value.startswith('component:'):
        identifier, sep, relative = value[len('component:'):].partition('/')
        if not sep:
            raise Error(f'component path needs ID/path: {value}')
        modules = modules if modules is not None else getattr(ctx, 'modules', {})
        module = next((m for m in modules.values() if m.name == identifier), None)
        if not module or module.path is None:
            raise Error(f'component source unavailable: {identifier}; run unixnu fetch')
        return module.path / relative
    prefix, separator, relative = value.partition(':')
    if separator and prefix in ctx.profile_roots:
        return ctx.profile_roots[prefix] / relative
    path = Path(value)
    return path if path.is_absolute() else ctx.device / path
