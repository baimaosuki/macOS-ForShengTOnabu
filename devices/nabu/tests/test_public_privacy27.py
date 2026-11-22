"""Regression checks for the public-source privacy gate; use synthetic values."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('privacy', ROOT / 'devices/nabu/scripts/check-public-privacy27.py')
privacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(privacy)


def test_reject_credentials_without_printing_values(capsys):
    synthetic = b'ghp_' + b'X' * 36
    cases = [synthetic, b'-----BEGIN OPENSSH ' + b'PRIVATE KEY-----', b'https://' + b'user:synthetic-pass@example.org/']
    for data in cases:
        findings = privacy.inspect('example.txt', data)
        assert findings and all('value' not in item for item in findings)
    assert not capsys.readouterr().out


def test_protocol_fixtures_and_public_key_are_allowed():
    fixture = b'nabu-log-token=' + b'0102030405060708090a0b0c0d0e0f10'
    assert not privacy.inspect('devices/nabu/tests/fixture.cpp', fixture)
    assert privacy.inspect('config/example.toml', fixture)
    assert not privacy.inspect('recipient.pem', b'-----BEGIN PUBLIC KEY-----')
    assert not privacy.inspect('manual.md', b'/home/user/project /Users/nabu/unixnu-test cpu@0..cpu@7 git@github.com')
    assert privacy.inspect('manual.md', b'/home/' + b'private-person' + b'/project')


def test_device_documentation_identifiers():
    assert privacy.inspect('device.md', b'serialno=' + b'test-unit-123')
    assert privacy.inspect('device.toml', b'ether ' + b'aa:bb:cc:dd:ee:ff')
    assert not privacy.inspect('device.md', b'SERIAL=SERIAL serialno=REDACTED 02:00:00:00:00:01')
    assert not privacy.inspect('code.cpp', b'serialno=' + b'variable')


def test_export_scan_includes_nested_build_sources(tmp_path):
    source = tmp_path / 'tests/build/test_example.py'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'ghp_' + b'X' * 36)
    generated = tmp_path / 'build/generated.txt'
    generated.parent.mkdir()
    generated.write_text('generated output')
    report = privacy.scan(tmp_path)
    assert report['files_scanned'] == 1
    assert report['findings'] == [dict(path='tests/build/test_example.py', line=1, category='access-token')]
