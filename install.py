#!/usr/bin/env python3
"""Install the bundled skills locally; no network, prompts or overwrites."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
NAMES = ('home-fitness-coach', 'analyze-workout-video')


def files(root):
    result = {}
    for path in root.rglob('*'):
        if '__pycache__' in path.parts or path.suffix == '.pyc':
            continue
        if path.is_symlink():
            raise ValueError('symlinks are not supported in skill directories')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result


def install(destination, dry_run=False):
    destination = Path(destination).expanduser().absolute()
    # Keep user data and environments out of the distribution checkout.
    if destination.resolve().is_relative_to(ROOT):
        raise ValueError('choose an installation directory outside this checkout')
    payloads = {}
    states = {}
    for name in NAMES:
        source = ROOT / 'skills' / name
        payloads[name] = files(source)
        if 'SKILL.md' not in payloads[name]:
            raise ValueError(f'missing SKILL.md: {name}')
        target = destination / name
        if target.is_symlink():
            raise ValueError(f'refusing symlink destination: {name}')
        if target.exists():
            if not target.is_dir() or files(target) != payloads[name]:
                raise ValueError(f'existing installation differs: {name}; back it up and move it aside before retrying')
            states[name] = 'unchanged'
        else:
            states[name] = 'would-install' if dry_run else 'installed'
    if dry_run:
        return states
    destination.mkdir(parents=True, exist_ok=True)
    # Validate both skills before any write; each copy is published only when complete.
    with tempfile.TemporaryDirectory(prefix='.fitness-install-', dir=destination) as tmp:
        for name in NAMES:
            if states[name] == 'unchanged':
                continue
            staged = Path(tmp) / name
            for relative, content in payloads[name].items():
                output = staged / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(content)
            if (destination / name).exists() or (destination / name).is_symlink():
                raise ValueError(f'destination appeared during installation: {name}')
            staged.rename(destination / name)
    return states


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dest', type=Path, help='skill parent directory; default: $CODEX_HOME/skills or ~/.codex/skills')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    destination = args.dest or Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex') / 'skills'
    try:
        result = install(destination, args.dry_run)
    except (OSError, ValueError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}), file=sys.stderr)
        return 1
    print(json.dumps({'ok': True, 'skills': result}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
