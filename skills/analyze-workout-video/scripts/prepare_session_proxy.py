#!/usr/bin/env python3
"""Prepare cached coarse workout proxies and authoritative source-time maps.

The default path decodes only keyframes with one software decoder thread. This
is a discovery artifact, never an authoritative source for set boundaries or
repetition counts. Sparse-GOP sources use a hardware-only uniform proxy when
available. Every cache entry has a completion manifest so interrupted output
is never reused.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".m2ts"}
DJI_NAME = re.compile(r"DJI_(\d{14})_(\d{4})_D", re.IGNORECASE)
CACHE_VERSION = 3
HARDWARE_FAILURE_MARKERS = (
    "VideoToolbox malfunction",
    "Failed setup for format videotoolbox",
    "hwaccel initialisation returned error",
    "Function not implemented",
    "A hardware device reference is required",
)


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


def atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def discover(values: list[str]) -> list[Path]:
    paths: list[Path] = []
    for value in values:
        path = Path(value).expanduser().resolve()
        if path.is_dir():
            paths.extend(p for p in path.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS)
        elif path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
            paths.append(path)
        else:
            raise SystemExit(f"video input not found or unsupported: {path}")
    unique = {str(path): path for path in paths}
    return sorted(unique.values(), key=sort_key)


def filename_clock(path: Path) -> tuple[datetime | None, int | None]:
    match = DJI_NAME.search(path.stem)
    if not match:
        return None, None
    return datetime.strptime(match.group(1), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc), int(match.group(2))


def sort_key(path: Path) -> tuple[Any, ...]:
    clock, sequence = filename_clock(path)
    if clock is not None:
        return (0, clock.timestamp(), sequence or 0, path.name.lower())
    return (1, path.name.lower())


def probe(path: Path) -> dict[str, Any]:
    result = run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,pix_fmt,avg_frame_rate,r_frame_rate:stream_tags=rotate:stream_side_data=rotation:format=duration:format_tags=creation_time",
        "-of", "json", str(path),
    ])
    if result.returncode:
        raise SystemExit(f"ffprobe failed for {path}: {result.stderr.strip()[-500:]}")
    payload = json.loads(result.stdout)
    stream = (payload.get("streams") or [{}])[0]
    fmt = payload.get("format") or {}
    clock, sequence = filename_clock(path)
    creation = (fmt.get("tags") or {}).get("creation_time")
    if clock is None and creation:
        try:
            clock = datetime.fromisoformat(str(creation).replace("Z", "+00:00"))
        except ValueError:
            clock = None
    stat = path.stat()
    fingerprint_basis = f"{path}\0{stat.st_size}\0{stat.st_mtime_ns}"
    return {
        "file": path.stem,
        "path": str(path),
        "duration_s": float(fmt["duration"]),
        "size_bytes": stat.st_size,
        "fingerprint": "stat-sha256:" + hashlib.sha256(fingerprint_basis.encode()).hexdigest(),
        "codec": stream.get("codec_name"),
        "width": stream.get("width"),
        "height": stream.get("height"),
        "pix_fmt": stream.get("pix_fmt"),
        "avg_frame_rate": stream.get("avg_frame_rate"),
        "rotation": ((stream.get("tags") or {}).get("rotate") or ((stream.get("side_data_list") or [{}])[0].get("rotation"))),
        "recording_start": clock.isoformat() if clock else None,
        "sequence": sequence,
    }


def compatible(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return all(left.get(key) == right.get(key) for key in ("codec", "width", "height", "pix_fmt", "rotation"))


def continuous(left: dict[str, Any], right: dict[str, Any], tolerance_s: float) -> bool:
    if not compatible(left, right):
        return False
    left_clock = datetime.fromisoformat(left["recording_start"]) if left.get("recording_start") else None
    right_clock = datetime.fromisoformat(right["recording_start"]) if right.get("recording_start") else None
    if not left_clock or not right_clock:
        return False
    gap = right_clock.timestamp() - (left_clock.timestamp() + float(left["duration_s"]))
    seq_ok = left.get("sequence") is None or right.get("sequence") is None or right["sequence"] == left["sequence"] + 1
    return seq_ok and abs(gap) <= tolerance_s


def make_chains(items: list[dict[str, Any]], tolerance_s: float, force_chain: bool) -> list[list[dict[str, Any]]]:
    chains: list[list[dict[str, Any]]] = []
    for item in items:
        if not chains or (not force_chain and not continuous(chains[-1][-1], item, tolerance_s)):
            chains.append([item])
        else:
            chains[-1].append(item)
    return chains


def ffconcat_escape(value: str) -> str:
    return value.replace("'", "'\\''")


def cache_key(chain: list[dict[str, Any]], fps: float, width: int, mode: str = "auto", max_keyframe_gap: float = 3.0) -> str:
    basis = json.dumps({
        "version": CACHE_VERSION,
        "sources": [x["fingerprint"] for x in chain],
        "fps": fps,
        "width": width,
        "mode": mode,
        "max_keyframe_gap": max_keyframe_gap,
    }, sort_keys=True)
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


def keyframe_times(path: Path) -> list[float]:
    result = run([
        "ffprobe", "-v", "error", "-threads", "1", "-skip_frame", "nokey",
        "-select_streams", "v:0", "-show_frames",
        "-show_entries", "frame=best_effort_timestamp_time", "-of", "csv=p=0", str(path),
    ])
    if result.returncode:
        raise RuntimeError(f"keyframe probe failed for {path}: {result.stderr.strip()[-500:]}")
    values: list[float] = []
    for raw in result.stdout.splitlines():
        text = raw.strip().split(",", 1)[0]
        try:
            values.append(float(text))
        except ValueError:
            continue
    return values


def inspect_keyframes(chain: list[dict[str, Any]]) -> dict[str, Any]:
    frames: list[dict[str, Any]] = []
    gaps: list[float] = []
    offset = 0.0
    for source in chain:
        local = keyframe_times(Path(source["path"]))
        for index, timestamp in enumerate(local):
            frames.append({
                "proxy_frame_index": len(frames),
                "unified_time_s": round(offset + timestamp, 6),
                "source_file": source["file"],
                "source_path": source["path"],
                "source_time_s": round(timestamp, 6),
            })
            if index:
                gaps.append(local[index] - local[index - 1])
        offset += float(source["duration_s"])
    return {
        "frame_count": len(frames),
        "max_gap_s": round(max(gaps), 6) if gaps else None,
        "median_gap_s": round(sorted(gaps)[len(gaps) // 2], 6) if gaps else None,
        "frames": frames,
    }


def choose_proxy_mode(requested: str, profile: dict[str, Any], max_keyframe_gap: float) -> str:
    if requested != "auto":
        return requested
    maximum = profile.get("max_gap_s")
    if profile.get("frame_count", 0) >= 2 and maximum is not None and maximum <= max_keyframe_gap:
        return "keyframes"
    return "uniform"


def hardware_failed(stderr: str) -> bool:
    return any(marker.lower() in stderr.lower() for marker in HARDWARE_FAILURE_MARKERS)


def videotoolbox_preflight(chain: list[dict[str, Any]], width: int) -> dict[str, Any]:
    """Exercise the same zero-copy decode/filter/encode path at start/middle/end."""
    checks: list[dict[str, Any]] = []
    for source in chain:
        duration = float(source["duration_s"])
        out_w, out_h = display_dimensions(source, width)
        for label, timestamp in (("start", 0.0), ("middle", max(0.0, duration / 2 - 0.5)), ("end", max(0.0, duration - 1.5))):
            command = [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning", "-y",
                "-ss", f"{timestamp:.3f}", "-hwaccel", "videotoolbox",
                "-hwaccel_output_format", "videotoolbox_vld", "-i", source["path"],
                "-t", "1", "-an", "-vf", f"scale_vt=w={out_w}:h={out_h}",
                "-c:v", "h264_videotoolbox", "-allow_sw", "0", "-f", "null", "/dev/null",
            ]
            try:
                completed = subprocess.run(command, text=True, capture_output=True, check=False, timeout=30)
                stderr = completed.stderr.strip()
                ok = completed.returncode == 0 and not hardware_failed(stderr)
                reason = None if ok else stderr[-500:] or f"ffmpeg exit {completed.returncode}"
            except subprocess.TimeoutExpired:
                ok, reason = False, "preflight timed out after 30s"
            checks.append({"source_file": source["file"], "position": label, "source_time_s": round(timestamp, 3), "status": "success" if ok else "failed", "reason": reason})
            if not ok:
                return {"status": "failed", "checks": checks}
    return {"status": "success", "checks": checks}


def should_abort_for_speed(elapsed_s: float, decoded_s: float, source_duration_s: float, min_speed: float, max_runtime_s: float, grace_s: float) -> tuple[bool, str | None]:
    if elapsed_s > max_runtime_s:
        return True, f"runtime exceeded {max_runtime_s:.1f}s"
    if elapsed_s < grace_s or decoded_s <= 0:
        return False, None
    speed = decoded_s / elapsed_s
    projected = source_duration_s / speed if speed > 0 else float("inf")
    if speed < min_speed and projected > max_runtime_s:
        return True, f"decode speed {speed:.2f}x projects {projected:.1f}s, budget is {max_runtime_s:.1f}s"
    return False, None


def run_ffmpeg_progress(command: list[str], source_duration_s: float, min_speed: float, max_runtime_s: float, grace_s: float = 8.0) -> dict[str, Any]:
    progress_queue: queue.Queue[str | None] = queue.Queue()
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, text=True, bufsize=1)
        assert proc.stdout is not None

        def reader() -> None:
            for line in proc.stdout:
                progress_queue.put(line.rstrip())
            progress_queue.put(None)

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        started = time.monotonic()
        decoded_s = 0.0
        stopped_reason: str | None = None
        stream_done = False
        while proc.poll() is None or not stream_done:
            try:
                item = progress_queue.get(timeout=0.25)
                if item is None:
                    stream_done = True
                elif item.startswith("out_time_us="):
                    try:
                        decoded_s = max(decoded_s, float(item.split("=", 1)[1]) / 1_000_000)
                    except ValueError:
                        pass
            except queue.Empty:
                pass
            elapsed = time.monotonic() - started
            abort, reason = should_abort_for_speed(elapsed, decoded_s, source_duration_s, min_speed, max_runtime_s, grace_s)
            if abort and proc.poll() is None:
                stopped_reason = reason
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                break
        code = proc.wait()
        elapsed = time.monotonic() - started
        errors.seek(0)
        stderr = errors.read().decode("utf-8", "replace").strip()
    return {
        "returncode": code,
        "stderr": stderr,
        "elapsed_s": round(elapsed, 3),
        "decoded_s": round(decoded_s, 3),
        "speed_x": round(decoded_s / elapsed, 3) if elapsed > 0 else None,
        "stopped_reason": stopped_reason,
    }


@contextmanager
def decoder_lock(path: Path, wait_s: float = 120.0) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        deadline = time.monotonic() + wait_s
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"decoder lock remained busy for {wait_s:.0f}s: {path}")
                time.sleep(0.25)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def display_dimensions(source: dict[str, Any], width: int) -> tuple[int, int]:
    src_w, src_h = int(source["width"]), int(source["height"])
    if abs(int(source.get("rotation") or 0)) % 180 == 90:
        src_w, src_h = src_h, src_w
    height = max(2, int(round((src_h * width / src_w) / 2) * 2))
    return width, height


def make_concat(chain: list[dict[str, Any]]) -> Path:
    with tempfile.NamedTemporaryFile("w", suffix=".ffconcat", encoding="utf-8", delete=False) as handle:
        path = Path(handle.name)
        handle.write("ffconcat version 1.0\n")
        for source in chain:
            handle.write(f"file '{ffconcat_escape(source['path'])}'\n")
    return path


def decode_command(chain: list[dict[str, Any]], output: Path, fps: float, width: int, mode: str, backend: str, concat_path: Path) -> list[str]:
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning", "-y"]
    if mode == "keyframes":
        command.extend(["-threads", "1", "-skip_frame", "nokey"])
    elif backend == "videotoolbox":
        command.extend(["-hwaccel", "videotoolbox", "-hwaccel_output_format", "videotoolbox_vld"])
    command.extend(["-f", "concat", "-safe", "0", "-i", str(concat_path), "-an", "-sn", "-dn"])
    if mode == "keyframes":
        command.extend([
            "-vf", f"scale='min({width},iw)':-2,format=yuv420p", "-fps_mode", "vfr",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "31",
        ])
    elif backend == "videotoolbox":
        out_w, out_h = display_dimensions(chain[0], width)
        command.extend([
            "-vf", f"fps={fps},scale_vt=w={out_w}:h={out_h}",
            "-c:v", "h264_videotoolbox", "-allow_sw", "0", "-b:v", "650k",
        ])
    else:
        command.extend([
            "-vf", f"fps={fps},scale='min({width},iw)':-2,format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "29",
        ])
    command.extend(["-movflags", "+faststart", "-progress", "pipe:1", "-stats_period", "0.5", str(output)])
    return command


def validate_proxy(path: Path) -> dict[str, Any]:
    result = run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
        "-show_entries", "stream=codec_name,width,height,nb_read_frames:format=duration", "-of", "json", str(path),
    ])
    if result.returncode:
        raise RuntimeError(f"proxy validation failed: {result.stderr.strip()[-500:]}")
    payload = json.loads(result.stdout)
    stream = (payload.get("streams") or [{}])[0]
    frame_count = int(stream.get("nb_read_frames") or 0)
    if frame_count < 1 or path.stat().st_size < 1024:
        raise RuntimeError("proxy validation failed: empty or incomplete output")
    return {
        "codec": stream.get("codec_name"),
        "width": stream.get("width"),
        "height": stream.get("height"),
        "frame_count": frame_count,
        "duration_s": float((payload.get("format") or {}).get("duration") or 0),
        "size_bytes": path.stat().st_size,
    }


def decode(chain: list[dict[str, Any]], output: Path, fps: float, width: int, backend: str, mode: str = "auto", keyframe_profile: dict[str, Any] | None = None, min_speed: float = 1.0, max_runtime_s: float = 900.0) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    profile = keyframe_profile or inspect_keyframes(chain)
    chosen_mode = choose_proxy_mode(mode, profile, 3.0)
    duration = sum(float(source["duration_s"]) for source in chain)
    attempts: list[dict[str, Any]] = []
    partial = output.with_name(f".{output.name}.partial.mp4")
    concat_path = make_concat(chain)
    try:
        routes: list[tuple[str, str]]
        if chosen_mode == "keyframes":
            routes = [("keyframes", "software")]
        elif backend == "auto":
            routes = [("uniform", "videotoolbox")]
            if profile.get("frame_count", 0):
                routes.append(("keyframes", "software"))
            else:
                routes.append(("uniform", "software"))
        else:
            routes = [("uniform", backend)]
        for route_mode, route_backend in routes:
            partial.unlink(missing_ok=True)
            preflight: dict[str, Any] | None = None
            if route_backend == "videotoolbox":
                preflight = videotoolbox_preflight(chain, width)
                if preflight["status"] != "success":
                    attempts.append({
                        "mode": route_mode,
                        "backend": route_backend,
                        "stage": "start-middle-end-preflight",
                        "status": "failed",
                        "reason": next((item["reason"] for item in preflight["checks"] if item["status"] == "failed"), "unknown preflight failure"),
                        "checks": preflight["checks"],
                    })
                    continue
            command = decode_command(chain, partial, fps, width, route_mode, route_backend, concat_path)
            result = run_ffmpeg_progress(command, duration, min_speed, max_runtime_s)
            failed = result["returncode"] != 0 or result["stopped_reason"] or (route_backend == "videotoolbox" and hardware_failed(result["stderr"]))
            attempt = {
                "mode": route_mode,
                "backend": route_backend,
                "status": "failed" if failed else "success",
                "elapsed_s": result["elapsed_s"],
                "speed_x": result["speed_x"],
            }
            if preflight is not None:
                attempt["preflight"] = preflight
            if failed:
                attempt["reason"] = result["stopped_reason"] or result["stderr"][-800:] or f"ffmpeg exit {result['returncode']}"
                attempts.append(attempt)
                continue
            validation = validate_proxy(partial)
            os.replace(partial, output)
            attempts.append(attempt)
            return {
                "status": "created",
                "backend": route_backend,
                "analysis_mode": "keyframe_coarse" if route_mode == "keyframes" else "uniform_coarse",
                "coarse_only": True,
                "authoritative_for": ["candidate_discovery"],
                "prohibited_for": ["exact_set_boundaries", "completed_rep_counts", "form_findings"],
                "attempts": attempts,
                "validation": validation,
            }
        raise RuntimeError("proxy decode failed: " + json.dumps(attempts, ensure_ascii=False))
    finally:
        partial.unlink(missing_ok=True)
        concat_path.unlink(missing_ok=True)


def completed_cache(proxy: Path, completion_path: Path, key: str) -> dict[str, Any] | None:
    if not proxy.is_file() or not completion_path.is_file():
        return None
    try:
        payload = json.loads(completion_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("cache_key") != key or payload.get("complete") is not True:
        return None
    try:
        validation = validate_proxy(proxy)
    except RuntimeError:
        return None
    return {
        "status": "reused",
        "backend": payload.get("backend", "cached"),
        "analysis_mode": payload.get("analysis_mode", "unknown_coarse"),
        "coarse_only": True,
        "authoritative_for": ["candidate_discovery"],
        "prohibited_for": ["exact_set_boundaries", "completed_rep_counts", "form_findings"],
        "attempts": [],
        "validation": validation,
    }


def chain_segments(chain: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float]:
    offset = 0.0
    segments = []
    for source in chain:
        duration = float(source["duration_s"])
        segments.append({
            "unified_start_s": round(offset, 6),
            "unified_end_s": round(offset + duration, 6),
            "source_file": source["file"],
            "source_path": source["path"],
            "source_start_s": 0.0,
            "source_end_s": duration,
            "source_fingerprint": source["fingerprint"],
        })
        offset += duration
    return segments, offset


def translate_unified_time(segments: list[dict[str, Any]], unified_time_s: float) -> dict[str, Any]:
    for index, segment in enumerate(segments):
        is_last = index == len(segments) - 1
        if segment["unified_start_s"] <= unified_time_s < segment["unified_end_s"] or (is_last and unified_time_s <= segment["unified_end_s"]):
            return {
                "source_file": segment["source_file"],
                "source_path": segment["source_path"],
                "source_time_s": round(segment["source_start_s"] + unified_time_s - segment["unified_start_s"], 6),
            }
    raise ValueError(f"unified timestamp outside chain: {unified_time_s}")


def build_map(chains: list[list[dict[str, Any]]], output_dir: Path, fps: float, width: int, backend: str, dry_run: bool, *, mode: str = "auto", max_keyframe_gap: float = 3.0, cache_dir: Path | None = None, min_speed: float = 1.0, max_runtime_s: float = 900.0, lock_path: Path = Path(tempfile.gettempdir()) / "analyze-workout-video-decoder.lock") -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_root = (cache_dir or output_dir).resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "schema_version": 2,
        "pipeline_version": CACHE_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "proxy_settings": {"requested_mode": mode, "fps": fps, "max_width": width, "audio": False, "max_keyframe_gap_s": max_keyframe_gap},
        "rules": {
            "coarse_only": True,
            "exact_counts_require_source_local_decode": True,
            "exact_boundaries_require_source_local_decode": True,
        },
        "chains": [],
    }
    for index, chain in enumerate(chains, 1):
        profile = inspect_keyframes(chain)
        selected_mode = choose_proxy_mode(mode, profile, max_keyframe_gap)
        key = cache_key(chain, fps, width, selected_mode, max_keyframe_gap)
        proxy = cache_root / f"session-chain-{index:02d}-{key}.mp4"
        completion = cache_root / f"session-chain-{index:02d}-{key}.complete.json"
        keyframe_map = cache_root / f"session-chain-{index:02d}-{key}.keyframes.json"
        segments, duration = chain_segments(chain)
        decode_result = completed_cache(proxy, completion, key)
        if decode_result is None and dry_run:
            decode_result = {
                "status": "planned", "backend": backend,
                "analysis_mode": "keyframe_coarse" if selected_mode == "keyframes" else "uniform_coarse",
                "coarse_only": True, "attempts": [],
            }
        elif decode_result is None:
            with decoder_lock(lock_path):
                decode_result = decode(chain, proxy, fps, width, backend, selected_mode, profile, min_speed, max_runtime_s)
            completion_payload = {
                "complete": True,
                "cache_key": key,
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "backend": decode_result["backend"],
                "analysis_mode": decode_result["analysis_mode"],
                "validation": decode_result["validation"],
            }
            atomic_write_text(completion, json.dumps(completion_payload, ensure_ascii=False, indent=2) + "\n")
        atomic_write_text(keyframe_map, json.dumps({
            "schema_version": 1,
            "coarse_only": True,
            "chain_duration_s": round(duration, 6),
            "profile": {name: value for name, value in profile.items() if name != "frames"},
            "frames": profile["frames"],
        }, ensure_ascii=False, indent=2) + "\n")
        result["chains"].append({
            "chain_id": index,
            "continuous": len(chain) > 1,
            "proxy_path": str(proxy),
            "cache_completion_path": str(completion),
            "keyframe_time_map_path": str(keyframe_map),
            "duration_s": round(duration, 6),
            "sources": chain,
            "segments": segments,
            "keyframe_profile": {name: value for name, value in profile.items() if name != "frames"},
            "decode": decode_result,
        })
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", help="Video files or folders")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, help="persistent fingerprint-keyed proxy cache")
    parser.add_argument("--map", dest="map_path", type=Path)
    parser.add_argument("--fps", type=float, default=2.0)
    parser.add_argument("--width", type=int, default=320)
    parser.add_argument("--continuity-tolerance", type=float, default=3.0)
    parser.add_argument("--force-chain", action="store_true")
    parser.add_argument("--decoder", choices=("auto", "videotoolbox", "software"), default="auto")
    parser.add_argument("--mode", choices=("auto", "keyframes", "uniform"), default="auto")
    parser.add_argument("--max-keyframe-gap", type=float, default=3.0)
    parser.add_argument("--min-speed", type=float, default=1.0)
    parser.add_argument("--max-runtime", type=float, default=900.0)
    parser.add_argument("--lock-path", type=Path, default=Path(tempfile.gettempdir()) / "analyze-workout-video-decoder.lock")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.fps <= 0 or args.width < 64 or args.max_keyframe_gap <= 0:
        raise SystemExit("fps and max keyframe gap must be positive; width must be at least 64")
    if args.min_speed <= 0 or args.max_runtime <= 0:
        raise SystemExit("min speed and max runtime must be positive")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("ffmpeg and ffprobe are required")
    sources = [probe(path) for path in discover(args.inputs)]
    if not sources:
        raise SystemExit("no supported videos found")
    chains = make_chains(sources, args.continuity_tolerance, args.force_chain)
    payload = build_map(
        chains, args.output_dir.resolve(), args.fps, args.width, args.decoder, args.dry_run,
        mode=args.mode, max_keyframe_gap=args.max_keyframe_gap,
        cache_dir=args.cache_dir.resolve() if args.cache_dir else None,
        min_speed=args.min_speed, max_runtime_s=args.max_runtime, lock_path=args.lock_path,
    )
    map_path = (args.map_path or args.output_dir / "segment-map.json").resolve()
    atomic_write_text(map_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(map_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
