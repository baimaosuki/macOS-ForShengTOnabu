#!/usr/bin/env python3
"""Check public source for credentials and deployment-specific identifiers.

Reports locations/categories only, never matched values. This is a pattern
check of the selected tree, not a certification of all secrets or Git history.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
PATTERNS = {
    'private-key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----'),
    'access-token': re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|xox[baprs]-[A-Za-z0-9-]{15,}|AKIA[A-Z0-9]{16}|sk-(?:proj-)?[A-Za-z0-9_-]{30,})\b'),
    'credential-url': re.compile(rb'https?://[^/\s:@]+:[^/\s@]+@[^\s]+'),
}
LOG_TOKEN = re.compile(rb'nabu-log-token=([0-9a-fA-F]{32})(?![0-9a-fA-F])')
HOME = re.compile(rb'/(home|Users)/([^/\s"\x27<>`,;:)]+)')
DOC_SERIAL = re.compile(rb'\b(?:serialno|ro\.boot\.serialno|SERIAL)\s*[:=]\s*([A-Za-z0-9_.:-]+)')
MAC = re.compile(rb'\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b')
PUBLIC_USERS = {b'user', b'NAME', b'name', b'nabu', b'admin'}
SKIP = {'.git', '__pycache__', '.pytest_cache', 'node_modules', '.cache'}
GENERATED_ROOTS = {'out', 'build'}


def inspect(name, data):
    findings = []
    for line, content in enumerate(data.splitlines(), 1):
        for category, pattern in PATTERNS.items():
            if pattern.search(content):
                findings.append(dict(path=name, line=line, category=category))
        for match in LOG_TOKEN.finditer(content):
            # Explicit protocol fixtures (including malformed zero tests)
            # are not private deployment identities.
            fixture = (b'0102030405060708090a0b0c0d0e0f10', b'00000000000000000000000000000000')
            if '/tests/' not in '/' + name or match[1].lower() not in fixture:
                findings.append(dict(path=name, line=line, category='deployment-log-identity'))
        if name.endswith(('.md', '.toml')):
            for match in HOME.finditer(content):
                user = match[2]
                if user not in PUBLIC_USERS and not user.startswith((b'$', b'{')) and user != b'keys':
                    findings.append(dict(path=name, line=line, category='personal-home-path'))
            for match in DOC_SERIAL.finditer(content):
                if match[1].upper() not in {b'SERIAL', b'DEVICE', b'REDACTED', b'UNKNOWN'}:
                    findings.append(dict(path=name, line=line, category='device-serial'))
            for match in MAC.finditer(content):
                if match[0].lower() != b'02:00:00:00:00:01':
                    findings.append(dict(path=name, line=line, category='network-device-address'))
    return findings


def source_files(root):
    # In a checkout use main/current tracked paths, so generated products and
    # private local inputs never get read. An exported tree has no .git.
    if (root / '.git').exists():
        paths = subprocess.check_output(['git', 'ls-tree', '-r', '-z', '--name-only', 'HEAD'], cwd=root).split(b'\0')
        return [root / name.decode() for name in paths if name and (root / name.decode()).is_file()]
    return [path for path in root.rglob('*') if path.is_file()
            and not SKIP.intersection(path.relative_to(root).parts)
            and path.relative_to(root).parts[0] not in GENERATED_ROOTS]


def scan(root):
    findings = []
    files = source_files(root)
    for path in files:
        findings.extend(inspect(path.relative_to(root).as_posix(), path.read_bytes()))
    return dict(passed=not findings, files_scanned=len(files), findings=findings,
                matched_values_printed=False, history_scanned=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = scan(args.root)
    text = json.dumps(report, indent=2) + '\n'
    if args.output:
        args.output.write_text(text)
    print(text, end='')
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
