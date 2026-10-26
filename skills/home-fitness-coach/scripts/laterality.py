"""Working-side review and consistency gates; never infer anatomy from set order."""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path


VERSION = 1
SIDE_FIELDS = ('side', 'side_review', 'side_basis', 'side_confidence')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def require(condition, message):
    if not condition:
        raise ValueError('laterality: ' + message)


def policy(project):
    path = Path(project) / 'data/review-policy.json'
    return read(path) if path.exists() else {}


def strict_report(project, report, path):
    if report.get('laterality_policy_version', 0) >= VERSION:
        return True
    rules = policy(project)
    if not rules.get('require_working_side_review'):
        return False
    path, project = Path(path).resolve(), Path(project).resolve()
    try:
        relative = str(path.relative_to(project))
    except ValueError:
        return True
    old_hash = (rules.get('historical_reports') or {}).get(relative)
    return not (old_hash and path.is_file()
                and hashlib.sha256(path.read_bytes()).hexdigest() == old_hash)


def local_asset(project, value):
    require(isinstance(value, str) and value, 'missing evidence path')
    project = Path(project).resolve()
    path = Path(value)
    path = (path if path.is_absolute() else project / path).resolve()
    require(path.is_relative_to(project) and path.is_file(), 'missing/project-external evidence: ' + value)
    return path


def unilateral_ids(project, data):
    path = Path(project) / 'data/catalogs/exercises.json'
    ids = {x['id'] for x in read(path).get('exercises', []) if x.get('unilateral')} if path.exists() else set()
    ids.update(x['exercise_id'] for x in data.get('exercises', []) if x.get('unilateral'))
    # This established family also stays protected in isolated import fixtures.
    ids.add('split_squat.bulgarian')
    return ids


def segments(row):
    return row.get('source_segments') or row.get('segments') or [{
        'file': row.get('source_file') or row.get('file'),
        'start_s': row.get('start_s'), 'end_s': row.get('end_s')}]


def within(row, file, timestamp):
    return any(s['file'] == file and float(s['start_s']) - .11 <= timestamp <= float(s['end_s']) + .11
               for s in segments(row))


def check_frame(project, proof, row, sources):
    receipt_path = local_asset(project, proof.get('receipt'))
    receipt = read(receipt_path)
    frame = next((f for f in receipt.get('frames', []) if f['id'] == proof.get('frame_id')), None)
    require(frame is not None, 'side evidence frame absent from receipt')
    source = receipt['source']
    file, timestamp = source['file'], float(frame['source_s'])
    require(source.get('fingerprint') and file in sources and source['fingerprint'] == sources[file].get('fingerprint'),
            'side evidence source fingerprint differs')
    require(within(row, file, timestamp), 'side evidence outside this working set')
    image = (receipt_path.parent / frame['path']).resolve()
    require(local_asset(project, str(image)) == image, 'invalid side frame')
    require(hashlib.sha256(image.read_bytes()).hexdigest() == frame['sha256'], 'side frame hash differs')
    return file, timestamp


def check_row(project, row, sources):
    side = row.get('side')
    require(side in ('left', 'right', 'unknown'),
            'single-leg set needs anatomical left/right; first/second/先侧/后侧 are not sides')
    review = row.get('side_review') or {}
    require(isinstance(review, dict), 'side_review must be structured')
    require(review.get('reviewer') and review.get('basis'), 'side review needs reviewer and concrete support observation')
    proofs = review.get('evidence') or []
    require(proofs, 'each working side needs its own source-frame evidence')
    observed = [check_frame(project, proof, row, sources) for proof in proofs]
    if side in ('left', 'right'):
        require(review.get('status') == 'confirmed' and review.get('side') == side,
                'reported side conflicts with its reviewed side')
        require(review.get('calibration'), 'missing anatomical/working-leg calibration')
    else:
        require(review.get('status') == 'unresolved', 'unknown side must be explicitly unresolved')
        require(review.get('unresolved_cause') in ('occlusion', 'out_of_frame', 'overlap', 'orientation'),
                'unresolved side needs a specific visibility/orientation cause')
        reason = str(review.get('reason') or '').strip()
        require(reason and reason not in ('未标定', '看不清', '无法判断', '解剖左右未标定', '左右未标定，无法判断是哪一侧'),
                'unknown side needs a concrete occlusion/ambiguity reason, not 未标定')
        blocked = review.get('affected_segment') or {}
        require(blocked.get('file') in sources, 'unresolved side needs original-file interval')
        start, end = blocked.get('start_s'), blocked.get('end_s')
        require(isinstance(start, (int, float)) and isinstance(end, (int, float))
                and math.isfinite(start + end) and start < end
                and within(row, blocked['file'], start) and within(row, blocked['file'], end),
                'unresolved-side interval outside this working set')
        require(any(f == blocked['file'] and start - .11 <= t <= end + .11 for f, t in observed),
                'unresolved-side interval has no supporting frame')


