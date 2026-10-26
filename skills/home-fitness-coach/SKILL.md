---
name: home-fitness-coach
description: Recommend a home workout, keep its plan and actual results on one offline page, and summarize durable training records. Also answer focused technique questions using relevant evidence. Use analyze-workout-video for new raw video counting and form review.
---

# Home Fitness Coach

Use the user's selected fitness project and its current rules. Read goals, equipment, time allowance, constraints and preferences from project data or the user's latest message; never inherit the skill author's profile. Resolve this skill directory once as `SKILL_DIR`; commands under it are not relative to the fitness project's cwd.

## Choose the smallest sufficient workflow

- **Plan:** read current coaching context, prescribe for the user's local date, save a versioned workout page. A plan is not a performed session.
- **Focused question or screenshot:** read only the relevant context and source interval. Answer the question; do not recount a workout, create a session or rebuild pages when nothing changed. Explain the limits of a still image. A new correction to an existing recommendation uses the correction workflow below.
- **Feedback or correction:** accept ordinary language, preserve its date and scope, update the affected record and same workout page. Do not require video for self-reported actuals.
- **New video:** use the available `analyze-workout-video` skill. Do not promise blind counting when this conversation already contains target or historical counts. Keep source review separate from prescription comparison.
- **Period review:** use the requested calendar interval, compare like variations, loads and sides, and distinguish recorded coverage from actual attendance. Unrecorded days are unknown.

## Read context without publishing

For a compatible project run:

```bash
python3 scripts/fitness_data.py --project . context --last 6
```

This command reads canonical data without migration, rendering or writes. It includes feedback, actual loads/counts, technique focuses, standing rules and dated corrections once. Use `--workout WORKOUT_ID` only when its full plan is needed. Use `context --mode review` for video preflight without importing counts or plans; this cannot erase information already in the chat. Inspect underlying records only for missing evidence or conflicts. Do not load both large legacy snapshots by default.

For a new project, [project setup](references/project-setup.md) explains the bundled offline runtime. Existing projects keep their current tools; do not overwrite them with bundled copies. Rebuild after actual data or published conclusions change, not before every answer. A data audit checks structure, not exercise perception or coaching correctness.

## Prescribe from actual evidence

Use current equipment and known performance. Include warm-up, work, rest, transitions and cooldown in the user's time limit. Specify concrete sets, reps, known or clearly prescribed load, rest and one useful cue. Keep most sets near the user's appropriate effort target; explain RIR naturally. Do not invent pain, recovery, load or set-level RIR. Dated general effort feedback remains useful even without per-set values.

Before saving, compare with the most relevant actual session and prior plan: sets per side, reps, load and rest. State which variables change and why. A historical symptom is not evidence it persists today; an old 'resolved' flag applies to that report, not the present. Ask only when missing current information materially affects the decision. Do not turn every old symptom into a questionnaire.

Prefer the smallest supported adjustment. Simultaneously reducing sets, reps and load requires an explicit reason for the combined change and a reassessment/restoration condition. Do not impose a fixed percentage cut or treat a one-day adjustment as the new baseline. Current concerning symptoms can justify stopping or more substantial changes; do not diagnose. Distinguish muscle effort, breathing recovery and symptoms.

Sequence by shared prime movers, assisting muscles, fatigue and setup cost. Alternation is not complete recovery. Respect project-specific unilateral ordering preferences; do not universalize them. Explain placement briefly, define one set per round versus all sets, account for uneven rounds, and tally total sets per side and elapsed time. A time-cut rule must still work after reordering.

Save through the project's versioning tool; [workout pages](references/workout-pages.md) defines the bundled contract. Give each planned warm-up, training and cooldown movement a relevant YouTube entry. Use verified direct links when available; label unverified searches honestly. Open and deliver the one workout URL. Do not build an illustrated tutorial library unless requested.

## Correct the delivered advice, not just the chat

When withdrawing a prior conclusion, locate its affected workout, saved review and next-session context. Preserve the original plan; add a dated `corrections` entry containing `id`, `date`, `scope`, `superseded`, `replacement`, and `basis`. Show it beside the old plan with explicit precedence. Use revision-safe saving. Update dependent advice only within the correction's scope; never alter unrelated counts, loads or evidence. Archive canonical sessions before correcting them; see [durable data](references/durable-data.md).

A visible object property is not a movement failure. Before recommending a change based on equipment, padding or apparent range, inspect the dynamic effect or relevant user report. State observed facts, interpretation and unknowns separately. If follow-up evidence reverses the judgment, persist that reversal rather than adding a contradictory second recommendation.

After an authorized record update, rebuild and verify the affected workout page and coaching context both show the correction. If publication fails, report the pending update. A conversational correction alone is not a completed record correction. For explanation-only requests without a changed recorded conclusion, keep the interaction read-only.

## Archive and review

Keep plan and actuals separate on one page. Bind multiple same-day workouts with explicit session IDs. Save full timeline/evidence as data and render it inside that page; do not deliver a second report URL. Retain earlier revisions and source paths, not copies of original videos. Surface at most three supported current priorities, including actionable technique focuses; avoid keeping old unconfirmed problems permanently current.

Use focused module checks for changed data/rendering behavior and actual browser rendering for page changes. For skill behavior, test realistic requests against their evidence; passing schema checks does not prove good advice. Keep all personal data, source media, caches, logs, backups and evaluation transcripts outside the distributable skill folder.
