#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render a self-contained workout analysis HTML report from validated JSON."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from count_evidence import validate_report_evidence


VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")
COLORS = ["#2f6b4f", "#315d7d", "#9a4637", "#8d6c20", "#6c4f7d", "#397777"]


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def confidence(value: Any) -> tuple[str, str]:
    raw = str(value or "medium").lower()
    if raw in {"high", "高", "中高"}:
        return "high", "高" if raw != "中高" else "中高"
    if raw in {"low", "低"}:
        return "low", "低"
    return "medium", "中"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"invalid report JSON: {message}")


def valid_report_date(value: Any) -> bool:
    raw = str(value or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return False
    try:
        return date.fromisoformat(raw).isoformat() == raw
    except ValueError:
        return False


def local_url(value: str, json_dir: Path, output_dir: Path, must_exist: bool = False) -> str:
    if not value:
        return value
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value) or value.startswith("data:"):
        require(not must_exist, "confirmed issue evidence must be a retained local file")
        return value
    source = Path(value)
    if not source.is_absolute():
        source = (json_dir / source).resolve()
    if must_exist:
        require(source.is_file(), f"issue screenshot does not exist: {source}")
    return Path(os.path.relpath(source, output_dir)).as_posix()


def timeline_segments(item: dict[str, Any]) -> list[dict[str, Any]]:
    segments = item.get("segments")
    if isinstance(segments, list) and segments:
        return segments
    return [{
        "file": item.get("file"),
        "start": item.get("start"),
        "end": item.get("end"),
        "start_s": item.get("start_s"),
        "end_s": item.get("end_s"),
    }]


