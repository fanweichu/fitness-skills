---
name: analyze-workout-video
description: Analyze local workout videos without user annotations, verify exercises and per-set repetitions from source frames, and archive traceable technique evidence on an offline workout page. Also revisit a specific video interval for a technique question. Not for generic fitness advice, playback or transcoding alone.
---

# Analyze Workout Video

Use the user's supplied media and selected output project. Never copy full originals into the project. Resolve this skill's installed directory once as `SKILL_DIR`; every bundled script path is relative to that directory, while cwd remains the project. Profiles, original media paths, credentials, local permissions and past training records are project data, never distributable skill assets.

## Choose the scope before loading the pipeline

- **Specific question / follow-up:** use existing receipts and only the relevant source interval. Do not recount unrelated sets. If a previous conclusion changes, preserve the old evidence and update the affected report/review with a dated correction. In a one-page workout project, verify that correction on the same page and in the coach context. No recorded change means no migration or rebuild.
- **Full new analysis:** read [full review](references/full-review.md), then [count evidence](references/count-evidence.md) before extraction. Read [report schema](references/report-schema.md) when building the final report. These retain the classification, source coverage, problem gate and bounded visual budget requirements.

## Stable execution entrypoints

Run `python3 SKILL_DIR/scripts/review_runtime.py preflight` once. It reports FFmpeg/FFprobe and current Python image/signal dependencies; select an available compatible runtime rather than installing packages or embedding a machine path. The wrapper resolves sibling scripts without relying on project cwd.

Original media is decoded only through `workout_video_decode.py prepare|window`. Honor the host's permission model; use the exact bounded entrypoint when escalation is needed, never a broad FFmpeg/Python allow rule. Outputs stay under the project or system temporary directory. Preserve fingerprints, completion markers, source PTS and cross-file segment maps. Coarse proxies are navigation only. Bounded windows check display geometry and normalize before publishing a completion marker; their cache keys include this version.

Use `review_runtime.py extract WINDOW OUTPUT_DIR --file LABEL --start SECONDS --end SECONDS` for validated frame pagination. Its `register` command records only explicitly selected, actually inspected pages in the existing manifest. Extraction does not imply viewing. Do not hand-build repeated extraction/registration scripts, squeeze entire sets into oversized sheets, reset budgets, or repeat an unchanged model pass.

## Evidence and honest independence

Exact counts require every set's own complete boundary–opposite–boundary cycles, an observed work interval excluding setup, full coverage and a reverse audit. Never infer a count from time, cadence, motion peaks, target reps or a neighboring set. Partial and uncertain cycles remain separate; incomplete coverage gives a lower bound.

Default to `source_review` if the current conversation contains targets, prior counts or corrections. Do not claim to forget them. Only an actually isolated context with no expected totals can claim `blind_source_review`, with its isolation basis recorded. Do not automatically spawn agents for this. Compare user-reported counts only after source events are locked; retain both provenances.

New event evidence is v3: explicit context exposure, boundary decision and per-cycle reverse records are validated. New reports use schema 5. Old evidence/reports remain historical and readable; do not backfill new assertions or exempt new reports to make checks pass. Passing a validator proves file identity, coverage and arithmetic, not exercise semantics or mental independence.

Classify from full body, persistent support contacts, load path and exclusion of the nearest look-alike. Confirm working intent before counting. Determine anatomical working side per set from source evidence; never use screen position, plan order or previous sessions. A hidden side stays unresolved with its exact source interval and reason.

A material, padding, rolling object or unusual silhouette alone is not a technique failure. Require a visible movement/control failure or a relevant user symptom before labeling a problem. Keep lower-severity actionable technique focuses separate. Each surfaced issue/focus needs dated source mapping, side/set, screenshot and GIF when the source is available. Do not diagnose. Give supported feedback in the order: immediate risk, plausible target loading, up to three useful cues, next filming requirement.

## Deliver and preserve

Register all candidate dispositions and inspected page costs, then run `analysis_manifest.py MANIFEST --assert-final-ready` and the strict report validator. Stop at the configured frame/pixel budget with explicit unresolved coverage; do not fabricate precision. Record actual inputs and elapsed processing where measured, not invented token counts.

If the chosen project already has a workout page, associate canonical records with it, preserve the plan, render actuals/timeline/GIFs/cost inside that same document, and deliver only its URL. Use the project's integration tools and versioning rules. Otherwise use the bundled standalone report renderer. Rebuild after data changes, and verify the affected page and local evidence links. Historical source loss does not erase preserved observations; label unavailable GIFs explicitly.
