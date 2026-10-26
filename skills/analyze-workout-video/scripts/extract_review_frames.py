#!/usr/bin/env python3
"""Paginate source-local cached windows without silent truncation (requires Pillow)."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import tempfile
from bisect import bisect_left
from pathlib import Path

from count_evidence import digest, number, read, require


def probe_window(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
                        "-show_entries", "stream=width,height:frame=best_effort_timestamp_time",
                        "-show_streams", "-of", "json", str(path)], capture_output=True, text=True, timeout=60, check=True)
    d = json.loads(r.stdout)
    return d["streams"][0], [float(f["best_effort_timestamp_time"]) for f in d["frames"]]


def plan_frames(pts, source_start, start, end, step, explicit=None):
    require(pts and all(a < b for a, b in zip(pts, pts[1:])), "invalid frame timestamps")
    start, end, step = number(start), number(end), number(step)
    require(start < end and step >= .08, "invalid sampling interval/range")
    if explicit is None:
        requested = [start + i * step for i in range(math.floor((end-start)/step)+1)] + [end]
    else:
        requested = [number(t) for t in explicit] + [start, end]
    require(len(requested) <= 2402 and all(start <= t <= end for t in requested), "out-of-range/excessive sampling request")
    indices = set()
    for t in requested:
        local_t = t - source_start
        i = bisect_left(pts, local_t)
        choices = [j for j in (i-1, i) if 0 <= j < len(pts)]
        j = min(choices, key=lambda k: abs(pts[k] - local_t))
        require(abs(pts[j] - local_t) <= .15, "requested time lies beyond decoded frames")
        indices.add(j)
    return sorted(indices)


def extract(window, out, *, file, start, end, step=.5, times=None, columns=6, rows=8, tile_width=240):
    from PIL import Image, ImageDraw, ImageFont
    window = Path(window).resolve(); out = Path(out).resolve()
    require(window.is_file(), "source-local window missing")
    marker = window.with_suffix(".complete.json")
    require(marker.is_file(), f"completion marker missing: {marker}")
    m = read(marker)
    require(m.get("complete") is True and m.get("coarse_only") is False
            and m.get("purpose") == "source_local_exact_verification", "only completed source-local windows accepted")
    require(8 <= number(m["sampling_fps"]) <= 12, "window must have 8-12 fps")
    require(0 < m["source_end_s"] - m["source_start_s"] <= 180, "window exceeds bounded decoder limit")
    require(m["source_start_s"] <= start < end <= m["source_end_s"], "range outside window")
    require(1 <= columns <= 6 and 1 <= rows <= 8 and 160 <= tile_width <= 320, "invalid sheet geometry")
    source = Path(m["source_path"])
    if source.is_file():
        s = source.stat()
        basis = f'{source}\0{s.st_size}\0{s.st_mtime_ns}'
        require(m["source_fingerprint"] == "stat-sha256:" + hashlib.sha256(basis.encode()).hexdigest(), "source fingerprint changed")
    stream, pts = probe_window(window)
    require(len(pts) == m["validation"]["frame_count"] and all(stream[k] == m["validation"][k] for k in ("width", "height")), "window failed FFprobe completion validation")
    require(all(abs((b-a) - 1/m["sampling_fps"]) < .002 for a,b in zip(pts,pts[1:])), "unexpected window cadence")
    indices = plan_frames(pts, m["source_start_s"], start, end, step, times)
    height = round(tile_width * stream["height"] / stream["width"])
    require(columns * tile_width <= 1920 and (height + 24) * rows + 30 <= 2000, "contact sheet exceeds readable dimensions")
    # Never overwrite old evidence. A failed extraction leaves no completed receipt.
    require(not out.exists(), "output directory already exists; choose a new run directory")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".review-frames-", dir=out.parent) as tmp:
        stage = Path(tmp)
        select = "+".join(f"eq(n\\,{i})" for i in indices)
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(window), "-an",
                        "-vf", f"select={select}", "-fps_mode", "passthrough", "-q:v", "3",
                        "-start_number", "0", str(stage/"frame-%06d.jpg")], check=True, timeout=90)
        files = sorted(stage.glob("frame-*.jpg"))
        require(len(files) == len(indices), "FFmpeg did not emit every requested frame")
        frames = []
        for i, image_path in zip(indices, files):
            frames.append({"id": f"f{i:06d}", "frame_index": i, "window_pts_s": pts[i],
                           "source_s": round(m["source_start_s"] + pts[i], 6),
                           "path": image_path.name, "sha256": digest(image_path)})
        font = ImageFont.load_default(size=15) if hasattr(ImageFont, "load_default") else None
        pages = []
        capacity = columns * rows
        for page_no, first in enumerate(range(0, len(frames), capacity), 1):
            chunk = frames[first:first+capacity]
            used_rows = math.ceil(len(chunk)/columns)
            sheet = Image.new("RGB", (columns*tile_width, used_rows*(height+24)+30), "#16242d")
            draw = ImageDraw.Draw(sheet)
            draw.text((8,7), f'{file} / page {page_no} / source time', fill="white", font=font)
            for n, f in enumerate(chunk):
                x, y = n % columns * tile_width, 30 + n // columns * (height+24)
                with Image.open(stage/f["path"]) as im:
                    require(im.size == (stream["width"],stream["height"]), "decoded frame dimensions differ")
                    sheet.paste(im.resize((tile_width,height)), (x,y))
                t = f["source_s"]
                draw.text((x+2,y+height+3), f'{int(t//60):02}:{t%60:04.1f} {f["id"]}', font=font, fill="white")
            p = stage/f'page-{page_no:03d}.jpg'; sheet.save(p, quality=90)
            pages.append({"id": f"p{page_no:03d}", "path": p.name, "sha256": digest(p),
                          "frame_ids": [f["id"] for f in chunk], "tile_width": tile_width,
                          "width": sheet.width, "height": sheet.height})
        receipt = {"schema_version": 1, "kind": "source_review_frames",
                   "source": {"file": file, "path": m["source_path"], "fingerprint": m["source_fingerprint"]},
                   "window": {"path_at_extraction": str(window), "sha256": digest(window), "completion": m},
                   "requested_range_s": [start,end], "sampling_step_s": step if times is None else None,
                   "expected_frame_count": len(indices), "requested_frame_ids": [f["id"] for f in frames],
                   "frames": frames, "pages": pages}
        (stage/"receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        # Atomic publication of the complete directory.
        os.rename(stage, out)
    return out/"receipt.json"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("window", type=Path, help="name.mp4 with adjacent name.complete.json"); p.add_argument("output_dir", type=Path)
    p.add_argument("--file", required=True, help="original source label used in report")
    p.add_argument("--start", type=float, required=True, help="original-file seconds, inclusive")
    p.add_argument("--end", type=float, required=True, help="last sampled original-file second, inclusive")
    p.add_argument("--step", type=float, default=.5)
    p.add_argument("--times", type=Path, help="JSON array of original-file seconds, plus range endpoints")
    p.add_argument("--columns", type=int, default=6); p.add_argument("--rows", type=int, default=8)
    p.add_argument("--tile-width", type=int, default=240)
    a = p.parse_args()
    try:
        times = json.loads(a.times.read_text()) if a.times else None
        print(extract(a.window,a.output_dir,file=a.file,start=a.start,end=a.end,step=a.step,
                      times=times,columns=a.columns,rows=a.rows,tile_width=a.tile_width))
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"review extraction failed: {exc}")


if __name__ == "__main__":
    main()
