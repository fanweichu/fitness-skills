#!/usr/bin/env python3
"""Initialize an empty user-selected project without replacing existing files."""
import argparse
import json
from pathlib import Path
import shutil

RUNTIME = ('fitness_data.py', 'training_pages.py', 'coach_context.py', 'skill_paths.py',
           'laterality.py', 'render_workout_review.py', 'training_theme.css', 'review_theme.css', 'verify_pages.cjs')


def initialize(project):
    project = Path(project).resolve()
    payloads = {'data/coach-profile.json': {},
                'data/catalogs/exercises.json': {'exercises': []},
                'data/catalogs/issues.json': {'issues': []},
                'data/review-policy.json': {'schema_version': 1, 'require_working_side_review': True,
                                            'standing_corrections': [], 'historical_reports': {}}}
    destinations = [project/'scripts'/name for name in RUNTIME] + [project/name for name in payloads]
    if any(p.exists() for p in destinations):
        raise ValueError('existing project files found; use the existing runtime instead of initialization')
    for directory in ('scripts','data/workouts','data/sessions','data/revisions','data/catalogs',
                      'derived','form-reviews','review-assets','workouts','logs'):
        (project/directory).mkdir(parents=True, exist_ok=True)
    for name in RUNTIME:
        shutil.copy2(Path(__file__).parent/name,project/'scripts'/name)
    for name,data in payloads.items():
        (project/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    return project


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__);p.add_argument('--project',type=Path,required=True)
    print(initialize(p.parse_args().project))
