# Report schema

## New report evidence requirement

New reports use `schema_version: 5`, a top-level `evidence_manifest` path relative to the report JSON, v3 event evidence, and `evidence_set_id` on every timeline row. Read [count-evidence.md](count-evidence.md) before constructing them. Every count, source fingerprint, segment and confidence is revalidated from per-cycle frame evidence at render time. Add numeric `uncertain` alongside `completed` and `partial`; unresolved coverage is displayed as a lower bound, not an exact total. Schemas 3 and 4 remain readable for existing reports.

The schema-v2 example below documents historical field compatibility only. Render pre-existing reports explicitly with `--allow-legacy`; the page displays a history/unverified notice. Never use that flag to label a new analysis as verified.

Write one UTF-8 analysis JSON file. In projects with `data/workouts/` and an existing workout-page renderer, the existing `workouts/<workout_id>.html` is the sole user-facing deliverable: validate and render the full analysis into that page through the project integration renderer. In a compatible project use `python3 scripts/render_workout_review.py INPUT.json form-reviews/REPORT.html`; it validates the JSON, expands the existing workout page and turns the old report URL into a compatibility redirect. Keep the JSON and evidence as data; do not send a separate report link.

Only projects without a workout-page workflow use the standalone renderer:

```bash
scripts/render_html_report.py INPUT.json OUTPUT.html
```

The following synthetic example illustrates historical field layout only, not new evidence or any user record:

```json
{
  "schema_version": 2,
  "report": {
    "title": "2000-01-01 训练视频分析",
    "date": "2000-01-01",
    "session_label": "完整居家训练",
    "overall_confidence": "high",
    "summary": "一句话结论",
    "timing_note": "普通区间约 ±0.25 秒；问题点由源帧复核"
  },
  "sources": [
    {"file": "fixture-a", "path": "/absolute/source.mp4", "duration_s": 30, "fingerprint": "source receipt fingerprint"}
  ],
  "exercises": [
    {
      "exercise_id": "pushup.standard",
      "name": "俯卧撑",
      "sets": 2,
      "reps": [8, 7],
      "total": 15,
      "confidence": "high",
      "problem_gate": {
        "basis": "visible_technique_failure",
        "criterion": "head and trunk descend together",
        "observed_violation": "head moves forward before the trunk",
        "observed_effect": "neck position changes while the torso remains at the same height"
      },
      "guide_url": "../exercise-guides.html#pushup",
      "video_url": "https://www.youtube.com/results?search_query=push+up+proper+form+tutorial",
      "video_id": "_l3ySVKYVJ8"
    }
  ],
  "timeline": [
    {
      "file": "fixture-a",
      "start": "00:02",
      "end": "00:08",
      "start_s": 2,
      "end_s": 8,
      "segments": [
        {"file": "fixture-a", "start": "00:02", "end": "00:08", "start_s": 2, "end_s": 8}
      ],
      "stitched_start_s": 2,
      "stitched_end_s": 8,
      "exercise": "俯卧撑",
      "exercise_id": "pushup.standard",
      "set": 1,
      "completed": 7,
      "partial": 0,
      "reps_per_side": null,
      "execution_mode": "bilateral_synchronous",
      "confidence": "high",
      "note": "周期两相一致"
    }
  ],
  "issues": [
    {
      "issue_id": "pushup.neck_reach",
      "file": "fixture-a",
      "timestamp": "00:07",
      "timestamp_s": 7,
      "exercise": "俯卧撑",
      "set": 1,
      "phase": "后段最低位",
      "observation": "头颈先向前探，腰髋线条下降",
      "scan_range": "00:05-00:08，10 fps + 源帧",
      "confidence": "high",
      "correction": "后颈拉长、收腹轻夹臀，让胸整体下降",
      "image": "/absolute/or/json-relative/problem.jpg",
      "gif": "/absolute/or/json-relative/problem.gif",
      "guide_url": "../exercise-guides.html#pushup",
      "video_url": "https://www.youtube.com/results?search_query=push+up+proper+form+tutorial",
      "video_id": "_l3ySVKYVJ8"
    }
  ],
  "next_session": ["下次只保留 1-3 个最重要提示"],
  "cost": {
    "local": "元数据、运动曲线和算术",
    "coarse_visual": "6 张接触表",
    "verification_visual": "4 个密扫窗口",
    "retained": "5 张问题证据图"
  },
  "analysis_manifest": {
    "generated_contact_sheets": 14,
    "model_visible_contact_sheets": 14,
    "dense_windows": 2,
    "decode_attempts": 1,
    "failed_extra_methods": 0,
    "candidate_sets": 8,
    "source_verified_sets": 8,
    "final_counts": 8,
    "max_model_visible_contact_sheets": 24
  }
}
```

Rules:

