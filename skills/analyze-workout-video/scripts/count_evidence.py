"""Structural/provenance checks, not an automated judge of exercise semantics."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path


def require(ok, message):
    if not ok:
        raise ValueError(message)


def number(value):
    require(isinstance(value, (float, int)) and not isinstance(value, bool)
            and math.isfinite(value), "expected finite numeric time")
    return float(value)


def read(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    require(isinstance(data, dict), "expected JSON object")
    return data


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def local(base, name):
    require(isinstance(name, str) and name and "://" not in name, "local evidence path required")
    return (base / name).resolve()


def check_asset(base, asset):
    p = local(base, asset["path"])
    require(p.is_file() and digest(p) == asset["sha256"], f"missing or changed evidence asset: {p}")
    return p


def load_receipt(path):
    path = Path(path).resolve()
    r = read(path)
    require(r.get("schema_version") == 1 and r.get("kind") == "source_review_frames",
            "frame receipt must come from extract_review_frames.py")
    m = r["window"]["completion"]
    require(m.get("complete") is True and m.get("coarse_only") is False
            and m.get("purpose") == "source_local_exact_verification", "coarse or incomplete window")
    require(8 <= number(m["sampling_fps"]) <= 12, "source verification needs 8-12 fps")
    require(m["source_fingerprint"] == r["source"]["fingerprint"]
            and m["source_path"] == r["source"]["path"], "source identity mismatch")
    start, end = map(number, r["requested_range_s"])
    require(m["source_start_s"] <= start < end <= m["source_end_s"], "requested range outside window")
    frames = r["frames"]
    require(frames and len(frames) == r["expected_frame_count"], "truncated extraction")
    ids = [f["id"] for f in frames]
    require(len(set(ids)) == len(ids), "duplicate frame ids")
    require(ids == r["requested_frame_ids"], "requested and emitted frame ids differ")
    times = [number(f["source_s"]) for f in frames]
    require(all(a < b for a, b in zip(times, times[1:])), "unordered frame timestamps")
    require(abs(times[0] - start) <= .15 and abs(times[-1] - end) <= .15,
            "receipt omits requested first or last frame")
    for f in frames:
        require(0 <= f["frame_index"] < m["validation"]["frame_count"], "invalid window frame index")
        require(f["id"] == f'f{f["frame_index"]:06d}'
                and abs(number(f["window_pts_s"]) - f["frame_index"] / m["sampling_fps"]) < .002,
                "frame id/index/time mismatch")
        require(abs(number(f["source_s"]) - (m["source_start_s"] + number(f["window_pts_s"]))) < .00001,
                "source/window timestamp mismatch")
        check_asset(path.parent, f)
    page_ids = []; covered = []
    for page in r["pages"]:
        require(page["id"] not in page_ids, "duplicate page id")
        page_ids.append(page["id"])
        require(0 < len(page["frame_ids"]) <= 48, "oversized/empty contact sheet")
        require(160 <= page["tile_width"] <= 320 and page["width"] <= 1920
                and page["height"] <= 2000, "unreadable contact-sheet geometry")
        covered.extend(page["frame_ids"])
        check_asset(path.parent, page)
    require(covered == ids, "pagination dropped, duplicated, or reordered frames")
    return r


def validate_evidence(path):
    path = Path(path).resolve(); e = read(path)
    evidence_version = e.get("schema_version")
    require(evidence_version in (1, 2, 3), "structured count evidence v1, v2 or v3 required")
    require(e.get("method") == "visual_cycle_sequence", "candidate signals are navigation only")
    require(e.get("set_id") and e.get("exercise_id"), "set/exercise identity required")
    review = e.get("review", {})
    require(review.get("reviewer") and review.get("cycle_definition"), "visual review attribution required")
    require(len(review.get("positive_cues", [])) >= 2 and review.get("excluded_lookalike"),
            "classification cues and look-alike exclusion required")
    receipts = {}; frames = {}; viewed = set(); assets = {}
    for key, name in e.get("receipts", {}).items():
        rp = local(path.parent, name); r = load_receipt(rp); receipts[key] = r
        for f in r["frames"]:
            frames[f'{key}/{f["id"]}'] = (r, f)
        pages = {p["id"]: p for p in r["pages"]}
        selected = review.get("viewed_pages", {}).get(key, [])
        require(len(selected) == len(set(selected)) and set(selected) <= set(pages), "unknown viewed page")
        for pid in selected:
            page = pages[pid]
            viewed.update(f'{key}/{fid}' for fid in page["frame_ids"])
            asset = local(rp.parent, page["path"])
            assets[str(asset)] = {"path": str(asset), "sha256": page["sha256"],
                                  "frames": len(page["frame_ids"]),
                                  "pixels": page["width"] * page["height"]}
    require(receipts and viewed, "no reviewed frame evidence")
    require(set(review.get("viewed_pages", {})) <= set(receipts), "unknown receipt in review")
    segments = e.get("segments", [])
    require(segments, "working-set source segments required")
    sources = {}; total = 0.; ordered = []
    for s in segments:
        a, b = number(s["start_s"]), number(s["end_s"])
        require(0 <= a < b, "invalid working segment")
        # Same source may occur in adjacent segments, but never overlap.
        for previous in ordered:
            if previous["file"] == s["file"]:
                require(a >= previous["end_s"], "overlapping/reversed source segments")
        ordered.append(s)
        matches = [r for r in receipts.values() if r["source"]["file"] == s["file"]]
        require(matches, "segment has no source receipt")
        identities = {(r["source"]["path"], r["source"]["fingerprint"]) for r in matches}
        require(len(identities) == 1, "conflicting source identities")
        sources[s["file"]] = matches[0]["source"]
        intervals = sorted(r["requested_range_s"] for r in matches)
        cursor = a
        for lo, hi in intervals:
            if lo <= cursor + .15 and hi > cursor:
                cursor = hi
        require(cursor >= b - .15, "working segment extends beyond extracted coverage")
        total += b - a

    def frame(ref):
        require(ref in frames and ref in viewed, f"frame was not on a reviewed page: {ref}")
        return frames[ref]

    def position(ref):
        r, f = frame(ref); offset = 0.
        for s in segments:
            if r["source"]["file"] == s["file"] and s["start_s"] - .001 <= f["source_s"] <= s["end_s"] + .001:
                return offset + f["source_s"] - s["start_s"]
            offset += s["end_s"] - s["start_s"]
        raise ValueError("cycle frame outside working-set boundaries")

    require(type(e.get("coverage_complete")) is bool, "explicit coverage status required")
    context = e.get("context", {})
    before_r, before_f = frame(context["before"])
    require(before_r["source"]["file"] == segments[0]["file"]
            and before_f["source_s"] < segments[0]["start_s"], "missing pre-set context")
    if e["coverage_complete"]:
        after_r, after_f = frame(context["after"])
        require(after_r["source"]["file"] == segments[-1]["file"]
                and after_f["source_s"] > segments[-1]["end_s"], "missing post-set reset coverage")
        require(context.get("after_observation"), "post-set reset observation required")
    else:
        require(e.get("unresolved_reason"), "incomplete coverage needs a concrete reason")
    for excluded in context.get("excluded_context", []):
        require(excluded.get("reason") and excluded.get("observation"), "excluded context needs observation and reason")
        refs = excluded.get("frames", [])
        require(refs, "excluded context needs frame references")
        for ref in refs:
            r, f = frame(ref)
            require(not any(s["file"] == r["source"]["file"] and s["start_s"] <= f["source_s"] <= s["end_s"]
                            for s in segments), "in-set exclusion belongs in events, not context")
    counts = dict(complete=0, partial=0, uncertain=0, excluded=0)
    mode = e.get("counting_mode", "bilateral")
    require(mode in ("bilateral", "per_side"), "unknown counting mode")
    previous_ends = {}; event_ids = set(); intervals = []
    side_counts = {"left": 0, "right": 0}
    for event in e.get("events", []):
        require(event.get("id") and event["id"] not in event_ids, "duplicate/empty event id")
        event_ids.add(event["id"])
        status = event["status"]
        require(status in counts, "unknown event status")
        refs = event.get("frames", [])
        require(len(refs) == 3 and len(set(refs)) == 3, "each event needs three ordered distinct frames")
        times = [position(ref) for ref in refs]
        require(times[0] < times[1] < times[2], "phase order must be boundary-opposite-boundary")
        side = event.get("side") if mode == "per_side" else "both"
        require(side in (("left", "right") if mode == "per_side" else ("both",)), "per-side event needs left/right identity")
        require(times[0] >= previous_ends.get(side, -1.) - .001, "duplicate or overlapping cycles")
        require(event.get("observation"), "event needs a visual observation")
        if status == "complete":
            require(event.get("phase_labels") == ["boundary", "opposite", "boundary"], "complete return not confirmed")
        else:
            require(event.get("reason"), "non-complete events need a reason")
        counts[status] += 1; previous_ends[side] = times[2]
        intervals.append((times[0], times[2]))
        if status == "complete" and mode == "per_side": side_counts[side] += 1
    require(event_ids, "no event records; final counts are derived, not asserted")
    cursor = 0.
    for start, end in sorted(intervals):
        require(start <= cursor + .15, "unreviewed gap between event records; record pause/excluded/uncertain interval")
        cursor = max(cursor, end)
    require(abs(cursor - total) <= .15, "events do not cover first/last working boundaries")
    completed = min(side_counts.values()) if mode == "per_side" else counts["complete"]
    if e.get("claimed_count") is not None:
        require(e["claimed_count"] == completed, "claimed count differs from confirmed events")
    is_set = e.get("disposition", "working_set") == "working_set"
    require(is_set or (e.get("disposition") == "non_set" and counts["complete"] == 0
                       and counts["excluded"] == len(event_ids)), "invalid non-set dismissal")
    if evidence_version >= 2:
        audit = e.get("classification_audit", {})
        require(audit.get("full_context_reviewed") is True,
                "v2 classification requires a full-context review before cycle counting")
        require(audit.get("intent") in ("working_set", "setup_adjustment", "transition", "ambiguous"),
                "v2 classification intent is missing or invalid")
        require(audit.get("closest_lookalike") and audit.get("discriminator"),
                "v2 classification requires a closest look-alike and concrete discriminator")
        if is_set:
            require(audit["intent"] == "working_set", "working-set evidence conflicts with context intent")
            require(audit.get("stable_configuration") is True and audit.get("consistent_cycle_path") is True,
                    "working set lacks stable configuration or consistent cycle path")
            require(audit.get("setup_or_adjustment_observed") is False,
                    "setup/equipment/clothing adjustment must be split out before counting")
            require(counts["complete"] >= 2, "working set needs at least two complete repeated cycles")
            if audit.get("novel_or_one_off"):
                second = audit.get("second_pass", {})
                require(second.get("wide_context_reviewed") is True
                        and second.get("agrees_with_exercise_id") is True
                        and second.get("setup_adjustment_ruled_out") is True,
                        "novel or one-off exercise needs an agreeing wide-context second pass")
        else:
            require(audit["intent"] != "working_set", "non-set evidence conflicts with context intent")
            require(e.get("set") is None, "non-set evidence must not carry a set number")
    if evidence_version >= 3 and is_set:
        context_mode = review.get("count_context")
        require(context_mode in ("source_review", "blind_source_review"), "count_context required")
        exposed = review.get("expected_totals_exposed")
        require(isinstance(exposed, bool), "expected-total exposure must be explicit")
        if context_mode == "blind_source_review":
            require(exposed is False and review.get("isolation_basis"), "blind claim needs isolated context")
        boundary = review.get("boundary_pass", {})
        require(boundary.get("locked_before_counting") is True
                and boundary.get("segments") == segments and boundary.get("observation"),
                "boundary pass must lock these source segments before counting")
        forward = {event["id"]: event for event in e["events"] if event["status"] == "complete"}
        reverse = review.get("reverse_events", [])
        require(len(reverse) == len(forward), "reverse audit must cover every complete cycle")
        matched = set(); previous_starts = {}
        for audit_event in reverse:
            eid = audit_event.get("event_id")
            require(eid in forward and eid not in matched, "reverse audit omitted/duplicated cycle")
            event = forward[eid]; refs = audit_event.get("frames", [])
            require(len(refs) == 3 and len(set(refs)) == 3 and audit_event.get("observation"),
                    "reverse audit needs inspected phase triplet and observation")
            times = [position(ref) for ref in refs]
            require(times[0] < times[1] < times[2], "reverse phase refs remain chronological")
            ft = [position(ref) for ref in event["frames"]]
            require(all(abs(a-b) <= .5 for a,b in zip(times,ft)), "reverse phases do not match forward cycle")
            side = event.get("side", "both")
            require(audit_event.get("side", "both") == side, "reverse side mismatch")
            require(times[0] < previous_starts.get(side, float("inf")), "reverse audit must run end to start")
            previous_starts[side] = times[0]; matched.add(eid)
    conf = e.get("confidence", "medium")
    require(conf in ("high", "medium", "low"), "invalid confidence")
    require(conf != "high" or (e["coverage_complete"] and not counts["uncertain"]), "high confidence conflicts with missing/uncertain evidence")
    return {"evidence_version": evidence_version, "set_id": e["set_id"], "exercise_id": e["exercise_id"], "set": e.get("set"),
            "completed": completed, "partial": counts["partial"], "uncertain": counts["uncertain"],
            "reps_per_side": side_counts if mode == "per_side" else None,
            "excluded": counts["excluded"], "coverage_complete": e["coverage_complete"],
            "confidence": conf, "disposition": "working_set" if is_set else "non_set",
            "segments": segments, "sources": sources, "viewed_assets": assets}


def validate_manifest(path):
    path = Path(path).resolve(); m = read(path)
    require(m.get("schema_version") == 4, "legacy manifest is not structured evidence; use new receipts")
    candidates = m.get("candidate_sets", [])
    require(len(candidates) == len(set(candidates)), "duplicate candidate ids")
    require(set(candidates) == set(m.get("evidence", {})), "candidate sets missing structured evidence")
    results = {}; assets = {}
    for sid, entry in m.get("evidence", {}).items():
        p = local(path.parent, entry["path"])
        require(p.is_file() and digest(p) == entry["sha256"], "registered evidence changed; re-register after review")
        result = validate_evidence(p)
        require(result["set_id"] == sid, "evidence set id mismatch")
        results[sid] = result; assets.update(result["viewed_assets"])
    for asset in m.get("visible_pages", {}).values():
        p = local(path.parent, asset["path"])
        require(p.is_file() and digest(p) == asset["sha256"], "registered page changed")
        assets[str(p)] = asset
    budget = m.get("budget", {})
    frames = sum(a["frames"] for a in assets.values()); pixels = sum(a["pixels"] for a in assets.values())
    require(frames <= budget.get("max_frames", 3000) and pixels <= budget.get("max_pixels", 300000000), "visual frame/pixel budget exceeded")
    derived = {sid: r["completed"] for sid, r in results.items() if r["disposition"] == "working_set"}
    require(m.get("final_counts", {}) == derived, "manifest final counts differ from event records")
    return results, {"model_visible_contact_sheets": len(assets), "model_visible_frames": frames,
                     "model_visible_pixels": pixels, "candidate_sets": len(candidates),
                     "source_verified_sets": len(results), "final_counts": len(derived)}


def validate_report_evidence(data, json_path, allow_legacy=False):
    name = data.get("evidence_manifest")
    if not name:
        require(allow_legacy and data.get("schema_version", 2) <= 2,
                "structured evidence_manifest required; historical rendering needs explicit --allow-legacy")
        data["_verification_notice"] = "历史兼容视图：未按新版逐次证据规则验证，不代表重新认证。"
        return
    results, cost = validate_manifest(local(Path(json_path).parent, name))
    active = {sid: r for sid, r in results.items() if r["disposition"] == "working_set"}
    if data.get("schema_version", 0) >= 5:
        require(all(r["evidence_version"] >= 3 for r in active.values()),
                "schema-5 reports require v3 boundary/context/reverse evidence")
    summaries = data["exercises"]
    exercise_ids = [s.get("exercise_id") for s in summaries]
    require(len(exercise_ids) == len(set(exercise_ids)) and set(exercise_ids) == {r["exercise_id"] for r in active.values()},
            "report summary exercise identities differ from evidence")
    for summary in summaries:
        sets = sorted((r for r in active.values() if r["exercise_id"] == summary["exercise_id"]),key=lambda r:r["set"])
        reps = [r["completed"] for r in sets]
        require(summary.get("reps") == reps and summary.get("total") == sum(reps) and summary.get("sets") == len(reps),
                "report per-set summary differs from evidence")
    rows = data["timeline"]; ids = [r.get("evidence_set_id") for r in rows]
    require(len(ids) == len(set(ids)) and set(ids) == set(active), "report omits/duplicates evidence sets")
    report_sources = {s["file"]: s for s in data["sources"]}
    for row in rows:
        r = active[row["evidence_set_id"]]
        require(row["exercise_id"] == r["exercise_id"] and row["set"] == r["set"], "report exercise/set mapping mismatch")
        for field in ("completed", "partial", "uncertain"):
            require(row.get(field, 0) == r[field], f"report {field} differs from event evidence")
        require(row.get("reps_per_side") == r["reps_per_side"], "report per-side counts differ from evidence")
        require(row.get("confidence") == r["confidence"], "report confidence differs from evidence")
        segs = row.get("segments") or [row]
        normalized = [{k: s[k] for k in ("file", "start_s", "end_s")} for s in segs]
        require(normalized == r["segments"], "report boundaries differ from evidence")
        require(all(row[k] == normalized[0][k] for k in ("file", "start_s", "end_s")), "report legacy boundary mismatch")
        for file, source in r["sources"].items():
            require(file in report_sources and all(report_sources[file].get(k) == source[k] for k in ("path", "fingerprint")), "report source identity mismatch")
        uncertainty = f'已确认{r["completed"]}次；待定{r["uncertain"]}次'
        if not r["coverage_complete"]:
            uncertainty += "；区间覆盖尚未完整，最终总数待核查"
        row["note"] = (row.get("note", "") + " " + uncertainty).strip()
        row["coverage_complete"] = r["coverage_complete"]
    if any(r["uncertain"] or not r["coverage_complete"] for r in active.values()):
        require(data["report"].get("overall_confidence") != "high", "overall high confidence conflicts with uncertain sets")
        data["_incomplete_counts"] = True
        for exercise in data["exercises"]:
            if any(r["exercise_id"] == exercise["exercise_id"] and (r["uncertain"] or not r["coverage_complete"]) for r in active.values()):
                require(exercise.get("confidence") != "high", "exercise high confidence conflicts with uncertain sets")
    data["analysis_manifest"] = {**data.get("analysis_manifest", {}), **cost}
    data["_verification_notice"] = "逐次记录、源片映射与帧文件完整性已校验；动作语义由视觉复核负责。"
    if data.get("_incomplete_counts"):
        data["_verification_notice"] += " 本报告总数为已确认次数下限，仍有待定周期或未覆盖区段。"
