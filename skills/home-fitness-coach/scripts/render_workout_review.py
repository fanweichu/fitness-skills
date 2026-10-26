#!/usr/bin/env python3
"""Strict skill report renderer with this project's traceable technique focuses."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
import laterality

from training_pages import (esc, technique_focus_html, theme_review_html,
                            observation_evidence_html, collect, write,
                            save_workout, render)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--project', type=Path, default=Path.cwd())
    args = parser.parse_args()
    source, output = args.input.resolve(), args.output.resolve()
    project = args.project.resolve()
    skill = __import__('skill_paths').video_skill()
    sys.path.insert(0, str(skill / 'scripts'))
    spec = importlib.util.spec_from_file_location('strict_workout_report', skill / 'scripts/render_html_report.py')
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    data = json.loads(source.read_text())
    laterality.validate_report(project, data, source)
    renderer.validate(data, source, output)
    if (project / 'data/workouts').is_dir() and output.parent == project / 'form-reviews':
        # Check canonical/report agreement before changing even the report index.
        collect(project)
        renderer.write_report_index(output.parent, data, output.name)
        matches = [g for g in collect(project) if any(r['url'] == output.name for r in g['reports'])]
        if len(matches) != 1:
            raise ValueError('bind this report to one workout/session before rendering')
        group = matches[0]
        archive = project / 'data/revisions/page-delivery' / output.name
        if output.is_file() and not archive.exists():
            write(archive, output.read_text())
        review = dict(group.get('review') or {})
        changed = review.get('analysis_layout') != 'inline' or 'full_report' in review
        review['analysis_layout'] = 'inline'
        review.pop('full_report', None)
        if changed:
            save_workout(project, {'workout_id': group['workout_id'], 'date': group['date'], 'review': review})
        render(project)
        print(project / 'workouts' / (group['workout_id'] + '.html'))
        return
    result = renderer.render(data, output)
    label = (data.get('report') or {}).get('total_reps_label')
    if label:
        result = result.replace('<span>完整次数</span>', f'<span>{esc(label)}</span>', 1)
    cards = []
    for item in data.get('technique_focuses') or []:
        focus = dict(item)
        for key in ('image', 'gif'):
            asset = (source.parent / focus[key]).resolve()
            if not asset.is_file():
                raise ValueError('missing focus evidence: ' + str(asset))
            focus[key] = str(asset.relative_to(project))
        # Workout pages and detailed reports are both one directory below root.
        if output.parent.parent != project:
            raise ValueError('detailed reports must be one directory below project root')
        cards.append(technique_focus_html({'date': data['report']['date']}, focus))
    if cards:
        section = '<section id="technique-focuses"><h2>技术重点</h2><p>以下是可调整的动作重点，单独保留，不增加上方问题数。</p>' + ''.join(cards) + '</section>'
        result = result.replace('<section><h2>下次训练</h2>', section + '<section><h2>下次训练</h2>', 1)
        result = result.replace('</style>', '.focus-evidence{margin:20px 0;background:transparent;overflow-wrap:anywhere;min-height:0}.focus-evidence img{width:100%;max-width:560px;height:auto;border-radius:8px}.focus-evidence figcaption{position:static;background:transparent;color:var(--ink);padding:0;margin-bottom:10px}.focus-evidence figcaption p{font-size:13px}</style>', 1)
    observations = []
    for item in data.get('form_observations') or []:
        observation = dict(item)
        if observation.get('gif'):
            asset = (source.parent / observation['gif']).resolve()
            if not asset.is_file():
                raise ValueError('missing observation GIF: ' + str(asset))
            observation['gif'] = str(asset.relative_to(project))
            observations.append(observation_evidence_html(observation))
    if observations:
        section = '<section id="video-review"><h2>动作片段复查</h2>' + ''.join(observations) + '</section>'
        result = result.replace('<section><h2>下次训练</h2>', section + '<section><h2>下次训练</h2>', 1)
    output.write_text(theme_review_html(result))
    if output.parent.name == 'form-reviews':
        renderer.write_report_index(output.parent, data, output.name)
    print(output)


if __name__ == '__main__':
    main()
