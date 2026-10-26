# Durable Fitness Data Contract

## Source of truth

`data/sessions/YYYY-MM-DD-NNN.json` is the canonical record. Each record includes:

- `schema_version`, `session_id`, `revision`, `date`, timezone, status
- source file paths and lightweight fingerprints
- normalized exercises with stable IDs and set-level counts
- normalized technique issues with stable IDs and local evidence paths
- user feedback fields for RIR, pain, energy, sleep, and notes
- pipeline version and import provenance

Existing canonical records are immutable in place from the user's perspective. A correction first archives the prior record as `data/revisions/<session-id>-rNNN.json`, then increments the active record's revision.

## Catalogs

- `data/catalogs/exercises.json`: canonical names, aliases, movement families, unilateral status, and guide anchors.
- `data/catalogs/issues.json`: stable issue names, fixed coaching cues, stop rules, and exercise scope.
- `data/workouts/*.json`: training-before prescriptions and concise follow-up reviews, versioned through `training_pages.py save-workout`.
- `data/coach-profile.json`: current equipment, goals and lasting constraints.
- `data/plans/*.json`: legacy long-term plan metadata; not a performed-session source.

Names shown in historical reports may change; IDs must remain stable. Add a new alias instead of rewriting old reports.

## Derived views

These files are replaceable outputs and must never be treated as the only copy:

- `derived/coach-snapshot.json`: bounded machine-readable statistics.
- `derived/coach-state.json`: compact coach state including dated feedback, profile and recent plans; rebuild also refreshes the bookmarkable dashboard and workout pages.
- `derived/training-sets.csv`: set-level export for spreadsheets and long-term analysis.
- `form-reviews/report-index.json`: report links and summaries for the dashboard.
- `workouts/<workout_id>.html` and `dashboard.html`: offline pages generated from workout documents and canonical actuals.
- `reports/` and `derived/period-reports/`: requested period reports and statistics, derived from canonical actuals plus versioned analysis in `data/period-reports/`.

Regenerate them with:

```bash
python3 scripts/fitness_data.py --project PROJECT rebuild --last 6
```

## Required invariants

- Summary set counts and repetitions reconcile exactly with the set timeline.
- Every known exercise and issue uses a catalog ID.
- Every technique issue has a source file, timestamp, and local screenshot or GIF when the source remains available.
- A missing source video is a warning, not permission to discard confirmed historical data.
- Unknown subjective values remain `null`; they are not inferred from video.
- Changes preserve the previous revision.

## Model boundary

Scripts own arithmetic over visually verified events, aggregation, aliases, recurrence, audit, export, and priority scoring. They do not independently perceive repetitions. The model owns explanation, bounded prioritization, and plan judgment. Any new fact that can be expressed as a stable rule should be moved into the deterministic pipeline instead of being recalculated in prose.

## Coaching feedback and grouping

Recent general effort reports must be included in coaching context with date and scope, even if set-level RIR is null. Preserve them without inventing historical per-set values. Same-day warm-up, main training and stretching can share a workout; explicit workout IDs and session links distinguish multiple workouts on one date. Count actual training groups, never report files or unexecuted plans.

Read current context without side effects using `fitness_data.py --project PROJECT context --last 6`. `derived/coach-context.json` is a rebuildable copy; direct context reads reflect canonical updates immediately. Workout-level `corrections` persist independently of the recent-session window.