def validate(data: dict[str, Any], json_path: Path, output_path: Path, allow_legacy: bool = False) -> None:
    for key in ("report", "sources", "exercises", "timeline", "issues"):
        require(key in data, f"missing top-level field '{key}'")
    require(isinstance(data["report"], dict), "report must be an object")
    require(valid_report_date(data["report"].get("date")), "report.date must be a real calendar date in YYYY-MM-DD format")
    require(isinstance(data["sources"], list) and data["sources"], "sources must be a non-empty list")
    require(isinstance(data["exercises"], list) and data["exercises"], "exercises must be a non-empty list")
    require(isinstance(data["timeline"], list) and data["timeline"], "timeline must be a non-empty list")
    try:
        validate_report_evidence(data, json_path, allow_legacy=allow_legacy)
    except (ValueError, KeyError, OSError, TypeError) as exc:
        require(False, f"count evidence verification failed: {exc}")
    exercise_names: set[str] = set()
    exercise_summary: dict[str, tuple[int, int]] = {}
    for index, item in enumerate(data["exercises"], 1):
        name = str(item.get("name") or "")
        require(bool(name), f"exercise {index} is missing name")
        require(name not in exercise_names, f"duplicate exercise summary '{name}'")
        exercise_names.add(name)
        reps = item.get("reps")
        require(isinstance(reps, list) and all(isinstance(x, int) and x >= 0 for x in reps), f"exercise {index} has invalid reps")
        require(item.get("sets") == len(reps), f"exercise '{name}' sets does not match reps")
        require(item.get("total") == sum(reps), f"exercise '{name}' total does not match reps")
        exercise_summary[name] = (len(reps), sum(reps))
        video_id = str(item.get("video_id") or "")
        require(not video_id or bool(VIDEO_ID.fullmatch(video_id)), f"exercise {index} has invalid video_id")
    source_names: set[str] = set()
    for index, source in enumerate(data["sources"], 1):
        source_name = str(source.get("file") or "")
        require(bool(source_name), f"source {index} is missing file label")
        require(source_name not in source_names, f"duplicate source label '{source_name}'")
        source_names.add(source_name)
        require(float(source.get("duration_s") or 0) > 0, f"source '{source_name}' has invalid duration")
    timeline_reps: dict[str, list[int]] = defaultdict(list)
    timeline_sets: dict[str, list[int]] = defaultdict(list)
    for index, item in enumerate(data["timeline"], 1):
        require(str(item.get("file")) in source_names, f"timeline row {index} references an unknown source")
        exercise_name = str(item.get("exercise") or "")
        require(exercise_name in exercise_names, f"timeline row {index} references unknown exercise '{exercise_name}'")
        require(float(item.get("end_s", -1)) > float(item.get("start_s", -1)) >= 0, f"timeline row {index} has invalid numeric times")
        segments = timeline_segments(item)
        require(isinstance(segments, list) and segments, f"timeline row {index} has no source segments")
        for segment_index, segment in enumerate(segments, 1):
            source_name = str(segment.get("file") or "")
            require(source_name in source_names, f"timeline row {index} segment {segment_index} references an unknown source")
            require(float(segment.get("end_s", -1)) > float(segment.get("start_s", -1)) >= 0, f"timeline row {index} segment {segment_index} has invalid numeric times")
        first = segments[0]
        require(str(first.get("file")) == str(item.get("file")), f"timeline row {index} legacy file must match first segment")
        completed = item.get("completed")
        require(isinstance(completed, int) and completed >= 0, f"timeline row {index} has invalid completed reps")
        set_index = item.get("set")
        require(isinstance(set_index, int) and set_index >= 1, f"timeline row {index} has invalid set number")
        timeline_reps[exercise_name].append(completed)
        timeline_sets[exercise_name].append(set_index)
    timeline_summary = {name: (len(values), sum(values)) for name, values in timeline_reps.items()}
    require(timeline_summary == exercise_summary, f"exercise summary does not match timeline: {exercise_summary!r} != {timeline_summary!r}")
    for name, set_numbers in timeline_sets.items():
        require(sorted(set_numbers) == list(range(1, len(set_numbers) + 1)), f"timeline set numbers are not contiguous for '{name}'")
    for index, issue in enumerate(data["issues"], 1):
        for key in ("timestamp", "exercise", "observation", "scan_range", "correction", "image"):
            require(bool(issue.get(key)), f"issue {index} is missing '{key}'")
        if int(data.get("schema_version") or 0) >= 4:
            gate = issue.get("problem_gate") or {}
            require(gate.get("basis") in ("visible_technique_failure", "user_reported_symptom"),
                    f"issue {index} lacks a valid problem basis")
            require(bool(gate.get("criterion")) and bool(gate.get("observed_violation")),
                    f"issue {index} lacks a problem criterion or observed violation")
            if gate["basis"] == "visible_technique_failure":
                require(bool(gate.get("observed_effect")),
                        f"issue {index} lacks the visible movement effect; an environment feature alone is not a problem")
            else:
                require(bool(gate.get("user_report_reference")),
                        f"issue {index} lacks the user-reported symptom reference")
        issue["_image_url"] = local_url(str(issue["image"]), json_path.parent, output_path.parent, must_exist=True)
        gif = str(issue.get("gif") or "")
        issue["_media_url"] = local_url(gif, json_path.parent, output_path.parent, must_exist=True) if gif else issue["_image_url"]
        issue["_media_kind"] = "gif" if gif else "image"
        video_id = str(issue.get("video_id") or "")
        require(not video_id or bool(VIDEO_ID.fullmatch(video_id)), f"issue {index} has invalid video_id")


def render_links(item: dict[str, Any], issue: bool = False) -> str:
    links = []
    if item.get("guide_url"):
        links.append(f'<a href="{esc(item["guide_url"])}" target="_blank" rel="noopener noreferrer">本地图文</a>')
    video_id = str(item.get("video_id") or "")
    if video_id:
        links.append(f'<button class="video-button" type="button" data-video-id="{esc(video_id)}">▶ 卡片内播放</button>')
        links.append(f'<a class="correct-video" href="https://www.youtube.com/watch?v={esc(video_id)}" target="_blank" rel="noopener noreferrer">↗ 新标签打开</a>')
    if item.get("video_url"):
        links.append(f'<a href="{esc(item["video_url"])}" target="_blank" rel="noopener noreferrer">更多视频</a>')
    if issue and not links:
        links.append('<span class="muted">尚未匹配教程入口</span>')
    return '<div class="link-row">' + "".join(links) + "</div>"


