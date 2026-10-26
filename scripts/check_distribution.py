#!/usr/bin/env python3
"""Check the explicit release inventory and common sensitive text patterns."""
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    'absolute home path': re.compile(r'(?:/Users/|/home/|[A-Za-z]:[\\/]Users[\\/])[\w.-]+'),
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'access token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{24,})\b'),
    'email address': re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'),
    'dated camera filename': re.compile(r'DJI_20\d{12}_\d+'),
}


def audit(root=ROOT):
    root = Path(root)
    expected = set((root / 'release-files.txt').read_text().splitlines())
    errors = []
    for name in expected:
        path = PurePosixPath(name)
        if not name or path.is_absolute() or '..' in path.parts or path.as_posix() != name:
            errors.append('invalid inventory entry')
    actual = set()
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if relative.parts[0] == '.git':
            continue
        if path.is_symlink():
            errors.append(f'symlink: {relative}')
        elif path.is_file():
            actual.add(relative.as_posix())
    for name in sorted(actual - expected):
        errors.append(f'not in release inventory: {name}')
    for name in sorted(expected - actual):
        errors.append(f'missing release file: {name}')
    for name in sorted(actual & expected):
        try:
            content = (root / name).read_text(encoding='utf-8')
            if '\x00' in content:
                raise ValueError('binary data')
        except (UnicodeError, ValueError):
            errors.append(f'non-text release file: {name}')
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(content):
                # Report the category/file only, never echo the matched secret.
                errors.append(f'{label}: {name}')
    if (root / '.git').exists():
        result = subprocess.run(['git', '-C', str(root), 'ls-files', '-z'], capture_output=True, check=True)
        tracked = set(result.stdout.decode().rstrip('\0').split('\0')) - {''}
        for name in sorted(tracked - expected):
            errors.append(f'tracked file outside release inventory: {name}')
    return errors


if __name__ == '__main__':
    failures = audit()
    if failures:
        print('\n'.join(failures), file=sys.stderr)
        raise SystemExit(1)
    print('Distribution check passed: inventory and common sensitive patterns.')
