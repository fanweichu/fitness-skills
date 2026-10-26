#!/usr/bin/env python3
"""Portable preflight, extraction and explicit reviewed-page registration."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

SCRIPTS = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    subs.add_parser('preflight')
    ext = subs.add_parser('extract')
    ext.add_argument('window', type=Path); ext.add_argument('output', type=Path)
    ext.add_argument('--file', required=True); ext.add_argument('--start', type=float, required=True)
    ext.add_argument('--end', type=float, required=True); ext.add_argument('--step', type=float, default=.5)
    ext.add_argument('--times', type=Path)
    reg = subs.add_parser('register', help='only pages the reviewer actually inspected')
    reg.add_argument('manifest', type=Path); reg.add_argument('receipt', type=Path)
    reg.add_argument('--pages', required=True, help='comma-separated inspected page IDs, e.g. p001,p002')
    args = parser.parse_args()
    if args.command == 'preflight':
        tools = {name: shutil.which(name) for name in ('ffmpeg', 'ffprobe')}
        modules = {name: importlib.util.find_spec(name) is not None for name in ('PIL', 'numpy')}
        print(json.dumps({'python': sys.executable, 'scripts': str(SCRIPTS), 'tools': tools,
                          'modules': modules, 'ready': all(tools.values()) and all(modules.values())}, indent=2))
        return 0 if all(tools.values()) and all(modules.values()) else 2
    if args.command == 'extract':
        from extract_review_frames import extract
        from window_geometry import geometry
        marker = json.loads(args.window.with_suffix('.complete.json').read_text())
        stream, rotation = geometry(args.window)
        if rotation or any(stream[k] != marker['validation'][k] for k in ('width', 'height')):
            raise ValueError('window geometry invalid; recreate it with bounded window entrypoint')
        # Fit portrait pages without distorting or discarding frames.
        tile_width = 240
        tile_height = round(tile_width * stream['height'] / stream['width'])
        rows = min(8, max(1, (2000-30)//(tile_height+24)))
        times = json.loads(args.times.read_text()) if args.times else None
        print(extract(args.window, args.output, file=args.file, start=args.start, end=args.end,
                      step=args.step, times=times, rows=rows, tile_width=tile_width))
        return 0
    return subprocess.run([sys.executable, str(SCRIPTS/'analysis_manifest.py'),
        str(args.manifest.resolve()), '--reviewed-pages', str(args.receipt.resolve())+'#'+args.pages,
        '--summary'], check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