def check_media(data, rows, ids):
    aliases = {x.get('name'): x.get('exercise_id') for x in data.get('exercises', [])}
    for field in ('issues', 'technique_focuses', 'form_observations'):
        for media in data.get(field, []) or []:
            ex = media.get('exercise_id') or aliases.get(media.get('exercise'))
            if media.get('side') or re.search(r'[左右](?:腿|侧|脚)', str(media.get('set_label') or '')):
                require(ex, 'side-labelled media needs an explicit exercise binding')
            if ex not in ids:
                continue
            number = media.get('set_index', media.get('set'))
            matches = [r for r in rows if r['exercise_id'] == ex
                       and r.get('set_index', r.get('set')) == number]
            require(len(matches) == 1, 'unilateral media needs one explicit exercise/set binding')
            row = matches[0]
            require(media.get('side') == row.get('side'), 'GIF/issue side differs from working set')
            label = str(media.get('set_label') or '')
            require(not re.search(r'先侧|后侧|先练侧|后练侧', label), 'ambiguous GIF side label')
            claimed = set(re.findall(r'([左右])(?:腿|侧|脚)', label))
            expected = {'left': '左', 'right': '右'}.get(row.get('side'))
            require(not claimed or claimed == {expected}, 'GIF displayed side label contradicts working set')
            file = media.get('source_file') or media.get('file')
            timestamp = media.get('timestamp_s')
            require(file and isinstance(timestamp, (int, float)) and within(row, file, timestamp),
                    'unilateral media source/time differs from working set')


def validate_report(project, data, path):
    strict = strict_report(project, data, path)
    if not strict:
        return False
    ids = unilateral_ids(project, data)
    sources = {s['file']: s for s in data.get('sources', [])}
    rows = data.get('timeline', [])
    keys = [(r['exercise_id'], r.get('set')) for r in rows if r['exercise_id'] in ids]
    require(len(keys) == len(set(keys)), 'duplicate unilateral exercise/set binding')
    for row in rows:
        if row['exercise_id'] in ids:
            check_row(project, row, sources)
    check_media(data, rows, ids)
    return True


def validate_session(project, data, report=None, report_path=None):
    report_path = report_path or (data.get('analysis') or {}).get('legacy_report_json')
    if report is None and report_path:
        report_path = Path(report_path)
        if not report_path.is_absolute():
            report_path = Path(project) / report_path
        require(report_path.is_file(), 'canonical report source missing')
        report = read(report_path)
    strict = (data.get('analysis') or {}).get('laterality_policy_version', 0) >= VERSION
    if report is not None:
        strict = validate_report(project, report, report_path) or strict
    elif policy(project).get('require_working_side_review'):
        strict = True
    ids = unilateral_ids(project, data)
    sources = {s['file']: s for s in data.get('sources', [])}
    rows = [{**r, 'exercise_id': ex['exercise_id']} for ex in data.get('exercises', []) for r in ex.get('sets', [])]
    for row in rows:
        if strict and row['exercise_id'] in ids:
            check_row(project, row, sources)
    if strict:
        check_media(data, rows, ids)
    if report is not None:
        expected = {(r['exercise_id'], r['set']): r for r in report.get('timeline', [])
                    if r.get('exercise_id') and r.get('set') is not None}
        if strict:
            actual_keys = [(r['exercise_id'], r.get('set_index')) for r in rows if r['exercise_id'] in ids]
            expected_keys = {key for key in expected if key[0] in ids}
            require(len(actual_keys) == len(set(actual_keys)) and set(actual_keys) == expected_keys,
                    'canonical unilateral groups differ from report timeline')
        for row in rows:
            other = expected.get((row['exercise_id'], row.get('set_index')))
            if not other or row['exercise_id'] not in ids:
                continue
            a, b = row.get('side'), other.get('side')
            if strict or (a in ('left', 'right') and b in ('left', 'right')):
                require(a == b, 'canonical side differs from report timeline')
                if strict:
                    require(row.get('side_review') == other.get('side_review'),
                            'canonical side evidence differs from report')
    return strict


def correction_context(sessions):
    result = []
    for session in sessions:
        analysis = session.get('analysis') or {}
        for key in ('correction_run', 'laterality_review'):
            correction = analysis.get(key)
            if correction:
                result.append({'session_id': session['session_id'], 'date': session['date'],
                               'kind': key, 'purpose': correction.get('purpose'),
                               'calibration': correction.get('calibration'),
                               'observations': correction.get('visual_observations', []),
                               'scope': correction.get('scope') or correction.get('counting')})
    return result
