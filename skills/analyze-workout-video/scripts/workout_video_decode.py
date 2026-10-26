#!/usr/bin/env python3
"""Bounded out-of-sandbox decoder entrypoint for workout-video analysis.

This program intentionally exposes only two operations: create a coarse session
proxy or create a short source-local verification window. It accepts no raw
FFmpeg arguments and limits writes to the current project or system temporary
directories, making it suitable for an exact Codex prefix allow rule.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import prepare_session_proxy as proxy


MAX_WINDOW_SECONDS = 180.0


def allowed_output(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    roots = {Path.cwd().resolve(), Path("/private/tmp").resolve(), Path(tempfile.gettempdir()).resolve()}
    if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
        raise SystemExit(f"output must stay under the current project or a system temporary directory: {resolved}")
    return resolved


def prepare_command(args: argparse.Namespace) -> int:
    output_dir = allowed_output(args.output_dir)
    cache_dir = allowed_output(args.cache_dir or (Path.cwd() / ".workout-video-cache"))
    map_path = allowed_output(args.map_path) if args.map_path else output_dir / "segment-map.json"
    forwarded = [
        *args.inputs,
        "--output-dir", str(output_dir),
        "--cache-dir", str(cache_dir),
        "--map", str(map_path),
        "--fps", str(args.fps),
        "--width", str(args.width),
        "--continuity-tolerance", str(args.continuity_tolerance),
        "--decoder", args.decoder,
        "--mode", args.mode,
        "--max-keyframe-gap", str(args.max_keyframe_gap),
        "--min-speed", str(args.min_speed),
        "--max-runtime", str(args.max_runtime),
    ]
    if args.force_chain:
        forwarded.append("--force-chain")
    if args.dry_run:
        forwarded.append("--dry-run")
    return proxy.main(forwarded)


def window_cache_key(source: Path, start: float, end: float, fps: float, width: int) -> str:
    stat = source.stat()
    basis = json.dumps({
        "version": str(proxy.CACHE_VERSION) + "-display-geometry-1",
        "source": str(source),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "start": start,
        "end": end,
        "fps": fps,
        "width": width,
    }, sort_keys=True)
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


def window_command(args: argparse.Namespace) -> int:
    source = Path(args.input).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() not in proxy.VIDEO_EXTENSIONS:
        raise SystemExit(f"video input not found or unsupported: {source}")
    output = allowed_output(args.output)
    if output == source:
        raise SystemExit("output must not overwrite original source")
    if output.suffix.lower() != ".mp4":
        raise SystemExit("verification window output must end in .mp4")
    meta = proxy.probe(source)
    start, end = float(args.start), float(args.end)
    if start < 0 or end <= start or end > float(meta["duration_s"]) + 0.05:
        raise SystemExit(f"window must satisfy 0 <= start < end <= {meta['duration_s']:.3f}")
    if end - start > MAX_WINDOW_SECONDS:
        raise SystemExit(f"verification windows are limited to {MAX_WINDOW_SECONDS:.0f}s")
    output.parent.mkdir(parents=True, exist_ok=True)
    completion = output.with_suffix(".complete.json")
    key = window_cache_key(source, start, end, args.fps, args.width)
    if output.is_file() and completion.is_file():
        try:
            cached = json.loads(completion.read_text(encoding="utf-8"))
            if cached.get("complete") is True and cached.get("cache_key") == key:
                actual = proxy.validate_proxy(output)
                from window_geometry import geometry
                stream, rotation = geometry(output)
                expected = proxy.display_dimensions(meta, args.width)
                recorded = cached.get("validation", {})
                display = cached.get("display_geometry", {})
                if (all(actual[k] == recorded.get(k) for k in ("width", "height", "frame_count"))
                        and (stream["width"], stream["height"]) == expected and rotation == 0
                        and (display.get("width"), display.get("height")) == expected
                        and display.get("rotation") == 0):
                    print(completion)
                    return 0
        except (OSError, ValueError, json.JSONDecodeError, RuntimeError):
            pass
    partial = output.with_name(f".{output.name}.partial.mp4")
    partial.unlink(missing_ok=True)
    duration = end - start
    out_w, out_h = proxy.display_dimensions(meta, args.width)
    routes = ["videotoolbox", "software"] if args.decoder == "auto" else [args.decoder]
    attempts = []
    with proxy.decoder_lock(Path(tempfile.gettempdir()) / "analyze-workout-video-decoder.lock"):
        for backend in routes:
            partial.unlink(missing_ok=True)
            command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning", "-y", "-ss", f"{start:.6f}"]
            if backend == "videotoolbox":
                command.extend(["-hwaccel", "videotoolbox", "-hwaccel_output_format", "videotoolbox_vld"])
            command.extend(["-i", str(source), "-t", f"{duration:.6f}", "-an", "-sn", "-dn"])
            if backend == "videotoolbox":
                command.extend([
                    "-vf", f"fps={args.fps},scale_vt=w={out_w}:h={out_h}",
                    "-c:v", "h264_videotoolbox", "-allow_sw", "0", "-b:v", "1200k",
                ])
            else:
                command.extend([
                    "-vf", f"fps={args.fps},scale='min({args.width},iw)':-2,format=yuv420p",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
                ])
            command.extend(["-movflags", "+faststart", "-progress", "pipe:1", "-stats_period", "0.5", str(partial)])
            result = proxy.run_ffmpeg_progress(command, duration, args.min_speed, args.max_runtime, grace_s=4.0)
            failed = result["returncode"] != 0 or result["stopped_reason"] or (backend == "videotoolbox" and proxy.hardware_failed(result["stderr"]))
            attempt = {
                "backend": backend,
                "status": "failed" if failed else "success",
                "elapsed_s": result["elapsed_s"],
                "speed_x": result["speed_x"],
            }
            if failed:
                attempt["reason"] = result["stopped_reason"] or result["stderr"][-800:] or f"ffmpeg exit {result['returncode']}"
                attempts.append(attempt)
                continue
            validation = proxy.validate_proxy(partial)
            from window_geometry import ensure_geometry
            normalized = ensure_geometry(partial, out_w, out_h, validation["frame_count"])
            validation = proxy.validate_proxy(partial)
            os.replace(partial, output)
            attempts.append(attempt)
            payload = {
                "schema_version": 1,
                "complete": True,
                "cache_key": key,
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "purpose": "source_local_exact_verification",
                "coarse_only": False,
                "source_path": str(source),
                "source_fingerprint": meta["fingerprint"],
                "source_start_s": start,
                "source_end_s": end,
                "sampling_fps": args.fps,
                "sampling_interval_s": round(1 / args.fps, 6),
                "backend": backend,
                "display_geometry": {"width": out_w, "height": out_h, "rotation": 0,
                                     "normalized": normalized},
                "attempts": attempts,
                "validation": validation,
            }
            proxy.atomic_write_text(completion, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            print(completion)
            return 0
    partial.unlink(missing_ok=True)
    raise SystemExit("verification-window decode failed: " + json.dumps(attempts, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    prepare = subparsers.add_parser("prepare", help="create cached coarse session proxies and source maps")
    prepare.add_argument("inputs", nargs="+")
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--cache-dir", type=Path)
    prepare.add_argument("--map", dest="map_path", type=Path)
    prepare.add_argument("--fps", type=float, default=2.0)
    prepare.add_argument("--width", type=int, default=320)
    prepare.add_argument("--continuity-tolerance", type=float, default=3.0)
    prepare.add_argument("--force-chain", action="store_true")
    prepare.add_argument("--decoder", choices=("auto", "videotoolbox", "software"), default="auto")
    prepare.add_argument("--mode", choices=("auto", "keyframes", "uniform"), default="auto")
    prepare.add_argument("--max-keyframe-gap", type=float, default=3.0)
    prepare.add_argument("--min-speed", type=float, default=1.0)
    prepare.add_argument("--max-runtime", type=float, default=900.0)
    prepare.add_argument("--dry-run", action="store_true")
    prepare.set_defaults(handler=prepare_command)
    window = subparsers.add_parser("window", help="decode one short source-local verification window")
    window.add_argument("input")
    window.add_argument("--start", type=float, required=True)
    window.add_argument("--end", type=float, required=True)
    window.add_argument("--output", type=Path, required=True)
    window.add_argument("--fps", type=float, default=10.0)
    window.add_argument("--width", type=int, default=640)
    window.add_argument("--decoder", choices=("auto", "videotoolbox", "software"), default="auto")
    window.add_argument("--min-speed", type=float, default=0.5)
    window.add_argument("--max-runtime", type=float, default=180.0)
    window.set_defaults(handler=window_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "fps", 1) <= 0 or getattr(args, "width", 64) < 64:
        raise SystemExit("fps must be positive and width must be at least 64")
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
