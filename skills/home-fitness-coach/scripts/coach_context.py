"""Read-only task context; no migrations, derived writes, or HTML rendering."""
import json
from pathlib import Path
from training_pages import collect, training_groups, read


def compact_context(project, last=6, mode='coach', workout_id=None):
    project = Path(project)
    policy_path = project / 'data/review-policy.json'
    policy = read(policy_path) if policy_path.exists() else {}
    # Review preflight intentionally avoids plans, prior counts and diagnostic prose.
    if mode == 'review':
        return {'schema_version': 1, 'mode': 'review',
                'review_requirements': policy.get('standing_corrections', []),
                'counting_context': 'Prior chat may already expose targets; declare exposure honestly. No counts or plans loaded by this command.'}
    groups = collect(project, include_analysis=False)
    actuals = training_groups(groups)[-max(1, last):]
    profile_path = project / 'data/coach-profile.json'
    context = {'schema_version': 1, 'mode': 'coach',
               'coach_profile': read(profile_path) if profile_path.exists() else {},
               'review_requirements': policy.get('standing_corrections', []),
               'recent_actuals': [], 'corrections': [], 'current_workout': None}
    for group in groups:
        for correction in group.get('corrections', []):
            context['corrections'].append({'workout_id': group['workout_id'], **correction})
    for group in actuals:
        entry = {'workout_id': group['workout_id'], 'date': group['date'],
                 'exercises': [], 'feedback': [], 'technique_focuses': [],
                 'review': {k: v for k, v in (group.get('review') or {}).items()
                            if k in ('summary', 'priorities', 'next_time', 'feedback')}}
        for session in group['sessions']:
            entry['exercises'].extend({
                'exercise_id': ex['exercise_id'], 'name': ex.get('name'),
                'sets': [{k: row.get(k) for k in ('side', 'completed_reps', 'load', 'rir')}
                         for row in ex.get('sets', [])]}
                for ex in session.get('exercises', []))
            feedback = {k: v for k, v in (session.get('user_feedback') or {}).items()
                        if v not in (None, [], {}, '')}
            if feedback:
                entry['feedback'].append({'session_id': session['session_id'], **feedback})
            for focus in session.get('technique_focuses', []):
                entry['technique_focuses'].append({k: focus.get(k) for k in
                    ('exercise_id', 'issue_id', 'title', 'observation', 'cue', 'set', 'side', 'timestamp')
                    if focus.get(k) is not None})
            entry.setdefault('corrections', []).extend(
                __import__('laterality').correction_context([session]))
        context['recent_actuals'].append(entry)
    if workout_id:
        matches = [g for g in groups if g['workout_id'] == workout_id]
        if not matches:
            raise ValueError('workout not found: ' + workout_id)
        context['current_workout'] = {k: matches[0][k] for k in
            ('workout_id', 'date', 'plan', 'review', 'corrections') if k in matches[0]}
    else:
        plans = [g for g in groups if g.get('plan')]
        if plans:
            latest = plans[-1]
            context['latest_prescription'] = {
                'workout_id': latest['workout_id'], 'date': latest['date'],
                'summary': latest['plan'].get('summary'),
                'has_actual_records': bool(latest['sessions']),
                'path': 'data/workouts/' + latest['workout_id'] + '.json'}
    return context