- Keep `sets == len(reps)` and `total == sum(reps)`; the renderer rejects inconsistent totals.
- Require the per-exercise summary to match the grouped timeline exactly: exercise names, set counts, completed totals, and contiguous set numbers.
- Use stable `exercise_id` and `issue_id` values from the project catalogs. Human-facing names may change; IDs must remain stable across models and revisions.
- Require `report.date` to be a real calendar date in `YYYY-MM-DD` format. For standalone reports under `form-reviews/`, use the same filename stem for the JSON and HTML, for example `2000-01-01-workout-report.json` and `2000-01-01-workout-report.html`.
- Supply numeric `start_s`, `end_s`, and source `duration_s` so the HTML can draw proportional timeline bars.
- A set crossing source files remains one timeline row. Add ordered `segments`, each with its original `file`, display and numeric start/end. The renderer draws one block per segment but counts the row only once. Keep the legacy top-level file/time fields equal to the first segment for backward compatibility; optional `stitched_start_s` and `stitched_end_s` describe the unified proxy clock.
- For shoulder press, add `execution_mode` (`bilateral_synchronous`, `alternating_other_at_shoulder`, or `alternating_other_overhead`) and `reps_per_side` when visible. The exercise summary total is the reported per-side set count, not left plus right.
- Copy actual analysis-run counters into `analysis_manifest`; never substitute estimated token usage.
- Before copying totals, make the manifest pass `--assert-final-ready`. Every reported set must be registered as a candidate, verified in a source-local 8-12 fps window, and assigned a final count. A keyframe or uniform coarse proxy is never count evidence.
- Require `image`, `correction`, `timestamp`, and `scan_range` for every confirmed issue. In schema 4 also require `problem_gate.basis`, `criterion`, and `observed_violation`. `basis` is `visible_technique_failure` or `user_reported_symptom`; the former requires `observed_effect`, and the latter requires `user_report_reference`. Environment or material descriptions alone are observations, not issues. Confirmed evidence must be a retained local file. Add `gif` after local GIF generation; retain `image` as the still-frame fallback.
- Reuse the project's `dashboard.html` mappings for `guide_url`, `video_url`, and `video_id`. For an unknown exercise, use a YouTube search URL and omit `video_id` until a specific demonstration is verified.
- Use one deterministic exercise-color mapping for both timeline bars and the visible legend; every displayed timeline color must have a full exercise-name label.
- Render a verified `video_id` as a direct `https://www.youtube.com/watch?v=VIDEO_ID` link. Under the project's standard `file://` bookmark, the primary tutorial action opens that direct page without showing setup instructions. An optional HTTP view may embed `youtube-nocookie.com` in the card, but report usability must not depend on it.
- Keep links relative to the output HTML when possible. The renderer automatically rewrites local issue image paths relative to the output file.

## Durable data handoff

The report JSON is the verified session-analysis input. In a standard long-term fitness project, migrate it into the durable schema-v2 session store after report validation. Preserve stable IDs, per-set times, source paths and fingerprints, issue evidence, user feedback, pipeline/model metadata, and append-only revisions. Treat HTML, dashboard indexes, coach snapshots, statistics databases, CSV exports, and Markdown summaries as rebuildable derivatives.

## Dashboard history index

The report index is derived from analysis JSON and contains summaries and ownership mappings only. In a workout-page project, the dashboard links to `workouts/<workout_id>.html`; that same workout document renders the full timeline, GIFs, corrections and cost. Old report URL entries are internal mappings or compatibility redirects, not additional destinations. Projects without a workout-page workflow may retain standalone report links.

## Markdown fallback

Use the following only as a compact supporting note when needed.

Use concise tables. Preserve original per-file timestamps so the user can seek directly.

## Session summary

```markdown
| Exercise | Sets | Completed reps by set | Total | Confidence |
|---|---:|---|---:|---|
```

## Precise timeline

```markdown
| File | Start-end | Exercise | Set | Completed | Partial | Confidence | Evidence note |
|---|---|---|---:|---:|---:|---|---|
```

Rules:

- Use `HH:MM:SS.mmm` only when derived from source presentation timestamps. Otherwise use the resolution supported by the sampling interval and state the uncertainty.
- For variable-frame-rate media, retrieve source timestamps near a candidate with `ffprobe -select_streams v:0 -read_intervals START%+DURATION -show_frames -show_entries frame=best_effort_timestamp_time,pkt_duration_time -of csv=p=0 INPUT` before making frame-accurate claims.
- Keep source-relative timestamps. If also giving a stitched session clock, label it separately.
- Record transitions, rest, camera adjustment, and unclassified activity only when useful for navigation or set boundaries.
- Put ambiguous exercise labels in brackets with alternatives and say what evidence would distinguish them.

## Problem-event confirmation

```markdown
| Timestamp | Exercise/set/rep | Phase | Confirmed observation | Scanned source range/cadence | Confidence | Cue |
|---|---|---|---|---|---|---|
```

For each event, retain one representative frame only when it materially supports the finding. Describe the observation narrowly, for example “hip rises before shoulders during the concentric phase,” rather than assigning a diagnosis.

## Cost note

Report:

- coarse pass: number of model-visible contact sheets or frames;
- verification pass: number of short clips or dense frame windows;
- local work: metadata/motion computations, which use no model tokens;
- resolution and number of scan stages.

Do not claim an exact token count or multiplier unless the execution environment exposes measured usage.
