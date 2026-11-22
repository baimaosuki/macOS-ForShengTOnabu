"""Exercise the published menu installer and its Data-initialization contract."""
import hashlib
import importlib.util
from pathlib import Path
import tomllib
import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('nabu_boot_config27', ROOT / 'devices/nabu/scripts/nabu_boot_config27.py')
menu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(menu)


def test_exact_menu_and_old_payload_arguments(tmp_path):
    raw = menu.ALIGNED_CONFIG.read_bytes()
    original = tomllib.loads(raw.decode())
    original['entry'] = [e for e in original['entry'] if e['id'] != 'a640-metal-init']
    # Earlier packages ordered otherwise-equivalent args differently.
    for entry in original['entry']:
        if 'args' in entry:
            entry['args'] = ' '.join(reversed(entry['args'].split()))
    report = menu.install(tmp_path, original)
    assert (tmp_path / menu.MENU_PATH).read_bytes() == raw
    assert report['sha256'] == menu.ALIGNED_SHA256 == hashlib.sha256(raw).hexdigest()
    assert report['bytes'] == 4909
    assert report['default'] == 'a640-metal' and report['ui_scale'] == 3
    assert report['entry_order'][:2] == ['a640-metal', 'a640-metal-init']
    assert menu.install(tmp_path, tomllib.loads(raw.decode())) == report


def test_reject_changed_payload_and_unsafe_reset(tmp_path):
    raw = menu.ALIGNED_CONFIG.read_bytes()
    reference = tomllib.loads(raw.decode())
    reference['entry'][0]['args'] += ' nabu-new-required-arg=1'
    with pytest.raises(ValueError, match='args differ'):
        menu.install(tmp_path, reference)
    assert not (tmp_path / menu.MENU_PATH).exists()
    for changed in (raw.replace(b'default = "a640-metal"', b'default = "xnu"'),
                    raw.replace(b'scale = 3', b'scale = 1'),
                    raw.replace(b'args = "debug=', b'args = "data-reset=1 debug=', 1),
                    raw.replace(b'confirm_args = ["data-reset=1"]', b'confirm_args = []', 1)):
        with pytest.raises(ValueError):
            menu.validate(changed)
