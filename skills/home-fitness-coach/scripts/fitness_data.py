#!/usr/bin/env python3
"""Build and audit durable fitness data without model calls.

The per-session JSON files under data/sessions are the durable source of truth.
Dashboard indexes, coach snapshots, CSV exports, and HTML reports are derived.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from statistics import mean
from typing import Any


SCHEMA_VERSION = 2
PIPELINE_VERSION = "2.3.0"

# Allow the standalone script and importlib-based project tests to share grouping.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from training_pages import collect as collect_workouts, training_groups, render as render_training_pages
import laterality


def session_duration(data: dict[str, Any]) -> float:
    """Keep source clocks intact while honoring an explicitly selected review scope."""
    sources = {item["file"]: float(item.get("duration_s") or 0) for item in data.get("sources") or []}
    segments = (data.get("analysis_scope") or {}).get("segments")
    if not segments:
        return round(sum(sources.values()), 3)
    total = 0.0
    previous_ends: dict[str, float] = {}
    for segment in segments:
        file = segment["file"]
        start, end = float(segment["start_s"]), float(segment["end_s"])
        if (file not in sources or not math.isfinite(start + end)
                or not 0 <= start < end <= sources[file] + .001
                or start < previous_ends.get(file, 0)):
            raise ValueError("invalid or overlapping analysis scope")
        total += end - start
        previous_ends[file] = end
    return round(total, 3)


def validate_new_evidence(data: dict[str, Any], path: Path, project: Path | None = None) -> None:
    """Historical sessions stay readable; new reports must pass the evidence gate."""
    project = project or (path.parent.parent if path.parent.name == 'form-reviews' else path.parent)
    laterality.validate_report(project, data, path)
    if data.get("schema_version", 2) < 3 and not data.get("evidence_manifest"):
        return
    skill = __import__("skill_paths").video_skill()
    sys.path.insert(0, str(skill / "scripts"))
    try:
        from count_evidence import validate_report_evidence
        validate_report_evidence(data, path)
    finally:
        sys.path.pop(0)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def catalog_maps(project: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str], dict[str, dict[str, Any]]]:
    exercise_data = load_json(project / "data/catalogs/exercises.json")
    issue_data = load_json(project / "data/catalogs/issues.json")
    exercises = {item["id"]: item for item in exercise_data.get("exercises", [])}
    aliases: dict[str, str] = {}
    for item in exercises.values():
        for alias in [item.get("name"), *(item.get("aliases") or [])]:
            if alias:
                aliases[str(alias).strip()] = item["id"]
    issues = {item["id"]: item for item in issue_data.get("issues", [])}
    return exercises, aliases, issues


def fast_fingerprint(path: Path) -> str | None:
    if not path.is_file():
        return None
    size = path.stat().st_size
    digest = hashlib.sha256()
    digest.update(str(size).encode("ascii"))
    with path.open("rb") as handle:
        digest.update(handle.read(1024 * 1024))
        if size > 1024 * 1024:
            handle.seek(max(0, size - 1024 * 1024))
            digest.update(handle.read(1024 * 1024))
    return f"sha256-fast:{digest.hexdigest()}"


def infer_issue_ids(exercise_id: str, text: str) -> list[str]:
    result: list[str] = []
    if exercise_id in {"pushup.standard", "pushup.knee"}:
        if re.search(r"头颈|头部|脸先|下巴", text):
            result.append("pushup.neck_reach")
        if re.search(r"塌腰|腰髋|髋部略低|骨盆.*下", text):
            result.append("pushup.hip_sag")
    elif exercise_id == "pushup.pike":
        if re.search(r"髋位|水平|躯干角度", text):
            result.append("pike.hip_drop")
    elif exercise_id == "split_squat.bulgarian":
        if re.search(r"骨盆|横移|侧倾|侧移|转动|找平衡", text):
            result.append("split_squat.pelvic_shift")
        if re.search(r"后脚|床沿|椅面|支撑|毛巾|借力", text):
            result.append("split_squat.rear_support")
    elif exercise_id == "row.backpack":
        if re.search(r"速度|惯性|摆动|29|30|31|32", text):
            result.append("row.excessive_speed")
        if re.search(r"耸肩|肩膀|躯干|抬头|低头|颈部", text):
            result.append("row.shrug_trunk_motion")
    elif exercise_id == "hip_abduction.side_lying":
        if re.search(r"过高|高度|60\s*度|抬得明显高", text):
            result.append("hip_abduction.excessive_range")
        if re.search(r"骨盆|后滚|后翻|脚尖.*上", text):
            result.append("hip_abduction.pelvis_roll")
    elif exercise_id == "bridge.single_leg":
        if "悬空腿" in text:
            result.append("bridge.free_leg_motion")
        if re.search(r"下背|腰|肋骨", text):
            result.append("bridge.lumbar_extension")
    elif exercise_id == "hip_extension.quadruped":
        if re.search(r"骨盆|腰椎|腰开始塌", text):
            result.append("hip_extension.pelvic_rotation")
    return list(dict.fromkeys(result))


def session_filename(report_date: str, ordinal: int) -> str:
    return f"{report_date}-{ordinal:03d}.json"


def migrate_report(
    project: Path,
    report_path: Path,
    output_path: Path,
    session_id: str,
    exercises_by_id: dict[str, dict[str, Any]],
    alias_to_id: dict[str, str],
    issues_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    legacy = load_json(report_path)
    side_review_required = laterality.validate_report(project, legacy, report_path)
    if legacy.get("schema_version", 2) >= 3 or legacy.get("evidence_manifest"):
        errors = validate_legacy_report(report_path)
        if errors:
            raise ValueError("; ".join(errors))
        validate_new_evidence(legacy, report_path)
    report = legacy.get("report") or {}
    timeline = legacy.get("timeline") or []
    timeline_by_exercise: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in timeline:
        timeline_by_exercise[str(row.get("exercise") or "")].append(row)

    canonical_exercises: list[dict[str, Any]] = []
    reported_to_id: dict[str, str] = {}
    for item in legacy.get("exercises") or []:
        reported_name = str(item.get("name") or "").strip()
        exercise_id = item.get("exercise_id") or alias_to_id.get(reported_name)
        if not exercise_id:
            exercise_id = f"unmapped.{re.sub(r'[^a-z0-9]+', '-', reported_name.lower()).strip('-') or 'exercise'}"
        reported_to_id[reported_name] = exercise_id
        catalog = exercises_by_id.get(exercise_id, {})
        rows = sorted(timeline_by_exercise.get(reported_name, []), key=lambda row: (str(row.get("file")), float(row.get("start_s") or 0)))
        sets: list[dict[str, Any]] = []
        for index, row in enumerate(rows, 1):
            source_segments = row.get("segments") or [{
                "file": str(row.get("file") or ""),
                "start": str(row.get("start") or ""),
                "end": str(row.get("end") or ""),
                "start_s": float(row.get("start_s") or 0),
                "end_s": float(row.get("end_s") or 0),
            }]
            sets.append({
                "set_index": int(row.get("set") or index),
                "side": row.get("side") or ("per_side" if row.get("reps_per_side") else ("unknown" if catalog.get("unilateral") else "bilateral")),
                **{key: copy.deepcopy(row[key]) for key in laterality.SIDE_FIELDS[1:] if key in row},
                "completed_reps": int(row.get("completed") or 0),
                "reps_per_side": row.get("reps_per_side"),
                "execution_mode": row.get("execution_mode"),
                "partial_reps": int(row.get("partial") or 0),
                "uncertain_reps": int(row.get("uncertain") or 0),
                "source_file": str(row.get("file") or ""),
                "source_segments": source_segments,
                "start": str(row.get("start") or ""),
                "end": str(row.get("end") or ""),
                "start_s": float(row.get("start_s") or 0),
                "end_s": float(row.get("end_s") or 0),
                "stitched_start_s": row.get("stitched_start_s"),
                "stitched_end_s": row.get("stitched_end_s"),
                "confidence": str(row.get("confidence") or item.get("confidence") or "medium"),
                "load": {"value": None, "unit": "kg", "label": None},
                "tempo": None,
                "rir": None,
                "note": str(row.get("note") or ""),
                **({"evidence_set_id": row["evidence_set_id"],
                    "coverage_complete": row["coverage_complete"]} if row.get("evidence_set_id") else {}),
            })
        if not sets:
            for index, reps in enumerate(item.get("reps") or [], 1):
                sets.append({
                    "set_index": index,
                    "side": "unknown" if catalog.get("unilateral") else "bilateral",
                    "completed_reps": int(reps),
                    "partial_reps": 0,
                    "uncertain_reps": 0,
                    "source_file": None,
                    "start": None,
                    "end": None,
                    "start_s": None,
                    "end_s": None,
                    "confidence": str(item.get("confidence") or "medium"),
                    "load": {"value": None, "unit": "kg", "label": None},
                    "tempo": None,
                    "rir": None,
                    "note": "legacy summary without timeline row",
                })
        canonical_exercises.append({
            "exercise_id": exercise_id,
            "name": str(catalog.get("name") or reported_name),
            "reported_name": reported_name,
            "family": str(catalog.get("family") or "unmapped"),
            "variant": exercise_id.split(".", 1)[1] if "." in exercise_id else "unknown",
            "unilateral": bool(catalog.get("unilateral")),
            "confidence": str(item.get("confidence") or "medium"),
            "sets": sets,
            "total_sets": len(sets),
            "total_completed_reps": sum(row["completed_reps"] for row in sets),
        })

    def project_asset(value: Any) -> str:
        if not value:
            return ""
        asset = Path(str(value))
        if not asset.is_absolute():
            asset = project / asset if asset.parts[0] == 'review-assets' else report_path.parent / asset
        return os.path.relpath(asset.resolve(), project)

    canonical_issues: list[dict[str, Any]] = []
    for source_index, item in enumerate(legacy.get("issues") or [], 1):
        reported_name = str(item.get("exercise") or "")
        exercise_id = reported_to_id.get(reported_name) or alias_to_id.get(reported_name) or "unmapped.exercise"
        explicit_issue_id = str(item.get("issue_id") or "").strip()
        combined = " ".join(str(item.get(key) or "") for key in ("observation", "correction", "phase"))
        if explicit_issue_id in issues_by_id or explicit_issue_id.startswith("unmapped."):
            issue_ids = [explicit_issue_id]
        else:
            issue_ids = infer_issue_ids(exercise_id, combined) or ["unmapped.technique"]
        for issue_id in issue_ids:
            catalog_issue = issues_by_id.get(issue_id, {})
            canonical_issues.append({
                "issue_id": issue_id,
                "name": str(catalog_issue.get("name") or item.get("observation") or "未归类技术问题"),
                "exercise_id": exercise_id,
                "source_issue_index": source_index,
                "source_file": str(item.get("file") or ""),
                "timestamp": str(item.get("timestamp") or ""),
                "timestamp_s": float(item.get("timestamp_s") or 0),
                "set_index": item.get("set"),
                "side": item.get("side"),
                "phase": str(item.get("phase") or ""),
                "observation": str(item.get("observation") or ""),
                "scan_range": str(item.get("scan_range") or ""),
                "confidence": str(item.get("confidence") or "medium"),
                "cue": str(catalog_issue.get("cue") or item.get("correction") or ""),
                "stop_rule": str(catalog_issue.get("stop_rule") or ""),
                "legacy_correction": str(item.get("correction") or ""),
                "image": project_asset(item.get("image")),
                "gif": project_asset(item.get("gif")) or None,
                "status": "confirmed",
            })

    canonical_technique_focuses: list[dict[str, Any]] = []
    for item in legacy.get("technique_focuses") or []:
        focus = dict(item)
        for asset_key in ("image", "gif", "gif_set_1", "gif_set_2"):
            if focus.get(asset_key):
                focus[asset_key] = project_asset(focus[asset_key])
        canonical_technique_focuses.append(focus)

    sources: list[dict[str, Any]] = []
    for source in legacy.get("sources") or []:
        source_path = Path(str(source.get("path") or ""))
        exists = source_path.is_file()
        sources.append({
            "file": str(source.get("file") or ""),
            "original_path": str(source_path),
            "duration_s": float(source.get("duration_s") or 0),
            "exists_at_import": exists,
            "size_bytes_at_import": source_path.stat().st_size if exists else None,
            "fingerprint": source.get("fingerprint") or (fast_fingerprint(source_path) if exists else None),
        })

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    result = {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "revision": 1,
        "date": str(report.get("date") or ""),
        "timezone": (legacy.get("report") or {}).get("timezone"),
        "status": "review_pending" if legacy.get("_incomplete_counts") else "confirmed",
        "session_label": str(report.get("session_label") or "训练复盘"),
        "title": str(report.get("title") or ""),
        "summary": str(report.get("summary") or ""),
        "overall_confidence": str(report.get("overall_confidence") or "medium"),
        "timing_note": str(report.get("timing_note") or ""),
        "plan": {"plan_id": "home-full-body-ab-v1", "execution": "unclassified"},
        **({"workout_id": legacy.get("workout_id") or report.get("workout_id")} if legacy.get("workout_id") or report.get("workout_id") else {}),
        "sources": sources,
        "exercises": canonical_exercises,
        "issues": canonical_issues,
        "technique_focuses": canonical_technique_focuses,
        "user_feedback": {
            "sleep_energy": None,
            "pain": [],
            "muscle_feel": {},
            "session_rir": None,
            "recovery_next_day": None,
            "notes": None,
        },
        "analysis": {
            "pipeline_version": PIPELINE_VERSION,
            "analysis_model": None,
            "model_policy": "scripts-first; model only for visual classification and form judgment",
            "manifest": legacy.get("analysis_manifest"),
            "migrated_at": now,
            "legacy_report_json": str(report_path.relative_to(project)),
        },
        "revisions": [{
            "revision": 1,
            "timestamp": now,
            "reason": "migrated from verified legacy workout report",
            "source": str(report_path.relative_to(project)),
        }],
    }
    if legacy.get("evidence_manifest"):
        evidence_path = (report_path.parent / legacy["evidence_manifest"]).resolve()
        result["analysis"]["evidence_manifest"] = os.path.relpath(evidence_path, project)
        result["analysis"]["evidence_manifest_sha256"] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        result["analysis"]["coverage_complete"] = not legacy.get("_incomplete_counts", False)
        result["revisions"][0]["reason"] = "imported report with structured per-cycle evidence"
    if legacy.get("unresolved_candidates"):
        result["unresolved_candidates"] = legacy["unresolved_candidates"]
    if legacy.get("aggregate_cost"):
        result["analysis"]["aggregate_cost"] = legacy["aggregate_cost"]
    if legacy.get("analysis_runs"):
        result["analysis"]["analysis_runs"] = [os.path.relpath((report_path.parent / name).resolve(), project)
                                                   for name in legacy["analysis_runs"]]
    if legacy.get("analysis_scope"):
        session_duration(legacy)
        result["analysis_scope"] = legacy["analysis_scope"]
    for key in ('form_observations', 'phase_analysis'):
        if key in legacy:
            result[key] = copy.deepcopy(legacy[key])
    for observation in result.get('form_observations', []):
        for key in ('image', 'gif'):
            if observation.get(key):
                observation[key] = project_asset(observation[key])
    if legacy.get('laterality_review'):
        result['analysis']['laterality_review'] = copy.deepcopy(legacy['laterality_review'])
    if side_review_required:
        result['analysis']['laterality_policy_version'] = laterality.VERSION
        result['analysis']['laterality_complete'] = not any(
            (row.get('side_review') or {}).get('status') == 'unresolved'
            for exercise in result['exercises'] for row in exercise['sets'])
        if not result['analysis']['laterality_complete']:
            result['status'] = 'review_pending'
    return result


def migrate(project: Path, force: bool = False, report_filter: set[str] | None = None) -> list[Path]:
    exercises, aliases, issues = catalog_maps(project)
    reports = sorted((project / "form-reviews").glob("*-workout-report.json"))
    used_ordinals: dict[str, set[int]] = defaultdict(set)
    report_sessions: dict[str, Path] = {}
    for session_path in session_paths(project):
        session = load_json(session_path)
        session_date = str(session.get("date") or "")
        match = re.fullmatch(r"\d{4}-\d{2}-\d{2}-(\d{3})", session_path.stem)
        if session_date and match:
            used_ordinals[session_date].add(int(match.group(1)))
        legacy_report = str((session.get("analysis") or {}).get("legacy_report_json") or "")
        if legacy_report:
            report_sessions[legacy_report] = session_path
    written: list[Path] = []
    for report_path in reports:
        if report_filter and report_path.name not in report_filter and str(report_path.relative_to(project)) not in report_filter:
            continue
        report_data = load_json(report_path)
        report_date = str((report_data.get("report") or {}).get("date") or "")
        date.fromisoformat(report_date)
        report_key = str(report_path.relative_to(project))
        output_path = report_sessions.get(report_key)
        if output_path is None:
            ordinal = 1
            while ordinal in used_ordinals[report_date]:
                ordinal += 1
            used_ordinals[report_date].add(ordinal)
            output_path = project / "data/sessions" / session_filename(report_date, ordinal)
        if output_path.exists() and not force:
            continue
        session_id = output_path.stem
        session = migrate_report(project, report_path, output_path, session_id, exercises, aliases, issues)
        if output_path.exists() and force:
            previous = load_json(output_path)
            # Re-importing verified video counts must not erase subsequent user feedback.
            if previous.get("user_feedback"):
                session["user_feedback"] = previous["user_feedback"]
            if previous.get("workout_id") and not session.get("workout_id"):
                session["workout_id"] = previous["workout_id"]
            if previous.get('plan') and session['plan'].get('execution') == 'unclassified':
                session['plan'] = copy.deepcopy(previous['plan'])
            if previous.get('analysis', {}).get('analysis_model') and not session['analysis'].get('analysis_model'):
                session['analysis']['analysis_model'] = previous['analysis']['analysis_model']
            preserve_set_annotations(previous, session)
            for key in ('correction_run', 'laterality_review'):
                if key not in session['analysis'] and key in (previous.get('analysis') or {}):
                    session['analysis'][key] = copy.deepcopy(previous['analysis'][key])
            previous_revision = int(previous.get("revision") or 1)
            revision_path = project / "data/revisions" / f"{session_id}-r{previous_revision:03d}.json"
            if not revision_path.exists():
                atomic_write_json(revision_path, previous)
            now = datetime.now().astimezone().isoformat(timespec="seconds")
            session["revision"] = previous_revision + 1
            session["revisions"] = [
                *(previous.get("revisions") or []),
                {
                    "revision": previous_revision + 1,
                    "timestamp": now,
                    "reason": "remigrated after verified report correction or evidence update",
                    "source": str(report_path.relative_to(project)),
                    "previous_snapshot": str(revision_path.relative_to(project)),
                },
            ]
        laterality.validate_session(project, session, report_data, report_path)
        atomic_write_json(output_path, session)
        written.append(output_path)
    return written


def preserve_set_annotations(previous, current):
    """Keep later user load/RIR annotations only on the same source-mapped set."""
    fingerprints = lambda s: {x['file']: x.get('fingerprint') for x in s.get('sources', [])}
    if fingerprints(previous) != fingerprints(current):
        return
    old_rows = {(ex['exercise_id'], row.get('set_index')): row
                for ex in previous.get('exercises', []) for row in ex.get('sets', [])}
    for ex in current.get('exercises', []):
        for row in ex.get('sets', []):
            old = old_rows.get((ex['exercise_id'], row.get('set_index')))
            if not old or old.get('source_segments') != row.get('source_segments'):
                continue
            if old.get('evidence_set_id') != row.get('evidence_set_id'):
                continue
            for key in ('load', 'rir', 'tempo'):
                empty = (row.get(key) or {}).get('value') is None if key == 'load' else row.get(key) is None
                if empty and old.get(key) is not None:
                    row[key] = copy.deepcopy(old[key])
            # Old historical reports may omit side; never overwrite an explicit new review.
            if row.get('side') in ('unknown', None) and not row.get('side_review') and old.get('side') in ('left', 'right'):
                for key in laterality.SIDE_FIELDS:
                    if key in old:
                        row[key] = copy.deepcopy(old[key])


def session_paths(project: Path) -> list[Path]:
    return sorted((project / "data/sessions").glob("*.json"))


def validate_legacy_report(path: Path) -> list[str]:
    errors: list[str] = []
    data = load_json(path)
    try:
        validate_new_evidence(data, path)
    except (ValueError, KeyError, OSError, TypeError, ImportError) as exc:
        errors.append(f"{path}: structured count evidence failed: {exc}")
    summary: dict[str, tuple[int, int]] = {}
    for item in data.get("exercises") or []:
        reps = item.get("reps") or []
        if item.get("sets") != len(reps):
            errors.append(f"{path}: exercise sets/reps mismatch for {item.get('name')}")
        if item.get("total") != sum(reps):
            errors.append(f"{path}: exercise total mismatch for {item.get('name')}")
        summary[str(item.get("name"))] = (int(item.get("sets") or 0), int(item.get("total") or 0))
    timeline: dict[str, list[int]] = defaultdict(list)
    for row in data.get("timeline") or []:
        timeline[str(row.get("exercise"))].append(int(row.get("completed") or 0))
    timeline_summary = {name: (len(values), sum(values)) for name, values in timeline.items()}
    if summary != timeline_summary:
        errors.append(f"{path}: exercise summary does not match timeline: {summary!r} != {timeline_summary!r}")
    return errors


def audit(project: Path) -> tuple[list[str], list[str]]:
    exercises, _, issues = catalog_maps(project)
    errors: list[str] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()
    for path in session_paths(project):
        data = load_json(path)
        try:
            laterality.validate_session(project, data)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            errors.append(f'{path}: {exc}')
        if data.get("schema_version") != SCHEMA_VERSION:
            errors.append(f"{path}: unsupported schema_version {data.get('schema_version')}")
        session_id = str(data.get("session_id") or "")
        if not session_id or session_id in seen_ids:
            errors.append(f"{path}: missing or duplicate session_id {session_id!r}")
        seen_ids.add(session_id)
        try:
            date.fromisoformat(str(data.get("date") or ""))
        except ValueError:
            errors.append(f"{path}: invalid date")
        source_names = {str(item.get("file")) for item in data.get("sources") or []}
        for exercise in data.get("exercises") or []:
            exercise_id = str(exercise.get("exercise_id") or "")
            if exercise_id not in exercises and not exercise_id.startswith("unmapped."):
                errors.append(f"{path}: unknown exercise_id {exercise_id}")
            sets = exercise.get("sets") or []
            total = sum(int(item.get("completed_reps") or 0) for item in sets)
            if exercise.get("total_sets") != len(sets):
                errors.append(f"{path}: total_sets mismatch for {exercise_id}")
            if exercise.get("total_completed_reps") != total:
                errors.append(f"{path}: total reps mismatch for {exercise_id}")
            for row in sets:
                source_file = row.get("source_file")
                if source_file and str(source_file) not in source_names:
                    errors.append(f"{path}: set references unknown source {source_file}")
                start_s, end_s = row.get("start_s"), row.get("end_s")
                if start_s is not None and end_s is not None and not float(end_s) > float(start_s) >= 0:
                    errors.append(f"{path}: invalid set times for {exercise_id}")
                for segment in row.get("source_segments") or []:
                    segment_file = str(segment.get("file") or "")
                    if segment_file not in source_names:
                        errors.append(f"{path}: set segment references unknown source {segment_file}")
                    segment_start, segment_end = segment.get("start_s"), segment.get("end_s")
                    if segment_start is None or segment_end is None or not float(segment_end) > float(segment_start) >= 0:
                        errors.append(f"{path}: invalid source segment times for {exercise_id}")
        for issue in data.get("issues") or []:
            issue_id = str(issue.get("issue_id") or "")
            if issue_id not in issues and not issue_id.startswith("unmapped."):
                errors.append(f"{path}: unknown issue_id {issue_id}")
            if not issue.get("timestamp") or not issue.get("cue") or not issue.get("image"):
                errors.append(f"{path}: incomplete issue evidence for {issue_id}")
            image_path = Path(str(issue.get("image") or ""))
            if not image_path.is_absolute():
                image_path = project / image_path
            if not image_path.is_file():
                errors.append(f"{path}: missing issue image {image_path}")
            gif_value = issue.get("gif")
            if gif_value:
                gif_path = Path(str(gif_value))
                if not gif_path.is_absolute():
                    gif_path = project / gif_path
                if not gif_path.is_file():
                    errors.append(f"{path}: missing issue GIF {gif_path}")
        for source in data.get("sources") or []:
            source_path = Path(str(source.get("original_path") or ""))
            if not source_path.is_file():
                warnings.append(f"{path.name}: source unavailable at {source_path}")

    for report_path in sorted((project / "form-reviews").glob("*-workout-report.json")):
        errors.extend(validate_legacy_report(report_path))

    guide_path = project / "exercise-guides.html"
    guide_text = guide_path.read_text(encoding="utf-8") if guide_path.is_file() else ""
    guide_ids = set(re.findall(r'id="([A-Za-z0-9-]+)"', guide_text))
    referenced: set[str] = set()
    for path in [project / "dashboard.html", project / "exercise-tutorials.md", *sorted((project / "form-reviews").glob("*.json"))]:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        referenced.update(re.findall(r'exercise-guides\.html#([A-Za-z0-9-]+)', text))
    for anchor in sorted(referenced - guide_ids):
        errors.append(f"missing exercise guide anchor: {anchor}")
    return errors, warnings


def normalize_confidence(value: str) -> str:
    raw = value.lower()
    if raw in {"high", "高", "中高"}:
        return "high"
    if raw in {"low", "低"}:
        return "low"
    return "medium"


def issue_status(exercise_sessions: list[str], occurrence_sessions: list[str]) -> str:
    if not occurrence_sessions:
        return "未发现"
    if not exercise_sessions or occurrence_sessions[-1] not in exercise_sessions[-3:]:
        return "历史待观察"
    if len(occurrence_sessions) == 1:
        return "新出现" if occurrence_sessions[-1] == exercise_sessions[-1] else "待复核"
    recent_exercise = exercise_sessions[-3:]
    recent_flags = [session_id in set(occurrence_sessions) for session_id in recent_exercise]
    if recent_flags and not recent_flags[-1]:
        return "改善"
    if len(recent_flags) >= 2 and recent_flags[-1] and not recent_flags[-2]:
        return "反复"
    if len(recent_flags) >= 2 and recent_flags[-1] and recent_flags[-2]:
        return "持续"
    return "观察"


def build_snapshot(project: Path, last_n: int = 6) -> tuple[dict[str, Any], dict[str, Any]]:
    exercises_catalog, _, issues_catalog = catalog_maps(project)
    sessions = [load_json(path) for path in session_paths(project)]
    sessions.sort(key=lambda item: (item["date"], item["session_id"]))
    workouts = training_groups(collect_workouts(project))
    recent_workouts = workouts[-last_n:]
    recent_ids = {s["session_id"] for w in recent_workouts for s in w["sessions"]}
    recent = [s for s in sessions if s["session_id"] in recent_ids]
    recent_dates = {w["date"] for w in recent_workouts}
    recent_corrections = laterality.correction_context(recent)
    recent_corrections.extend({"workout_id": w["workout_id"], "kind": "coaching_correction", **c}
                             for w in collect_workouts(project) for c in w.get("corrections", []))
    review_requirements = laterality.policy(project).get('standing_corrections', [])
    feedback = [{"session_id": s["session_id"], "training_date": s["date"], **s["user_feedback"]}
                for s in recent if any(v for v in (s.get("user_feedback") or {}).values())]
    profile_path = project / "data/coach-profile.json"
    profile = load_json(profile_path) if profile_path.exists() else {}
    recent_reviews = [{"workout_id": w["workout_id"], "date": w["date"], "review": w.get("review"),
                       "records": [{"session_id": s["session_id"], "summary": s.get("summary"),
                                    "corrections": laterality.correction_context([s]),
                                    "technique_focuses": s.get("technique_focuses") or [],
                                    "observations": [{k: o.get(k) for k in ("title", "observation", "interpretation", "timestamp")}
                                                     for o in s.get("form_observations") or []]}
                                   for s in w["sessions"]]}
                      for w in collect_workouts(project) if w.get("review") or w["sessions"]][-last_n:]
    current_plans = [{"workout_id": w["workout_id"], "date": w["date"], "plan": w["plan"],
                      "has_actual_records": bool(w["sessions"])} for w in collect_workouts(project) if w.get("plan")][-3:]

    exercise_summary: dict[str, dict[str, Any]] = {}
    exercise_session_ids: dict[str, list[str]] = defaultdict(list)
    for session in sessions:
        for exercise in session.get("exercises") or []:
            exercise_id = exercise["exercise_id"]
            exercise_session_ids[exercise_id].append(session["session_id"])
            target = exercise_summary.setdefault(exercise_id, {
                "exercise_id": exercise_id,
                "name": exercise.get("name") or exercises_catalog.get(exercise_id, {}).get("name") or exercise_id,
                "session_count": 0,
                "total_sets": 0,
                "total_completed_reps": 0,
                "latest": None,
            })
            target["session_count"] += 1
            target["total_sets"] += int(exercise.get("total_sets") or 0)
            target["total_completed_reps"] += int(exercise.get("total_completed_reps") or 0)
            target["latest"] = {
                "date": session["date"],
                "sets": exercise.get("total_sets"),
                "completed_reps": exercise.get("total_completed_reps"),
                "reps_by_set": [row.get("completed_reps") for row in exercise.get("sets") or []],
                "sides_by_set": [row.get("side") for row in exercise.get("sets") or []],
                "load_known": any((row.get("load") or {}).get("value") is not None for row in exercise.get("sets") or []),
                "rir_known": any(row.get("rir") is not None for row in exercise.get("sets") or []),
            }

    issue_occurrences: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for session in sessions:
        seen_in_session: set[str] = set()
        for issue in session.get("issues") or []:
            key = f"{session['session_id']}:{issue['issue_id']}"
            if key in seen_in_session:
                continue
            seen_in_session.add(key)
            issue_occurrences[issue["issue_id"]].append({
                "session_id": session["session_id"],
                "date": session["date"],
                "exercise_id": issue.get("exercise_id"),
                "timestamp": issue.get("timestamp"),
                "confidence": normalize_confidence(str(issue.get("confidence") or "medium")),
            })

    issue_summary: list[dict[str, Any]] = []
    for issue_id, occurrences in issue_occurrences.items():
        catalog = issues_catalog.get(issue_id, {})
        exercise_id = str(occurrences[-1].get("exercise_id") or "")
        occurrence_sessions = [item["session_id"] for item in occurrences]
        status = issue_status(exercise_session_ids.get(exercise_id, []), occurrence_sessions)
        if occurrences[-1]["date"] not in recent_dates or issue_id.startswith("unmapped."):
            status = "历史待观察"
        issue_summary.append({
            "issue_id": issue_id,
            "name": catalog.get("name") or issue_id,
            "exercise_id": exercise_id,
            "occurrence_count": len(occurrences),
            "first_seen": occurrences[0]["date"],
            "last_seen": occurrences[-1]["date"],
            "status": status,
            "cue": catalog.get("cue"),
            "stop_rule": catalog.get("stop_rule"),
        })
    status_priority = {"持续": 4, "反复": 3, "新出现": 2, "观察": 1, "改善": 0}
    issue_summary.sort(key=lambda item: (status_priority.get(item["status"], 0), item["last_seen"], item["occurrence_count"]), reverse=True)

    dates = [date.fromisoformat(item["date"]) for item in workouts]
    gaps = [(right - left).days for left, right in zip(dates, dates[1:])]
    span_days = max(1, (dates[-1] - dates[0]).days + 1) if dates else 1
    sessions_per_week = round(len(workouts) / span_days * 7, 2)

    recent_exercise_ids = {
        exercise["exercise_id"]
        for workout in workouts[-3:]
        for session in workout["sessions"]
        for exercise in session.get("exercises") or []
    }
    gaps_in_plan: list[dict[str, str]] = []
    if workouts and not ({"shoulder.press_seated_bottle", "shoulder.press_seated_dumbbell", "shoulder.ytw"} & recent_exercise_ids):
        gaps_in_plan.append({
            "id": "shoulder-specific-missing",
            "observation": "最近三次训练没有肩部专项推举或后肩稳定动作。",
            "candidate": "结合当前器械、动作覆盖与近期反馈判断是否安排肩部专项，不自动追加训练量。",
        })

    priorities = [item for item in issue_summary if item["status"] in {"持续", "反复", "新出现"}][:3]
    rule_candidates = [
        {"reason": item["name"], "recommendation": item.get("cue"), "stop_rule": item.get("stop_rule")}
        for item in priorities
    ]
    rule_candidates.extend({"reason": item["observation"], "recommendation": item["candidate"], "stop_rule": None} for item in gaps_in_plan)

    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    snapshot = {
        "schema_version": 1,
        "generated_at": generated_at,
        "source_session_ids": [item["session_id"] for item in sessions],
        "window": {"requested_last_n": last_n, "workout_ids": [w["workout_id"] for w in recent_workouts], "session_ids": [item["session_id"] for item in recent]},
        "coach_profile": profile,
        "recent_feedback": feedback,
        "recent_corrections": recent_corrections,
        "review_requirements": review_requirements,
        "recent_workout_reviews": recent_reviews,
        "recent_plans": current_plans,
        "frequency": {
            "session_count": len(workouts),
            "record_count": len(sessions),
            "count_basis": "实际训练场次；关联热身、拉伸和专项报告不另计；历史记录默认同日归组，显式 workout_id 可区分同日多练",
            "first_date": dates[0].isoformat() if dates else None,
            "last_date": dates[-1].isoformat() if dates else None,
            "sessions_per_week": sessions_per_week,
            "average_gap_days": round(mean(gaps), 2) if gaps else None,
            "recent_gaps_days": gaps[-5:],
        },
        "recent_sessions": [{
            "session_id": item["session_id"],
            "date": item["date"],
            "label": item.get("session_label"),
            "duration_s": session_duration(item),
            "exercise_ids": [exercise["exercise_id"] for exercise in item.get("exercises") or []],
            "issue_ids": sorted({issue["issue_id"] for issue in item.get("issues") or []}),
            "feedback_complete": any(value for value in (item.get("user_feedback") or {}).values()),
        } for item in recent],
        "exercise_summary": sorted(exercise_summary.values(), key=lambda item: item["total_completed_reps"], reverse=True),
        "issue_summary": issue_summary,
        "current_priorities": priorities,
        "plan_gaps": gaps_in_plan,
        "rule_candidates": rule_candidates,
        "data_quality": {
            "sessions_with_rir": sum(any(row.get("rir") is not None for exercise in session.get("exercises") or [] for row in exercise.get("sets") or []) for session in sessions),
            "sessions_with_user_feedback": sum(any(value for value in (session.get("user_feedback") or {}).values()) for session in sessions),
            "sources_currently_available": sum(Path(source.get("original_path") or "").is_file() for session in sessions for source in session.get("sources") or []),
            "source_count": sum(len(session.get("sources") or []) for session in sessions),
        },
    }
    state = {
        "schema_version": 1,
        "updated_at": generated_at,
        "latest_session_id": sessions[-1]["session_id"] if sessions else None,
        "latest_date": sessions[-1]["date"] if sessions else None,
        "frequency": snapshot["frequency"],
        "current_priorities": priorities,
        "coach_profile": profile,
        "recent_feedback": feedback,
        "recent_corrections": recent_corrections,
        "review_requirements": review_requirements,
        "recent_workout_reviews": recent_reviews,
        "recent_plans": current_plans,
        "next_session_candidates": rule_candidates,
        "plan_gaps": gaps_in_plan,
        "latest_exercise_state": [item for item in exercise_summary.values() if item.get("latest")],
        "pain_limits": [item for session in sessions for item in (session.get("user_feedback") or {}).get("pain", [])],
        "data_quality": snapshot["data_quality"],
    }
    return snapshot, state


def export_csv(project: Path, output: Path) -> None:
    rows: list[dict[str, Any]] = []
    for path in session_paths(project):
        session = load_json(path)
        laterality.validate_session(project, session)
        for exercise in session.get("exercises") or []:
            for row in exercise.get("sets") or []:
                rows.append({
                    "session_id": session["session_id"],
                    "date": session["date"],
                    "exercise_id": exercise["exercise_id"],
                    "exercise_name": exercise["name"],
                    "set_index": row.get("set_index"),
                    "side": row.get("side"),
                    "side_review_status": (row.get("side_review") or {}).get("status"),
                    "side_unresolved_reason": (row.get("side_review") or {}).get("reason"),
                    "completed_reps": row.get("completed_reps"),
                    "coverage_complete": row.get("coverage_complete"),
                    "completed_reps_is_lower_bound": row.get("coverage_complete") is False,
                    "partial_reps": row.get("partial_reps"),
                    "load_kg": (row.get("load") or {}).get("value"),
                    "tempo": row.get("tempo"),
                    "rir": row.get("rir"),
                    "confidence": row.get("confidence"),
                    "source_file": row.get("source_file"),
                    "source_segments_json": json.dumps(row.get("source_segments") or [], ensure_ascii=False, separators=(",", ":")),
                    "start_s": row.get("start_s"),
                    "end_s": row.get("end_s"),
                })
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys()) if rows else []
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_dashboard_data(project: Path, state: dict[str, Any]) -> Path:
    index_path = project / "form-reviews/report-index.json"
    index = load_json(index_path) if index_path.is_file() else {"records": []}
    scoped_durations = {}
    for path in session_paths(project):
        session = load_json(path)
        report_path = (session.get("analysis") or {}).get("legacy_report_json")
        if session.get("analysis_scope") and report_path:
            scoped_durations[Path(report_path).with_suffix(".html").name] = session_duration(session)
    changed = False
    for record in index.get("records") or []:
        duration = scoped_durations.get(record.get("url"))
        if duration is not None and record.get("duration_s") != duration:
            record["duration_s"] = duration
            changed = True
    if changed:
        atomic_write_json(index_path, index)
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "coach": state,
        "records": index.get("records") or [],
    }
    dashboard = project / "dashboard.html"
    start_marker = "<!-- FITNESS_DASHBOARD_DATA_START -->"
    end_marker = "<!-- FITNESS_DASHBOARD_DATA_END -->"
    if (project / "data/workouts").is_dir():
        render_training_pages(project)
        return dashboard
    html = dashboard.read_text(encoding="utf-8")
    if start_marker not in html or end_marker not in html:
        raise ValueError("dashboard data markers are missing")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    data = data.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    block = f'{start_marker}\n  <script>\n    window.FITNESS_DASHBOARD_DATA = {data};\n  </script>\n  {end_marker}'
    before, remainder = html.split(start_marker, 1)
    _, after = remainder.split(end_marker, 1)
    atomic_write_text(dashboard, before + block + after)
    return dashboard


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command", required=True)
    migrate_parser = subparsers.add_parser("migrate", help="migrate legacy workout reports into durable sessions")
    migrate_parser.add_argument("--force", action="store_true")
    migrate_parser.add_argument("--report", action="append", default=[], help="only migrate this report filename or project-relative path")
    subparsers.add_parser("audit", help="validate durable sessions, legacy reports, evidence, and guide anchors")
    snapshot_parser = subparsers.add_parser("snapshot", help="build compact coach snapshot and current state")
    snapshot_parser.add_argument("--last", type=int, default=6)
    snapshot_parser.add_argument("--output", type=Path, default=Path("derived/coach-snapshot.json"))
    snapshot_parser.add_argument("--state-output", type=Path, default=Path("derived/coach-state.json"))
    export_parser = subparsers.add_parser("export-csv", help="export one row per set for external statistics")
    export_parser.add_argument("--output", type=Path, default=Path("derived/training-sets.csv"))
    rebuild_parser = subparsers.add_parser("rebuild", help="migrate, audit, snapshot, and export")
    rebuild_parser.add_argument("--last", type=int, default=6)
    context_parser = subparsers.add_parser("context", help="read-only current context; no publication")
    context_parser.add_argument("--last", type=int, default=6)
    context_parser.add_argument("--mode", choices=("coach", "review"), default="coach")
    context_parser.add_argument("--workout")
    args = parser.parse_args()
    project = args.project.resolve()
    if args.command == "context":
        from coach_context import compact_context
        print(json.dumps(compact_context(project, args.last, args.mode, args.workout), ensure_ascii=False, indent=2))
        return 0

    if args.command == "migrate":
        written = migrate(project, force=args.force, report_filter=set(args.report) or None)
        for path in written:
            print(path)
        print(f"migrated={len(written)}")
        return 0
    if args.command == "audit":
        errors, warnings = audit(project)
        for warning in warnings:
            print(f"WARNING: {warning}")
        for error in errors:
            print(f"ERROR: {error}")
        print(f"sessions={len(session_paths(project))} warnings={len(warnings)} errors={len(errors)}")
        return 1 if errors else 0
    if args.command == "snapshot":
        snapshot, state = build_snapshot(project, last_n=max(1, args.last))
        output = args.output if args.output.is_absolute() else project / args.output
        state_output = args.state_output if args.state_output.is_absolute() else project / args.state_output
        atomic_write_json(output, snapshot)
        atomic_write_json(state_output, state)
        write_dashboard_data(project, state)
        print(output)
        print(state_output)
        return 0
    if args.command == "export-csv":
        output = args.output if args.output.is_absolute() else project / args.output
        export_csv(project, output)
        print(output)
        return 0
    if args.command == "rebuild":
        written = migrate(project, force=False)
        errors, warnings = audit(project)
        if errors:
            for error in errors:
                print(f"ERROR: {error}")
            print(f"rebuild blocked: {len(errors)} errors; derived views not published")
            return 1
        snapshot, state = build_snapshot(project, last_n=max(1, args.last))
        atomic_write_json(project / "derived/coach-snapshot.json", snapshot)
        atomic_write_json(project / "derived/coach-state.json", state)
        from coach_context import compact_context
        atomic_write_json(project / "derived/coach-context.json", compact_context(project, args.last))
        export_csv(project, project / "derived/training-sets.csv")
        write_dashboard_data(project, state)
        for warning in warnings:
            print(f"WARNING: {warning}")
        for error in errors:
            print(f"ERROR: {error}")
        print(f"migrated={len(written)} sessions={len(session_paths(project))} warnings={len(warnings)} errors={len(errors)}")
        return 1 if errors else 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