def index_entry(data: dict[str, Any], html_name: str) -> Optional[dict[str, Any]]:
    report = data.get("report")
    exercises = data.get("exercises")
    if not isinstance(report, dict) or not isinstance(exercises, list) or not valid_report_date(report.get("date")):
        return None
    exercise_items = []
    for item in exercises:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        exercise_items.append({
            "name": str(item["name"]),
            "sets": int(item.get("sets") or 0),
            "total": int(item.get("total") or 0),
        })
    duration_s = 0.0
    for source in data.get("sources", []):
        if isinstance(source, dict):
            try:
                duration_s += float(source.get("duration_s") or 0)
            except (TypeError, ValueError):
                pass
    return {
        "date": str(report["date"]),
        "title": str(report.get("title") or f'{report["date"]} 训练视频分析'),
        "session_label": str(report.get("session_label") or "训练复盘"),
        "summary": str(report.get("summary") or ""),
        "confidence": str(report.get("overall_confidence") or "medium"),
        "url": html_name,
        "total_sets": sum(item["sets"] for item in exercise_items),
        "total_reps": sum(item["total"] for item in exercise_items),
        "issue_count": len(data.get("issues", [])) if isinstance(data.get("issues"), list) else 0,
        "duration_s": round(duration_s, 3),
        "exercises": exercise_items,
    }


