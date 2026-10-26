#!/usr/bin/env python3
"""Generate compact local GIF evidence for confirmed workout issues.

This script performs no model calls. It reads either a legacy workout-report JSON
or a durable schema-v2 session, extracts one short source-relative window around
each confirmed issue, and optionally writes the GIF paths back atomically.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return data


def atomic_write(path: Path, data: dict[str, Any]) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def source_map(data: dict[str, Any]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for source in data.get("sources") or []:
        path = source.get("path") or source.get("original_path")
        if source.get("file") and path:
            result[str(source["file"])] = Path(str(path))
    return result


def slug(value: str) -> str:
    ascii_slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return ascii_slug[:64] or "issue"


def ffmpeg_filter(fps: int, width: int) -> str:
    return (
        f"fps={fps},scale={width}:-2:flags=lanczos,"
        "split[s0][s1];[s0]palettegen=max_colors=128:stats_mode=diff[p];"
        "[s1][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle"
    )


def generate_one(
    ffmpeg: str,
    source: Path,
    output: Path,
    start_s: float,
    duration_s: float,
    fps: int,
    width: int,
    label: str,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel", "error",
        "-ss", f"{start_s:.3f}",
        "-t", f"{duration_s:.3f}",
        "-i", str(source),
        "-an",
        "-filter_complex", ffmpeg_filter(fps, width),
        "-loop", "0",
        "-y",
        str(output),
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if result.returncode:
        raise SystemExit(f"GIF generation failed for {source}: {result.stderr.strip()}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="legacy report JSON or durable session JSON")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--before", type=float, default=1.5)
    parser.add_argument("--after", type=float, default=2.5)
    parser.add_argument("--fps", type=int, default=10)
    parser.add_argument("--width", type=int, default=560)
    parser.add_argument("--update-json", action="store_true")
    parser.add_argument("--require-all", action="store_true")
    args = parser.parse_args()
    if args.before < 0 or args.after <= 0 or args.fps < 4 or args.width < 240:
        raise SystemExit("invalid GIF window, fps, or width")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg not found")
    data = load_json(args.input)
    sources = source_map(data)
    issues = data.get("issues") or []
    generated = 0
    missing = 0
    for index, issue in enumerate(issues, 1):
        if str(issue.get("status") or "confirmed") != "confirmed":
            continue
        source_file = str(issue.get("file") or issue.get("source_file") or "")
        source = sources.get(source_file)
        if not source or not source.is_file():
            missing += 1
            print(f"SKIP missing source for issue {index}: {source_file}")
            continue
        timestamp_s = float(issue.get("timestamp_s") or 0)
        start_s = max(0.0, timestamp_s - args.before)
        duration_s = args.before + args.after
        name = str(issue.get("issue_id") or issue.get("exercise") or "issue")
        output = args.output_dir.resolve() / f"issue-{index:02d}-{slug(name)}.gif"
        label = f"{source_file}  {issue.get('timestamp') or timestamp_s}"
        generate_one(ffmpeg, source, output, start_s, duration_s, args.fps, args.width, label)
        issue["gif"] = str(output)
        issue["gif_window"] = {
            "start_s": round(start_s, 3),
            "duration_s": round(duration_s, 3),
            "fps": args.fps,
            "width": args.width,
            "source_fingerprint_basis": "source path plus issue timestamp",
        }
        print(output)
        generated += 1
    if args.require_all and missing:
        raise SystemExit(f"{missing} confirmed issues have unavailable source videos")
    if args.update_json and generated:
        atomic_write(args.input.resolve(), data)
    print(f"generated={generated} missing={missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
