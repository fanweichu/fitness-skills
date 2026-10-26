#!/usr/bin/env python3
"""Extract low-cost navigation signals from a workout video.

The JSON output contains media metadata, a compact motion curve, motion-active
intervals, and optional per-segment cycle candidates. Audio transient analysis is
opt-in. All candidates require visual verification before being reported as reps.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover
    raise SystemExit("numpy is required: run this script with a Python runtime that includes numpy") from exc


CACHE_VERSION = 2


def fast_file_fingerprint(path: Path) -> str:
    size = path.stat().st_size
    digest = hashlib.sha256()
    digest.update(str(size).encode("ascii"))
    with path.open("rb") as handle:
        digest.update(handle.read(1024 * 1024))
        if size > 1024 * 1024:
            handle.seek(max(0, size - 1024 * 1024))
            digest.update(handle.read(1024 * 1024))
    return digest.hexdigest()


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
        temp_path = Path(handle.name)
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)


def run(cmd: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise SystemExit(f"required executable not found: {name}")
    return path


def probe(path: Path, ffprobe: str) -> dict:
    result = run([
        ffprobe, "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ])
    if result.returncode:
        raise SystemExit(result.stderr.decode("utf-8", "replace").strip())
    data = json.loads(result.stdout)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise SystemExit("no video stream found")
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    duration = float(video.get("duration") or data.get("format", {}).get("duration") or 0)
    rate_text = video.get("avg_frame_rate", "0/1")
    num, den = (float(x) for x in rate_text.split("/"))
    rotation = 0
    for side in video.get("side_data_list", []):
        if "rotation" in side:
            rotation = int(side["rotation"])
    if not rotation and video.get("tags", {}).get("rotate"):
        rotation = int(video["tags"]["rotate"])
    return {
        "duration_s": duration,
        "width": int(video["width"]),
        "height": int(video["height"]),
        "fps": num / den if den else 0,
        "avg_frame_rate": rate_text,
        "nominal_frame_rate": video.get("r_frame_rate"),
        "start_time_s": float(video.get("start_time") or data.get("format", {}).get("start_time") or 0),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name") if audio else None,
        "has_audio": audio is not None,
        "rotation_degrees": rotation,
    }


def hardware_works(path: Path, ffmpeg: str) -> tuple[bool, str]:
    result = run([
        ffmpeg, "-v", "error", "-hwaccel", "videotoolbox", "-ss", "0",
        "-i", str(path), "-frames:v", "1", "-f", "null", "-",
    ])
    error = result.stderr.decode("utf-8", "replace").strip()
    failed = ("Failed setup for format videotoolbox" in error or "hwaccel initialisation returned error" in error)
    return result.returncode == 0 and not failed, error


def robust_threshold(values: np.ndarray, multiplier: float, floor: float = 0.0) -> float:
    if values.size == 0:
        return 0.0
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    return max(floor, median + multiplier * max(mad, median * 0.05, 1e-9))


def separated_peaks(values: np.ndarray, threshold: float, min_gap_samples: int) -> list[int]:
    candidates = []
    for i in range(1, len(values) - 1):
        if values[i] >= threshold and values[i] >= values[i - 1] and values[i] > values[i + 1]:
            candidates.append(i)
    chosen: list[int] = []
    for idx in sorted(candidates, key=lambda i: float(values[i]), reverse=True):
        if all(abs(idx - old) >= min_gap_samples for old in chosen):
            chosen.append(idx)
    return sorted(chosen)


def audio_transients(path: Path, ffmpeg: str, sample_rate: int = 8000) -> tuple[list[dict], dict]:
    window = max(1, int(sample_rate * 0.02))
    rms_parts: list[np.ndarray] = []
    carry = np.empty(0, dtype=np.float32)
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen([
            ffmpeg, "-nostdin", "-v", "error", "-i", str(path), "-vn", "-ac", "1",
            "-ar", str(sample_rate), "-f", "f32le", "-",
        ], stdout=subprocess.PIPE, stderr=errors)
        assert proc.stdout is not None
        while True:
            raw = proc.stdout.read(1024 * 1024)
            if not raw:
                break
            samples = np.frombuffer(raw, dtype=np.float32)
            if carry.size:
                samples = np.concatenate((carry, samples))
            usable_count = len(samples) // window * window
            if usable_count:
                frames = samples[:usable_count].reshape(-1, window)
                rms_parts.append(np.sqrt(np.mean(frames * frames, axis=1)))
            carry = samples[usable_count:].copy()
        code = proc.wait()
        errors.seek(0)
        error_text = errors.read().decode("utf-8", "replace").strip()
    if code:
        return [], {"status": "decode-failed", "error": error_text}
    if not rms_parts:
        return [], {"status": "ok-no-signal", "error": None}
    rms = np.concatenate(rms_parts)
    threshold = robust_threshold(rms, 8.0, floor=1e-4)
    # Keep close peaks so an intentional double clap is not merged.
    peaks = separated_peaks(rms, threshold, max(1, int(0.08 / 0.02)))
    baseline = float(np.median(rms)) + 1e-9
    events = [
        {"time_s": round((i + 0.5) * 0.02, 3), "strength_over_median": round(float(rms[i] / baseline), 2)}
        for i in peaks
    ]
    return events, {"status": "ok", "error": None, "threshold_rms": round(float(threshold), 7)}


def double_peak_candidates(events: list[dict]) -> list[dict]:
    pairs = []
    for left, right in zip(events, events[1:]):
        gap = right["time_s"] - left["time_s"]
        if 0.08 <= gap <= 0.8:
            pairs.append({"first_s": left["time_s"], "second_s": right["time_s"], "gap_s": round(gap, 3)})
    return pairs


def decode_backend(path: Path, ffmpeg: str, policy: str) -> tuple[list[str], dict]:
    if policy == "never" or platform.system() != "Darwin":
        reason = None if policy == "never" else "videotoolbox is only probed on macOS"
        return [], {"attempted_backend": "software", "final_backend": "software", "fallback_reason": reason}
    ok, error = hardware_works(path, ffmpeg)
    if ok:
        return ["-hwaccel", "videotoolbox"], {"attempted_backend": "videotoolbox", "final_backend": "videotoolbox", "fallback_reason": None}
    if policy == "require":
        raise SystemExit(f"videotoolbox probe failed: {error}")
    return [], {"attempted_backend": "videotoolbox", "final_backend": "software", "fallback_reason": error or "unknown probe failure"}


def motion_curve(path: Path, ffmpeg: str, meta: dict, fps: float, width: int, hw_args: list[str]) -> tuple[np.ndarray, np.ndarray]:
    src_w, src_h = meta["width"], meta["height"]
    if abs(meta.get("rotation_degrees", 0)) % 180 == 90:
        src_w, src_h = src_h, src_w
    height = max(2, int(round((src_h * width / src_w) / 2) * 2))
    cmd = [ffmpeg, "-nostdin", "-v", "error", *hw_args, "-i", str(path), "-an", "-vf", f"fps={fps},scale={width}:{height},format=gray", "-f", "rawvideo", "-"]
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errors)
        assert proc.stdout is not None
        frame_bytes = width * height
        values: list[float] = []
        previous = None
        while True:
            raw = proc.stdout.read(frame_bytes)
            if len(raw) < frame_bytes:
                break
            current = np.frombuffer(raw, dtype=np.uint8).astype(np.int16)
            values.append(0.0 if previous is None else float(np.mean(np.abs(current - previous))))
            previous = current
        code = proc.wait()
        errors.seek(0)
        error_text = errors.read().decode("utf-8", "replace").strip()
    silent_hw_fallback = bool(hw_args) and ("Failed setup for format videotoolbox" in error_text or "hwaccel initialisation returned error" in error_text)
    if code or silent_hw_fallback:
        raise RuntimeError(f"video signal decode failed: {error_text}")
    signal = np.asarray(values, dtype=np.float32)
    if len(signal) >= 5:
        signal = np.convolve(signal, np.ones(5, dtype=np.float32) / 5, mode="same")
    times = np.arange(len(signal), dtype=np.float32) / fps
    return times, signal


def active_intervals(times: np.ndarray, motion: np.ndarray, fps: float) -> list[dict]:
    if motion.size == 0:
        return []
    threshold = robust_threshold(motion, 2.5)
    active = motion >= threshold
    # Bridge gaps shorter than 0.75 s in one linear pass.
    gap = max(1, int(0.75 * fps))
    i = 0
    while i < len(active):
        if active[i]:
            i += 1
            continue
        start = i
        while i < len(active) and not active[i]:
            i += 1
        if start > 0 and i < len(active) and i - start <= gap:
            active[start:i] = True
    result = []
    i = 0
    while i < len(active):
        if not active[i]:
            i += 1
            continue
        j = i + 1
        while j < len(active) and active[j]:
            j += 1
        if (j - i) / fps >= 1.0:
            result.append({"start_s": round(float(times[i]), 3), "end_s": round(float(times[min(j - 1, len(times) - 1)]), 3)})
        i = j
    return result


def parse_segment(text: str) -> tuple[float, float, str]:
    parts = text.split(":", 2)
    if len(parts) < 2:
        raise argparse.ArgumentTypeError("segment must be START:END or START:END:LABEL (seconds)")
    start, end = float(parts[0]), float(parts[1])
    if end <= start:
        raise argparse.ArgumentTypeError("segment END must be greater than START")
    return start, end, parts[2] if len(parts) == 3 else ""


def cycle_candidates(times: np.ndarray, motion: np.ndarray, segment: tuple[float, float, str], fps: float) -> dict:
    start, end, label = segment
    mask = (times >= start) & (times <= end)
    local = motion[mask]
    local_times = times[mask]
    if local.size < 3:
        peaks: list[int] = []
    else:
        threshold = robust_threshold(local, 1.2)
        peaks = separated_peaks(local, threshold, max(1, int(0.55 * fps)))
    return {
        "start_s": start,
        "end_s": end,
        "label": label or None,
        "motion_peak_count": len(peaks),
        "motion_peak_times_s": [round(float(local_times[i]), 3) for i in peaks],
        "signal_semantics": "whole_frame_absolute_difference_peaks",
        "not_rep_count": True,
        "warning": "navigation peaks only; never use, halve, or otherwise transform this number into repetitions",
    }


def downsample_curve(times: np.ndarray, motion: np.ndarray, points: int = 1200) -> list[list[float]]:
    if len(times) <= points:
        indices = range(len(times))
    else:
        indices = np.linspace(0, len(times) - 1, points, dtype=int)
    return [[round(float(times[i]), 3), round(float(motion[i]), 4)] for i in indices]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--output", type=Path, help="write JSON here; default stdout")
    parser.add_argument("--cache-dir", type=Path, help="reuse a fingerprint-and-settings keyed JSON result")
    parser.add_argument("--motion-fps", type=float, default=2.0)
    parser.add_argument("--motion-width", type=int, default=96)
    parser.add_argument("--hardware", choices=("auto", "never", "require"), default="auto")
    parser.add_argument("--audio-transients", action="store_true", help="opt in to audio transient candidates; disabled by default")
    parser.add_argument("--segment", action="append", default=[], type=parse_segment, help="START:END[:LABEL] in seconds; repeatable")
    args = parser.parse_args()
    if not args.video.is_file():
        raise SystemExit(f"video not found: {args.video}")
    if args.motion_fps <= 0 or args.motion_width < 16:
        raise SystemExit("motion fps must be positive and motion width must be at least 16")
    source_fingerprint = fast_file_fingerprint(args.video)
    cache_payload = {
        "version": CACHE_VERSION,
        "source_fingerprint": source_fingerprint,
        "motion_fps": args.motion_fps,
        "motion_width": args.motion_width,
        "hardware": args.hardware,
        "audio_transients": args.audio_transients,
        "segments": args.segment,
    }
    cache_key = hashlib.sha256(json.dumps(cache_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    cache_path = args.cache_dir.resolve() / f"{cache_key}.json" if args.cache_dir else None
    if cache_path and cache_path.is_file():
        rendered = cache_path.read_text(encoding="utf-8").rstrip() + "\n"
        if args.output:
            atomic_write_text(args.output, rendered)
        else:
            print(rendered, end="")
        return 0
    ffmpeg, ffprobe = require_tool("ffmpeg"), require_tool("ffprobe")
    meta = probe(args.video, ffprobe)
    hw_args, backend = decode_backend(args.video, ffmpeg, args.hardware)
    try:
        times, motion = motion_curve(args.video, ffmpeg, meta, args.motion_fps, args.motion_width, hw_args)
    except RuntimeError as exc:
        if hw_args and args.hardware == "auto":
            backend["final_backend"] = "software"
            backend["fallback_reason"] = str(exc)
            times, motion = motion_curve(args.video, ffmpeg, meta, args.motion_fps, args.motion_width, [])
        else:
            raise SystemExit(str(exc))
    for start, end, _ in args.segment:
        if start < 0 or end > meta["duration_s"] + 0.5:
            raise SystemExit(f"segment {start}:{end} is outside video duration 0:{meta['duration_s']}")
    if args.audio_transients and meta["has_audio"]:
        audio_events, audio_status = audio_transients(args.video, ffmpeg)
        duration_minutes = max(meta["duration_s"] / 60, 1 / 60)
        event_rate = len(audio_events) / duration_minutes
        audio_status["raw_candidate_count"] = len(audio_events)
        audio_status["candidate_rate_per_minute"] = round(event_rate, 2)
        if event_rate > 12:
            audio_status.update({"status": "too-many-candidates", "reliable": False, "reason": "transient rate is too high for boundary use"})
            audio_events = []
        else:
            audio_status["reliable"] = audio_status.get("status") == "ok"
    elif args.audio_transients:
        audio_events, audio_status = [], {"status": "no-audio-stream", "error": None, "reliable": False}
    else:
        audio_events, audio_status = [], {"status": "disabled", "error": None, "reliable": False}
    result = {
        "source": str(args.video.resolve()),
        "source_fingerprint": f"sha256-fast:{source_fingerprint}",
        "signal_cache_key": cache_key,
        "metadata": meta,
        "decode": backend,
        "audio_transient_candidates": audio_events,
        "audio_analysis": audio_status,
        "double_peak_candidates": double_peak_candidates(audio_events),
        "motion_active_intervals": active_intervals(times, motion, args.motion_fps),
        "motion_curve": downsample_curve(times, motion),
        "segments": [cycle_candidates(times, motion, segment, args.motion_fps) for segment in args.segment],
        "timing": {"basis": "regular sample grid after ffmpeg fps filter", "sampling_interval_s": round(1 / args.motion_fps, 6), "use_source_pts_for_frame_accurate_reports": True},
        "caveat": "Signals are candidate locators; exercise labels, set boundaries, reps, and form findings require visual confirmation.",
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if cache_path:
        atomic_write_text(cache_path, rendered)
    if args.output:
        atomic_write_text(args.output, rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
