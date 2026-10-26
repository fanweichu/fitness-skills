# Workout pages and period reports

Project implementation: `scripts/training_pages.py`. Run commands from the chosen fitness project. `fitness_data.py rebuild` renders all workout pages and the dashboard after refreshing the long-term data.

## Workout document

Save a JSON input outside `data/workouts/`, then use the command below. The tool merges top-level fields into an existing document and archives its previous revision. When updating `review`, send the whole new review object; omitting `plan` preserves it.

```json
{
  "workout_id": "2000-01-01",
  "date": "2000-01-01",
  "title": "全身训练",
  "plan": {
    "estimated_duration": "约 55 分钟，包含热身、休息与拉伸",
    "summary": "本次调整及依据。",
    "blocks": [
      {
        "title": "正式训练",
        "note": "动作顺序、循环方式和休息。",
        "items": [
          {
            "name": "动作名",
            "dose": "组数 × 次数；重量；休息",
            "cue": "一条本次适用的关键提示。",
            "video_url": "https://www.youtube.com/results?search_query=exercise+tutorial"
          }
        ]
      }
    ],
    "notes": ["时间不足先省略哪项", "必要的训练后反馈"]
  }
}
```

Use the actual date; this is a schema illustration, not a prescribed workout. A workout ID is `YYYY-MM-DD` or `YYYY-MM-DD-suffix` for separate same-day workouts.

```bash
python3 scripts/training_pages.py --project . save-workout --input /absolute/path/input.json
python3 scripts/fitness_data.py --project . rebuild --last 6
```

After training, a partial update can contain:

```json
{
  "workout_id": "2000-01-01",
  "date": "2000-01-01",
  "session_ids": ["2000-01-01-001"],
  "review": {
    "summary": "基于已归档实际训练的结论。",
    "priorities": ["本次有证据支持的一条提示"],
    "next_time": "下一次的具体调整。",
    "feedback": "用户反馈，保留实际含义和时效。"
  }
}
```

Only link canonical session IDs that exist and belong to this workout. `session_ids` explicitly binds reports; without it the single workout for that date receives canonical records of that date. Multiple same-day workouts need explicit links. Historical records render by date without changing the canonical JSON. `review` alone is feedback, not counted as a completed training session. Known actual sets must also enter canonical records; never infer performed sets from a plan.

The renderer rebuilds `workouts/<id>.html`, `dashboard.html`, and `derived/workout-index.json`. HTML has inline CSS and no fetch/server dependency. It displays the plan, canonical actuals, full analysis, timeline, GIF evidence and cost in that same document. Detailed report JSON is a data source, not a separate user destination. In this project, `scripts/render_workout_review.py` validates the analysis, sets `review.analysis_layout` to `inline`, and rebuilds the existing workout page; any old report HTML becomes a redirect. Deliver only the workout URL. Do not hand-edit generated HTML.

## Period reports

```bash
python3 scripts/training_pages.py --project . period --start YYYY-MM-DD --end YYYY-MM-DD
```

Read `derived/period-reports/<start>_<end>.json` for exact totals and per-variation set histories. Then write an analysis JSON with `summary` and `next_steps` strings and run:

```bash
python3 scripts/training_pages.py --project . period --start YYYY-MM-DD --end YYYY-MM-DD --analysis /absolute/path/analysis.json
```

The analysis is stored in `data/period-reports/`, previous versions under `data/revisions/period-reports/`, and the user report in `reports/`. Rebuild refreshes statistics and flags prose whose underlying canonical revisions changed. Never count plans as completed sessions, combine all-exercise repetitions as a progress score, or equate a missing entry with a missed workout.

## Checks

Check a planned-only page, the same URL after canonical actuals + review are added, the dashboard link, and a period report that excludes planned-only workouts. Check local relative links and a narrow viewport. Preserve canonical data revisions and old detailed evidence. Keep archives inside the chosen project, outside the shared skill.

## Actual browser rendering

Use an available permitted Playwright runtime for actual rendering. `scripts/verify_pages.cjs` launches an isolated temporary Chrome with Chromium sandbox enabled, network requests aborted, offline mode and only local page JavaScript enabled. It loads authorized HTML bytes through `setContent`; it does not navigate to `file://`, use the user's browser profile or require a server.

Run with the bundled Node executable (discover its path with workspace dependencies). If process sandboxing prevents Chrome from launching, use the normal escalation mechanism rather than disabling Chromium sandboxing. Inspect the generated screenshots under `the selected QA output directory`; source checks alone do not establish visual correctness. The script tests desktop/mobile layout, clipped tables, video entries and review disclosure. Update the selected plan/review examples when the test target changes. This verifies rendering and the tested interactions, not external video playback or file-protocol navigation.

## Dated corrections

Use the existing workout ID and `save-workout` with the complete `corrections` list, preserving prior entries. Each entry requires nonempty `id`, `date`, `scope`, `superseded`, `replacement`, and `basis`. This is a versioned overlay; do not rewrite the original plan as if the revised advice had been given before training. The rendered correction takes precedence and the read-only context includes it. Confirm the page and context after saving. An invalid or duplicate correction ID is rejected before any revision is written.
