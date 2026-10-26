# Portable project setup

The skill contains the offline project runtime in `scripts/`. It does not contain anyone's profile, records, evidence, videos or permissions. Resolve the installed skill path from its catalog entry, not a fixed home directory.

For a new user-selected project only:

```bash
python3 SKILL_DIR/scripts/init_project.py --project PROJECT
```

The initializer creates empty data folders, catalogs, a profile and default side-review policy, and copies the runtime into `PROJECT/scripts/`. It refuses to overwrite any existing destination file. Collect missing goals, equipment, constraints and time allowance as needed; empty defaults are not recommendations. The user or host supplies the current local date/timezone.

For an existing compatible project use its scripts. Do not initialize or overwrite it. `context` is read-only and works before the first recorded workout. Save/render a plan with `training_pages.py`; after canonical actuals exist use `fitness_data.py rebuild` for migration/audit/snapshots/CSV/pages. `analyze-workout-video` is optional for text-only coaching; install it beside this skill or set `WORKOUT_SKILL_DIR` when importing video reports.

Python 3.10+ is needed for the project runtime. The video skill additionally needs FFmpeg/FFprobe and the modules documented by its preflight command. For browser checks use an available Node/Playwright runtime; `verify_pages.cjs --project PROJECT --file workouts/WORKOUT_ID.html --output QA_DIR` verifies only selected pages. Set `PLAYWRIGHT_MODULE` or `CHROME_EXECUTABLE` if discovery is needed. Do not copy the source author's runtime paths or browser permissions.