def write_report_index(report_dir: Path, current_data: dict[str, Any], current_html_name: str) -> Path:
    entries: dict[str, dict[str, Any]] = {}
    for json_path in sorted(report_dir.glob("*.json")):
        if json_path.name == "report-index.json":
            continue
        html_path = json_path.with_suffix(".html")
        if not html_path.is_file():
            continue
        try:
            candidate = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        entry = index_entry(candidate, html_path.name)
        if entry:
            entries[entry["url"]] = entry
    current_entry = index_entry(current_data, current_html_name)
    if current_entry:
        entries[current_html_name] = current_entry
    records = sorted(entries.values(), key=lambda item: (item["date"], item["title"], item["url"]), reverse=True)
    index_path = report_dir / "report-index.json"
    index_path.write_text(json.dumps({"version": 1, "records": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return index_path


def render(data: dict[str, Any], output_path: Path) -> str:
    report = data["report"]
    exercises = data["exercises"]
    timeline = data["timeline"]
    issues = data["issues"]
    exercise_colors = {item["name"]: COLORS[i % len(COLORS)] for i, item in enumerate(exercises)}
    timeline_legend = "".join(
        f'<span class="legend-item"><i style="background:{exercise_colors[item["name"]]}"></i>{esc(item["name"])}</span>'
        for item in exercises
    )
    total_sets = sum(item["sets"] for item in exercises)
    total_reps = sum(item["total"] for item in exercises)
    overall_cls, overall_text = confidence(report.get("overall_confidence"))

    exercise_cards = []
    summary_rows = []
    for item in exercises:
        cls, conf = confidence(item.get("confidence"))
        reps = " / ".join(str(x) for x in item["reps"])
        exercise_cards.append(f'''<article class="exercise-card" style="--accent:{exercise_colors[item['name']]}">
          <div><span class="eyebrow">{esc(item['sets'])} 组</span><h3>{esc(item['name'])}</h3></div>
          <strong class="big-number">{esc(item['total'])}</strong>
          <p>每组：{esc(reps)}</p><span class="confidence {cls}">{esc(conf)}置信度</span>
          {render_links(item)}
          <div class="player-slot"></div>
        </article>''')
        summary_rows.append(f'<tr><td>{esc(item["name"])}</td><td>{item["sets"]}</td><td>{esc(reps)}</td><td><strong>{item["total"]}</strong></td><td><span class="confidence {cls}">{esc(conf)}</span></td></tr>')

    sources_by_name = {str(x["file"]): x for x in data["sources"]}
    timeline_groups = []
    timeline_rows = []
    grouped: dict[str, list[tuple[dict[str, Any], dict[str, Any], int]]] = defaultdict(list)
    for item in timeline:
        segments = timeline_segments(item)
        for segment_index, segment in enumerate(segments):
            grouped[str(segment["file"])].append((item, segment, segment_index))
        cls, conf = confidence(item.get("confidence"))
        source_cell = "<br>".join(esc(segment["file"]) for segment in segments)
        time_cell = "<br>".join(f'{esc(segment.get("start", ""))}–{esc(segment.get("end", ""))}' for segment in segments)
        timeline_rows.append(f'''<tr><td>{source_cell}</td><td>{time_cell}</td><td>{esc(item['exercise'])}</td><td>{esc(item['set'])}</td><td><strong>{esc(item['completed'])}</strong></td><td>{esc(item.get('partial', 0))}</td><td><span class="confidence {cls}">{esc(conf)}</span></td><td>{esc(item.get('note', ''))}</td></tr>''')
    for file_name, items in grouped.items():
        duration = float(sources_by_name[file_name]["duration_s"])
        blocks = []
        for item, segment, segment_index in items:
            left = max(0.0, min(100.0, float(segment["start_s"]) / duration * 100))
            width = max(0.7, min(100 - left, (float(segment["end_s"]) - float(segment["start_s"])) / duration * 100))
            continuation = " · 续" if segment_index else ""
            label = f'{item["exercise"]} · {item["completed"]}{continuation}'
            title = f'{segment.get("start", "")}–{segment.get("end", "")} | {item["exercise"]} 第{item["set"]}组{continuation} | {item["completed"]}次（整组）'
            blocks.append(f'<div class="timeline-block" style="left:{left:.4f}%;width:{width:.4f}%;background:{exercise_colors.get(item["exercise"], COLORS[0])}" title="{esc(title)}"><span>{esc(label)}</span></div>')
        ticks = "".join(f'<span style="left:{pct}%">{int(duration * pct / 100 // 60):02d}:{int(duration * pct / 100 % 60):02d}</span>' for pct in (0, 25, 50, 75, 100))
        timeline_groups.append(f'''<article class="timeline-group"><div class="timeline-title"><strong>{esc(file_name)}</strong><span>{duration/60:.1f} 分钟</span></div><div class="timeline-track">{''.join(blocks)}</div><div class="timeline-ticks">{ticks}</div></article>''')

    issue_cards = []
    for index, issue in enumerate(issues, 1):
        cls, conf = confidence(issue.get("confidence"))
        issue_cards.append(f'''<article class="issue-card">
          <figure><img src="{esc(issue['_media_url'])}" alt="{esc(issue['exercise'])} {esc(issue['timestamp'])} 问题动作{'GIF' if issue['_media_kind'] == 'gif' else '截图'}" loading="lazy"><figcaption>{esc(issue['file'])} · {esc(issue['timestamp'])}{' · GIF' if issue['_media_kind'] == 'gif' else ''}</figcaption></figure>
          <div class="issue-body"><div class="issue-head"><span class="issue-number">问题 {index}</span><span class="confidence {cls}">{esc(conf)}置信度</span></div>
          <h3>{esc(issue['exercise'])} · 第{esc(issue.get('set', '?'))}组 · {esc(issue.get('phase', ''))}</h3>
          <dl><div><dt>看到了什么</dt><dd>{esc(issue['observation'])}</dd></div><div><dt>确认范围</dt><dd>{esc(issue['scan_range'])}</dd></div><div class="correction"><dt>怎么改</dt><dd>{esc(issue['correction'])}</dd></div></dl>
          {render_links(issue, issue=True)}<div class="player-slot"></div></div>
        </article>''')
    if not issue_cards:
        issue_cards.append('<div class="empty-state">本次没有确认需要留存证据的问题动作。</div>')

    source_rows = "".join(f'<tr><td>{esc(x["file"])}</td><td class="path">{esc(x["path"])}</td><td>{float(x["duration_s"])/60:.1f} 分钟</td></tr>' for x in data["sources"])
    next_items = "".join(f'<li>{esc(x)}</li>' for x in data.get("next_session", [])) or '<li>保持当前机位并继续记录 RIR。</li>'
    cost = data.get("cost", {})
    cost_cards = "".join(f'<div class="cost-card"><strong>{esc(k.replace("_", " "))}</strong><span>{esc(v)}</span></div>' for k, v in cost.items()) or '<div class="cost-card"><span>本次未记录分析成本。</span></div>'
    manifest = data.get("analysis_manifest") or {}
    manifest_labels = {
        "generated_contact_sheets": "生成接触表",
        "model_visible_contact_sheets": "模型实际查看",
        "model_visible_frames": "实际展示帧数",
        "model_visible_pixels": "实际展示像素",
        "dense_windows": "密扫窗口",
        "decode_attempts": "解码尝试",
        "failed_extra_methods": "失败的额外方法",
        "max_model_visible_contact_sheets": "当前硬上限",
    }
    manifest_cards = "".join(
        f'<div class="cost-card"><strong>{esc(label)}</strong><span>{esc(manifest.get(key, "未记录"))}</span></div>'
        for key, label in manifest_labels.items()
    ) if manifest else '<div class="cost-card"><span>本次没有结构化分析清单。</span></div>'
    manifest_note = f'<p>{esc(manifest.get("limit_status", ""))}</p>' if manifest.get("limit_status") else ""
    manifest_note += f'<p>{esc(data.get("_verification_notice", ""))}</p>'

    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><base target="_blank"><title>{esc(report.get('title', '训练视频分析报告'))}</title>
<style>
:root{{--bg:#f6f4ef;--ink:#18201d;--muted:#59635d;--line:#d8d2c7;--panel:#fff;--green:#2f6b4f;--green-soft:#e5f0e9;--red:#9a4637;--red-soft:#f3e4df;--blue:#315d7d;--blue-soft:#e4edf4;--amber:#8d6c20;--amber-soft:#f2ead4;--shadow:0 10px 28px rgba(24,32,29,.08)}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans SC",sans-serif;line-height:1.55}}a{{color:var(--blue);font-weight:800;text-decoration:none}}a:hover{{text-decoration:underline}}.shell{{width:min(1220px,calc(100% - 32px));margin:auto;padding:28px 0 48px}}header,section{{background:var(--panel);border:1px solid var(--line);border-radius:10px;box-shadow:var(--shadow)}}header{{padding:26px;display:grid;grid-template-columns:1.3fr .7fr;gap:22px}}section{{padding:22px;margin-top:18px}}h1,h2,h3{{margin:0;line-height:1.2}}h1{{font-size:32px}}h2{{font-size:23px;margin-bottom:14px}}h3{{font-size:17px}}p{{margin:8px 0 0;color:var(--muted)}}.back-link{{display:inline-flex;margin-bottom:10px;color:var(--green);font-size:14px}}.eyebrow{{font-size:12px;font-weight:900;text-transform:uppercase;color:var(--muted)}}.hero-stats{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}}.stat{{border:1px solid var(--line);border-radius:9px;padding:14px;background:#fff}}.stat strong{{display:block;font-size:28px}}.stat span{{color:var(--muted);font-size:13px}}.confidence{{display:inline-flex;border-radius:999px;padding:3px 8px;font-size:12px;font-weight:900}}.confidence.high{{color:var(--green);background:var(--green-soft)}}.confidence.medium{{color:var(--amber);background:var(--amber-soft)}}.confidence.low{{color:var(--red);background:var(--red-soft)}}.exercise-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.exercise-card{{border:1px solid var(--line);border-top:5px solid var(--accent);border-radius:9px;padding:14px;display:flex;flex-direction:column;gap:9px}}.exercise-card .big-number{{font-size:28px}}.link-row{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:12px}}.link-row a,.video-button{{border:1px solid rgba(49,93,125,.28);border-radius:7px;padding:7px 9px;background:#fff;color:var(--blue);font:inherit;font-weight:850;text-decoration:none;cursor:pointer}}.link-row a:hover,.video-button:hover{{background:var(--blue-soft);text-decoration:none}}.video-button{{border-color:rgba(47,107,79,.45);color:#fff;background:var(--green)}}.video-button:hover{{background:#285d44}}.correct-video{{border-color:rgba(47,107,79,.45)!important;color:var(--green)!important;background:var(--green-soft)!important}}.player-slot{{margin-top:4px}}.inline-player{{width:100%;aspect-ratio:16/9;border:0;border-radius:8px;background:#111;display:block}}.timeline-legend{{display:flex;flex-wrap:wrap;gap:8px 14px;margin:14px 0 4px;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:#faf9f6}}.legend-item{{display:inline-flex;align-items:center;gap:7px;color:var(--ink);font-size:13px;font-weight:850}}.legend-item i{{display:block;width:13px;height:13px;border-radius:3px;box-shadow:inset 0 0 0 1px rgba(0,0,0,.12)}}.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:9px}}table{{width:100%;border-collapse:collapse;min-width:780px}}th,td{{padding:10px 11px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{background:#f7f5f0;font-size:13px}}tr:last-child td{{border-bottom:0}}.timeline-group{{margin:16px 0}}.timeline-title{{display:flex;justify-content:space-between;color:var(--muted);margin-bottom:6px}}.timeline-track{{position:relative;height:56px;border-radius:8px;background:repeating-linear-gradient(90deg,#f5f2ec 0,#f5f2ec calc(25% - 1px),#d8d2c7 25%);overflow:hidden}}.timeline-block{{position:absolute;top:7px;height:42px;border-radius:6px;min-width:7px;color:#fff;box-shadow:0 2px 6px rgba(0,0,0,.16);overflow:hidden}}.timeline-block span{{display:block;padding:10px 7px;font-size:12px;font-weight:900;white-space:nowrap}}.timeline-ticks{{position:relative;height:22px;color:var(--muted);font-size:11px}}.timeline-ticks span{{position:absolute;transform:translateX(-50%)}}.issue-list{{display:grid;gap:16px}}.issue-card{{display:grid;grid-template-columns:minmax(280px,.8fr) minmax(0,1.2fr);border:1px solid var(--line);border-radius:10px;overflow:hidden}}figure{{margin:0;background:#171a18;position:relative;min-height:280px}}figure img{{width:100%;height:100%;object-fit:contain;display:block}}figcaption{{position:absolute;left:10px;bottom:10px;background:rgba(0,0,0,.76);color:#fff;border-radius:6px;padding:5px 8px;font-weight:800;font-size:12px}}.issue-body{{padding:18px}}.issue-head{{display:flex;justify-content:space-between;gap:8px;margin-bottom:10px}}.issue-number{{color:var(--red);font-weight:900}}dl{{display:grid;gap:10px;margin:16px 0 0}}dt{{font-size:13px;font-weight:900}}dd{{margin:3px 0 0;color:var(--muted)}}dl>div{{border-left:4px solid var(--line);padding-left:10px}}dl .correction{{border-color:var(--green);background:var(--green-soft);padding:10px;border-radius:0 8px 8px 0}}dl .correction dd{{color:var(--green);font-weight:800}}.next-list{{margin:0;padding-left:20px}}.cost-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}.cost-card{{border:1px solid var(--line);border-radius:8px;padding:12px;display:grid;gap:5px}}.cost-card strong{{font-size:12px;color:var(--muted)}}.path{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;word-break:break-all}}.muted{{color:var(--muted)}}.empty-state{{padding:20px;border:1px dashed var(--line);border-radius:9px;color:var(--muted)}}footer{{color:var(--muted);padding:18px 4px;text-align:center;font-size:13px}}
@media(max-width:900px){{header{{grid-template-columns:1fr}}.exercise-grid{{grid-template-columns:repeat(2,1fr)}}.issue-card{{grid-template-columns:1fr}}.cost-grid{{grid-template-columns:repeat(2,1fr)}}figure{{min-height:240px}}}}@media(max-width:560px){{.shell{{width:calc(100% - 18px);padding-top:10px}}header,section{{padding:16px}}h1{{font-size:25px}}.exercise-grid,.cost-grid{{grid-template-columns:1fr}}.timeline-track{{height:48px}}.timeline-block{{height:34px}}.timeline-block span{{padding:7px 5px;font-size:10px}}}}
</style></head><body><main class="shell">
<header><div><a class="back-link" href="../dashboard.html#training-records" target="_self">← 返回虚拟健身教练</a><span class="eyebrow">{esc(report.get('date',''))} · {esc(report.get('session_label','训练复盘'))}</span><h1>{esc(report.get('title','训练视频分析报告'))}</h1><p>{esc(report.get('summary',''))}</p><p>{esc(data.get('_verification_notice',''))}</p></div><div class="hero-stats"><div class="stat"><strong>{total_sets}</strong><span>总组数</span></div><div class="stat"><strong>{total_reps}</strong><span>{'已确认次数下限' if data.get('_incomplete_counts') else '完整次数'}</span></div><div class="stat"><strong>{len(issues)}</strong><span>确认问题点</span></div><div class="stat"><strong>{esc(overall_text)}</strong><span>总体置信度</span></div></div></header>
<section><h2>动作与组次汇总</h2><div class="exercise-grid">{''.join(exercise_cards)}</div><div class="table-wrap" style="margin-top:16px"><table><thead><tr><th>动作</th><th>组数</th><th>每组次数</th><th>总次数</th><th>置信度</th></tr></thead><tbody>{''.join(summary_rows)}</tbody></table></div></section>
<section><h2>训练时间轴</h2><p>{esc(report.get('timing_note',''))}</p><div class="timeline-legend" aria-label="时间轴颜色与动作对照">{timeline_legend}</div>{''.join(timeline_groups)}<div class="table-wrap"><table><thead><tr><th>文件</th><th>时间</th><th>动作</th><th>组</th><th>完整</th><th>半次</th><th>置信度</th><th>证据</th></tr></thead><tbody>{''.join(timeline_rows)}</tbody></table></div></section>
<section><h2>问题动作与改正方式</h2><p>每个问题均保留源视频短 GIF 或截图、时间点、确认范围和对应正确动作入口。</p><div class="issue-list">{''.join(issue_cards)}</div></section>
<section><h2>下次训练</h2><ul class="next-list">{next_items}</ul></section>
<section><h2>来源与分析成本</h2><div class="table-wrap"><table><thead><tr><th>文件</th><th>原始路径</th><th>时长</th></tr></thead><tbody>{source_rows}</tbody></table></div><div class="cost-grid" style="margin-top:14px">{cost_cards}</div><h3 style="margin-top:18px">实际分析清单</h3>{manifest_note}<div class="cost-grid" style="margin-top:10px">{manifest_cards}</div></section>
<footer>本报告由 analyze-workout-video 生成；原视频未复制进项目，只保留必要问题 GIF、证据截图和分析记录。</footer></main>
<script>
let activePlayer = null;
let activeButton = null;
document.querySelectorAll('.video-button').forEach((button) => {{
  button.addEventListener('click', () => {{
    const videoId = button.dataset.videoId;
    if (location.protocol === 'file:') {{
      window.open(`https://www.youtube.com/watch?v=${{videoId}}`, '_blank', 'noopener');
      return;
    }}
    if (activeButton === button) {{
      activePlayer.remove();
      activePlayer = null;
      activeButton.textContent = '▶ 卡片内播放';
      activeButton = null;
      return;
    }}
    if (activePlayer) activePlayer.remove();
    if (activeButton) activeButton.textContent = '▶ 卡片内播放';
    const slot = button.closest('.exercise-card, .issue-body').querySelector('.player-slot');
    const iframe = document.createElement('iframe');
    iframe.className = 'inline-player';
    iframe.src = `https://www.youtube-nocookie.com/embed/${{videoId}}?autoplay=1&rel=0`;
    iframe.title = '正确动作教学视频';
    iframe.allow = 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share';
    iframe.allowFullscreen = true;
    slot.replaceChildren(iframe);
    activePlayer = iframe;
    activeButton = button;
    button.textContent = '■ 收起视频';
  }});
}});
</script></body></html>'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="UTF-8 report JSON")
    parser.add_argument("output", type=Path, help="output HTML")
    parser.add_argument("--allow-legacy", action="store_true", help="explicit historical schema-v2 rendering with an unverified-history notice")
    args = parser.parse_args()
    require(args.input.is_file(), f"input JSON not found: {args.input}")
    data = json.loads(args.input.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.parent.name == "form-reviews":
        require(args.input.stem == args.output.stem, "form-reviews report JSON and HTML must use the same filename stem")
    validate(data, args.input.resolve(), args.output.resolve(), allow_legacy=args.allow_legacy)
    args.output.write_text(render(data, args.output.resolve()), encoding="utf-8")
    if args.output.parent.name == "form-reviews":
        write_report_index(args.output.parent.resolve(), data, args.output.name)
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
