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
    synthetic_token = '0102030405060708090a0b0c0d0e0f10'
    original['entry'] = [e for e in original['entry'] if e['id'] != 'a640-metal-init']
    # Earlier packages ordered otherwise-equivalent args differently.
    for entry in original['entry']:
        if 'args' in entry:
            entry['args'] = ' '.join(reversed(entry['args'].split())) + ' nabu-log-token=' + synthetic_token
    report = menu.install(tmp_path, original)
    installed = raw.replace(b'nabu-log=1 ', b'nabu-log=1 nabu-log-token=' + synthetic_token.encode() + b' ')
    assert (tmp_path / menu.MENU_PATH).read_bytes() == installed
    assert report['sha256'] == hashlib.sha256(installed).hexdigest()
    assert report['template_sha256'] == menu.ALIGNED_SHA256 == hashlib.sha256(raw).hexdigest()
    assert b'nabu-log-token=' not in raw
    assert report['bytes'] == 4909
    assert report['default'] == 'a640-metal' and report['ui_scale'] == 3
    assert report['entry_order'][:2] == ['a640-metal', 'a640-metal-init']
    assert menu.install(tmp_path, tomllib.loads(installed.decode())) == report


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


def test_require_local_identity_and_reject_conflicts(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv('NABU_LOCAL_BOOT_CONFIG', raising=False)
    raw = menu.ALIGNED_CONFIG.read_bytes()
    config = tomllib.loads(raw.decode())
    with pytest.raises(ValueError, match='identity required'):
        menu.install(tmp_path, config)
    assert not (tmp_path / menu.MENU_PATH).exists()
    first = '0102030405060708090a0b0c0d0e0f10'
    second = '1112131415161718191a1b1c1d1e1f20'
    deployed = raw.replace(b'nabu-log=1 ', b'nabu-log=1 nabu-log-token=' + first.encode() + b' ')
    private = tmp_path / 'private.toml'
    private.write_bytes(deployed)
    monkeypatch.setenv('NABU_LOCAL_BOOT_CONFIG', str(private))
    report = menu.install(tmp_path, config)
    assert report['log_allocation_identity_preserved']
    assert (tmp_path / menu.MENU_PATH).read_bytes() == deployed
    bad = tomllib.loads(deployed.replace(first.encode(), second.encode()).decode())
    with pytest.raises(ValueError, match='Conflicting'):
        menu.install(tmp_path, bad)
    for value in ('0' * 32, 'not-a-valid-token', first + ' nabu-log-token=' + first):
        broken = tomllib.loads(deployed.replace(first.encode(), value.encode()).decode())
        with pytest.raises(ValueError):
            menu.allocation_token(broken)
    assert first not in capsys.readouterr().out


def test_explicit_first_build_without_esp_journal(tmp_path, monkeypatch):
    monkeypatch.delenv('NABU_LOCAL_BOOT_CONFIG', raising=False)
    raw = menu.ALIGNED_CONFIG.read_bytes()
    reference = tomllib.loads(raw.decode())
    report = menu.install(tmp_path, reference, without_persistent_log=True)
    installed = (tmp_path / menu.MENU_PATH).read_bytes()
    assert b'nabu-log=1' not in installed and b'nabu-log-token=' not in installed
    assert b'nabu-log=0' in installed and b'log=1' in installed
    assert not report['persistent_log_enabled']
    assert not report['log_allocation_identity_preserved']
