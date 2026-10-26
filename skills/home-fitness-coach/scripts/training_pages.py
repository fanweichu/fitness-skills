#!/usr/bin/env python3
"""Offline workout pages: planned prescription + durable actuals + follow-up."""
from __future__ import annotations
import argparse
import calendar
import json
import os
import re
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from urllib.parse import urlparse, quote
from zoneinfo import ZoneInfo
import laterality


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def esc(value):
    return escape(str(value if value is not None else ''), quote=True)


def valid_id(value):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:-[a-z0-9-]+)?', value):
        raise ValueError('workout_id must be YYYY-MM-DD or YYYY-MM-DD-suffix')
    date.fromisoformat(value[:10])
    return value


def link(url, label, external=False):
    parsed = urlparse(str(url))
    if parsed.scheme not in ('', 'https') or str(url).startswith('//'):
        raise ValueError('unsupported link: ' + str(url))
    attrs = ' target="_blank" rel="noopener noreferrer"' if external else ''
    return f'<a href="{esc(url)}"{attrs}>{esc(label)}</a>'


def video(item):
    url = item.get('video_url')
    if not url:
        return ''
    host = urlparse(url).hostname
    if host not in ('youtube.com', 'www.youtube.com', 'youtu.be'):
        raise ValueError('video_url must link to YouTube')
    label = 'YouTube 搜索' if '/results?' in url else 'YouTube 教学 ↗'
    return link(url, label, True)


def save_workout(project, incoming):
    wid = valid_id(incoming['workout_id'])
    path = project / 'data/workouts' / f'{wid}.json'
    old = read(path) if path.exists() else {}
    merged = {**old, **incoming, 'schema_version': 1, 'workout_id': wid}
    if merged.get('date') != wid[:10]:
        raise ValueError('workout date and id disagree')
    correction_ids = set()
    for correction in merged.get('corrections', []):
        if not all(isinstance(correction.get(k), str) and correction[k].strip()
                   for k in ('id', 'date', 'scope', 'superseded', 'replacement', 'basis')):
            raise ValueError('correction needs id/date/scope/superseded/replacement/basis')
        date.fromisoformat(correction['date'])
        if correction['id'] in correction_ids:
            raise ValueError('duplicate correction id')
        correction_ids.add(correction['id'])
    for block in (merged.get('plan') or {}).get('blocks', []):
        for item in block.get('items', []):
            if not item.get('name') or not item.get('dose') or not item.get('video_url'):
                raise ValueError('every planned movement requires name, dose and YouTube link')
            video(item)
    # Validate linkage before writing any revision.
    if merged.get('session_ids') is not None:
        for sid in merged['session_ids']:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}-\d{3}', sid):
                raise ValueError('invalid canonical session id')
            source = project / 'data/sessions' / f'{sid}.json'
            if not source.exists() or read(source)['date'] != merged['date']:
                raise ValueError('linked session missing or date differs: ' + sid)
    if old:
        revision_path = project / 'data/revisions/workouts' / f'{wid}-r{old["revision"]:03d}.json'
        if not revision_path.exists():
            write(revision_path, old)
    merged['revision'] = int(old.get('revision', 0)) + 1
    merged['updated_at'] = now()
    write(path, merged)
    return merged


def collect(project, include_analysis=True):
    manifests = {p.stem: read(p) for p in sorted((project / 'data/workouts').glob('*.json'))}
    sessions = [read(p) for p in sorted((project / 'data/sessions').glob('*.json'))]
    for session in sessions:
        laterality.validate_session(project, session)
    owners = {}
    by_date = defaultdict(list)
    groups = {}
    for wid, manifest in manifests.items():
        valid_id(wid)
        if manifest.get('workout_id') != wid or manifest.get('date') != wid[:10]:
            raise ValueError('manifest id/date mismatch: ' + wid)
        by_date[manifest['date']].append(wid)
        groups[wid] = {**manifest, 'sessions': [], 'reports': []}
        for sid in manifest.get('session_ids', []):
            if sid in owners:
                raise ValueError('session assigned to multiple workouts: ' + sid)
            owners[sid] = wid
    known_ids = {s['session_id'] for s in sessions}
    if set(owners) - known_ids:
        raise ValueError('unknown linked sessions: ' + ', '.join(set(owners) - known_ids))
    for session in sessions:
        day = session['date']
        explicit = session.get('workout_id')
        if explicit and owners.get(session['session_id'], explicit) != explicit:
            raise ValueError('conflicting workout assignment: ' + session['session_id'])
        wid = owners.get(session['session_id']) or explicit
        if not wid:
            eligible = [k for k in by_date[day] if 'session_ids' not in manifests[k]]
            if len(eligible) > 1 or (by_date[day] and not eligible):
                raise ValueError('same-day workouts require explicit session_ids: ' + day)
            wid = eligible[0] if eligible else day
        valid_id(wid)
        if wid[:10] != day:
            raise ValueError('session date differs from workout: ' + wid)
        group = groups.setdefault(wid, {'workout_id': wid, 'date': day, 'title': '训练记录', 'sessions': [], 'reports': []})
        group['sessions'].append(session)
    index_path = project / 'form-reviews/report-index.json'
    records = read(index_path).get('records', []) if index_path.exists() else []
    for record in records:
        target = 'form-reviews/' + record['url']
        matching = [g for g in groups.values() if any(
            (s.get('analysis') or {}).get('legacy_report_json') and str(Path(s['analysis']['legacy_report_json']).with_suffix('.html')) == target
            for s in g['sessions'])]
        if not matching:
            matching = [g for g in groups.values() if g['date'] == record['date']]
        if len(matching) == 1:
            matching[0]['reports'].append(record)
        elif not matching:
            wid = valid_id(record['date'])
            groups[wid] = {'workout_id': wid, 'date': record['date'], 'title': '专项复盘', 'sessions': [], 'reports': [record]}
        # Ambiguous supplementary reports stay in the raw report index, never guessed onto a workout.
    for group in groups.values():
        if include_analysis and (group.get('review') or {}).get('analysis_layout') == 'inline':
            group['analysis_reports'] = []
            for record in group['reports']:
                path = project / 'form-reviews' / Path(record['url']).with_suffix('.json')
                if not path.is_file():
                    raise ValueError('missing inline analysis source: ' + str(path))
                analysis = read(path)
                laterality.validate_report(project, analysis, path)
                for issue in analysis.get('issues', []):
                    for key in ('gif', 'image'):
                        if issue.get(key):
                            media = Path(issue[key])
                            if media.is_absolute():
                                issue[key] = os.path.relpath(media, project / 'workouts')
                group['analysis_reports'].append(analysis)
    return sorted(groups.values(), key=lambda g: (g['date'], g['workout_id']))


def training_groups(groups):
    return [g for g in groups if any(s.get('exercises') for s in g['sessions'])]


def status(group):
    if (group.get('review') or {}).get('status') == 'review_pending':
        return '分析待完成'
    if any(s.get('status') == 'review_pending' for s in group['sessions']):
        return '分析待完成'
    if group['sessions'] or group['reports']:
        return '已有训练记录'
    if group.get('review'):
        return '已补充反馈'
    return '已安排 · 待反馈'


CSS = Path(__file__).with_name('training_theme.css').read_text(encoding='utf-8')


def document(title, body, nav, page_class=""):
    return f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><style>{CSS}</style></head><body><main class="{esc(page_class)}"><nav>{nav}</nav>{body}<footer>记录保留，建议随训练更新。离线打开即可；YouTube 教学需要联网。</footer></main></body></html>'


def bullets(values):
    return '<ul>' + ''.join(f'<li>{esc(v)}</li>' for v in values) + '</ul>' if values else ''


def actual_table(group):
    rows = []
    for session in group['sessions']:
        for ex in session.get('exercises', []):
            sets = ex.get('sets', [])
            side_labels = {'left': '左', 'right': '右', 'first': '先侧', 'second': '后侧', 'side_a': '先侧', 'side_b': '后侧', 'bilateral': '', 'unknown': ''}
            def side_label(row):
                if (row.get('side_review') or {}).get('status') == 'unresolved':
                    return '（侧别待确认）'
                return ('（' + side_labels.get(row.get('side'), '侧别未确认') + '）'
                        if row.get('side') not in (None, 'bilateral', 'unknown') else '')
            counts = ' / '.join(('≥' if s.get('coverage_complete') is False else '') + str(s.get('completed_reps', '?')) + side_label(s) for s in sets)
            rows.append(f'<tr><td data-label="动作">{esc(ex.get("name"))}</td><td data-label="记录组数">{len(sets)}</td><td data-label="完成次数">{esc(counts)}</td><td data-label="计数置信度">{esc({"high":"高", "medium":"中", "low":"低"}.get(ex.get("confidence"), "待确认"))}</td></tr>')
    if not rows:
        return '<p class="empty">尚无已归档的正式训练组次。方案不计作完成记录。</p>'
    detail_note = '详细时间点见本页训练时间线。' if group.get('analysis_reports') else '详细时间点见原复盘。'
    return '<div class="scroll"><table class="actual-table"><thead><tr><th>实际动作</th><th>记录组数</th><th>每组完成次数 / 侧别</th><th>计数置信度</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div><p class="muted">单侧动作按每侧分别记录；不同变式分别列出。' + detail_note + '</p>'


def technique_focus_html(session, focus):
    title = focus.get('title', '')
    cue = focus.get('cue') or focus.get('interpretation') or focus.get('observation', '')
    if not focus.get('gif'):
        return f'<p>{esc(title)}：{esc(cue)}</p>'
    def asset(value):
        value = str(value or '')
        if not value or urlparse(value).scheme or value.startswith('/') or '..' in Path(value).parts:
            raise ValueError('focus evidence must use a project-relative asset path')
        return '../' + quote(value)
    time_range = focus.get('source_time_range') or focus.get('timestamp', '')
    date_label = focus.get('training_date') or session.get('date', '')
    set_label = f'第 {focus.get("set_index") or focus.get("set", "?")} 组'
    side = {'left': '左侧', 'right': '右侧', 'first': '先侧', 'second': '后侧'}.get(focus.get('side'))
    if side:
        set_label += ' · ' + side
    tracking = f'{date_label} · {set_label} · {focus.get("source_file") or focus.get("file", "")} · {time_range}'
    still = link(asset(focus['image']), '查看对应截图') if focus.get('image') else ''
    source = link(focus['reference_url'], '动作参考', True) if focus.get('reference_url') else ''
    return f'<figure class="focus-evidence"><figcaption><strong>技术重点 · {esc(title)}</strong><p class="muted">{esc(tracking)}</p></figcaption><img src="{esc(asset(focus["gif"]))}" alt="{esc(title)}：本次训练动作证据" loading="lazy"><p>{esc(focus.get("observation", ""))}</p><p><strong>下次提示：</strong>{esc(cue)}</p><p>{still} {source}</p></figure>'


def observation_evidence_html(observation):
    path = str(observation.get('gif') or '')
    if not path:
        return ''
    if urlparse(path).scheme or path.startswith('/') or '..' in Path(path).parts:
        raise ValueError('observation GIF must use a project-relative path')
    tracking = ' · '.join(str(observation.get(k) or '') for k in ('training_date', 'set_label', 'source_file', 'source_time_range'))
    return f'<figure class="observation-evidence"><figcaption><strong>{esc(observation.get("title", ""))}</strong><p class="muted">{esc(tracking)}</p></figcaption><img src="../{esc(quote(path))}" alt="{esc(observation.get("title", ""))}" loading="lazy" style="display:block;width:100%;max-width:480px;height:auto"><p>{esc(observation.get("interpretation") or observation.get("observation", ""))}</p></figure>'


def analysis_html(reports):
    """Render analysis JSON in the workout document, without a second HTML page."""
    colors = ['#65a9dc', '#8acb8b', '#c19ce8', '#e9b66c', '#ea929b', '#78c9bd']
    body = '<section id="analysis"><h2>完整训练分析</h2>'
    for report_index, report in enumerate(reports):
        body += f'<p class="muted">{esc(report.get("report", {}).get("timing_note", ""))}</p>'
        phases = report.get('phase_analysis') or {}
        def stamp(seconds):
            seconds = float(seconds or 0)
            return f'{int(seconds // 60):02d}:{seconds % 60:04.1f}'
        if phases:
            warm = phases.get('warmup_and_preparation') or {}
            cool = phases.get('transition_and_cooldown') or {}
            body += '<article class="card"><h3>热身、正式训练与拉伸</h3>'
            body += f'<p>热身与准备：录像1 {stamp(warm.get("start_s"))}–{stamp(warm.get("end_s"))}；正式训练含休息：录像1 {stamp(warm.get("end_s"))}–最后一段录像 {stamp(cool.get("start_s"))}；随后整理与拉伸。</p>'
            for key in ('warmup_observations', 'cooldown_observations'):
                if phases.get(key):
                    body += f'<p>{esc(phases[key])}</p>'
            body += '</article>'
        palette = {ex['exercise_id']: colors[i % len(colors)] for i, ex in enumerate(report.get('exercises', []))}
        body += '<h3>训练时间线</h3><div class="analysis-legend">' + ''.join(
            f'<span><i style="background:{palette[ex["exercise_id"]]}"></i>{esc(ex["name"])}</span>' for ex in report.get('exercises', [])) + '</div>'
        source_names = {source['file']: f'录像{i + 1}' for i, source in enumerate(report.get('sources', []))}
        for source in report.get('sources', []):
            duration = float(source['duration_s'])
            bars = []
            for row_index, row in enumerate(report.get('timeline', [])):
                for segment in row.get('segments') or [row]:
                    if segment['file'] != source['file']:
                        continue
                    left = float(segment['start_s']) / duration * 100
                    width = (float(segment['end_s']) - float(segment['start_s'])) / duration * 100
                    title = f'{row["exercise"]} · 第{row["set"]}组 · {segment.get("start", stamp(segment["start_s"]))}–{segment.get("end", stamp(segment["end_s"]))}'
                    bars.append(f'<a href="#analysis-set-{report_index}-{row_index}" title="{esc(title)}" aria-label="{esc(title)}" style="left:{left:.5f}%;width:{width:.5f}%;background:{palette[row["exercise_id"]]}"></a>')
            body += f'<div class="analysis-track-label">{esc(source_names[source["file"]])} · {stamp(duration)}</div><div class="analysis-track">{"".join(bars)}</div>'
        rows = []
        for i, row in enumerate(report.get('timeline', [])):
            segments = row.get('segments') or [row]
            times = '；'.join(f'{source_names.get(s["file"], s["file"])} {s.get("start", stamp(s["start_s"]))}–{s.get("end", stamp(s["end_s"]))}' for s in segments)
            side = {'left': '左', 'right': '右'}.get(row.get('side'), '')
            if (row.get('side_review') or {}).get('status') == 'unresolved':
                side = '侧别待确认'
            note = row.get('note', '')
            if side == '侧别待确认':
                note += ' ' + row['side_review']['reason']
            completed = ('≥' if row.get('coverage_complete') is False else '') + str(row['completed'])
            cells = [('动作', row['exercise']), ('组／侧', f'{row["set"]} {side}'), ('原片时间', times), ('完成／半次／待定', f'{completed} / {row.get("partial", 0)} / {row.get("uncertain", 0)}'), ('置信度', {'high': '高', 'medium': '中', 'low': '低'}.get(row.get('confidence'), '待确认')), ('证据说明', note)]
            rows.append(f'<tr id="analysis-set-{report_index}-{i}">' + ''.join(f'<td data-label="{esc(label)}">{esc(value)}</td>' for label, value in cells) + '</tr>')
        body += '<div class="scroll"><table class="analysis-table"><thead><tr><th>动作</th><th>组／侧</th><th>原片时间</th><th>完成／半次／待定</th><th>置信度</th><th>证据说明</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'
        if not report.get('issues'):
            body += '<p class="muted">本次没有确认需要单列的问题动作；动作片段与复查说明见上方。</p>'
        for issue in report.get('issues', []):
            body += f'<article class="card"><h3>{esc(issue.get("title") or issue.get("exercise"))}</h3><p>{esc(issue.get("observation"))}</p><p>{esc(issue.get("correction") or issue.get("cue"))}</p>'
            media = issue.get('gif') or issue.get('image')
            if media:
                body += f'<img class="analysis-media" src="{esc(media)}" alt="{esc(issue.get("exercise"))} · {esc(issue.get("timestamp"))}">'
            body += f'<p class="muted">{esc(report.get("report", {}).get("date"))} · {esc(issue.get("file"))} · {esc(issue.get("timestamp"))} · 第{esc(issue.get("set"))}组 · {esc(issue.get("side", ""))} · {esc(issue.get("phase", ""))} · {esc(issue.get("scan_range"))} · 置信度：{esc(issue.get("confidence"))}</p>'
            if issue.get('video_url'):
                body += video(issue)
            body += '</article>'
        body += '<details><summary>本次实际动作的教学入口</summary><ul>'
        for ex in report.get('exercises', []):
            if ex.get('video_url'):
                body += '<li>' + esc(ex['name']) + '：' + video(ex) + '</li>'
        body += '</ul></details><h3>下次训练</h3>' + bullets(report.get('next_session', []))
        body += '<h3 id="analysis-cost">分析成本</h3>'
        for label, value in (report.get('cost') or {}).items():
            body += f'<p><strong>{esc(label)}：</strong>{esc(value)}</p>'
        body += '<details><summary>原视频来源与分析清单</summary>'
        for source in report.get('sources', []):
            body += f'<p>{esc(source_names[source["file"]])} · {esc(source.get("path"))} · {stamp(source["duration_s"])}</p>'
        body += '<dl class="analysis-metrics">' + ''.join(f'<dt>{esc(k)}</dt><dd>{esc(v)}</dd>' for k, v in (report.get('analysis_manifest') or {}).items()) + '</dl></details>'
    return body + '</section>'


def workout_html(group):
    wid = group['workout_id']
    plan = group.get('plan') or {}
    review = group.get('review') or {}
    title = group.get('title', '训练记录')
    body = f'<header><span class="tag">{esc(group["date"])}</span><span class="tag">{status(group)}</span><h1>{esc(title)}</h1></header>'
    body += '<section id="plan"><h2>训练方案</h2>'
    if group.get('corrections'):
        body += '<article class="card accent" id="corrections"><h3>后续更正 · 原方案保留供追溯</h3><p>以下更正优先于本页原方案中的旧建议。</p>'
        for c in group['corrections']:
            body += f'<p><strong>{esc(c["date"])} · {esc(c["scope"])}</strong><br>已撤回：<s>{esc(c["superseded"])}</s><br><strong>更正：</strong>{esc(c["replacement"])}<br><span class="muted">依据：{esc(c["basis"])}</span></p>'
        body += '</article>'
    if plan:
        body += f'<div class="card accent"><p class="dose">{esc(plan.get("estimated_duration", ""))}</p><p>{esc(plan.get("summary", ""))}</p></div>'
        for block in plan.get('blocks', []):
            body += f'<article class="card plan-block"><div class="block-heading"><h3>{esc(block["title"])}</h3><p class="muted">{esc(block.get("note", ""))}</p></div>'
            for item in block.get('items', []):
                body += f'<div class="exercise"><strong class="exercise-name">{esc(item["name"])}</strong><p class="dose">{esc(item["dose"])}</p><span class="video">{video(item)}</span>'
                body += f'<p class="exercise-cue">{esc(item.get("cue", ""))}</p></div>'
            body += '</article>'
        body += bullets(plan.get('notes', []))
    else:
        body += '<p class="empty">这次没有单独保存训练前方案；以下保留当时的实际训练和复盘。</p>'
    body += '</section><section id="actual"><h2>实际完成</h2>' + actual_table(group) + '</section><section id="review"><h2>练后结论</h2>'
    if review:
        body += f'<article class="card accent"><p>{esc(review.get("summary", ""))}</p>{bullets(review.get("priorities", []))}'
        if review.get('next_time'):
            body += f'<p><strong>下次：</strong>{esc(review["next_time"])}</p>'
        if review.get('feedback'):
            body += f'<p><strong>你的反馈：</strong>{esc(review["feedback"])}</p>'
        if review.get('technique_focuses'):
            body += '<div class="focus-grid">' + ''.join(technique_focus_html({'date': group['date']}, focus) for focus in review['technique_focuses']) + '</div>'
        body += '</article>'
    for s in group['sessions']:
        body += f'<article class="card"><h3>{esc(s.get("title") or s.get("session_label"))}</h3><p>{esc(s.get("summary", ""))}</p>'
        for issue in s.get('issues', []):
            body += f'<p><strong>{esc(issue.get("title") or issue.get("issue_id"))}：</strong>{esc(issue.get("cue", ""))}</p>'
        focuses = s.get('technique_focuses') or []
        if isinstance(focuses, dict):
            focuses = [focuses]
        if focuses:
            body += '<div class="focus-grid">' + ''.join(technique_focus_html(s, f) for f in focuses) + '</div>'
        observations = s.get('form_observations') or []
        if observations:
            body += '<div id="video-review"><h3>动作片段与复查</h3>' if group.get('analysis_reports') else '<details><summary>动作观察与解释</summary>'
            for observation in observations:
                body += observation_evidence_html(observation) or f'<p><strong>{esc(observation.get("title", ""))}</strong> · {esc(observation.get("timestamp", ""))}<br>{esc(observation.get("interpretation") or observation.get("observation", ""))}</p>'
            body += '</div>' if group.get('analysis_reports') else '</details>'
        feedback = s.get('user_feedback') or {}
        if feedback.get('notes'):
            body += f'<details><summary>训练感受与补充反馈</summary><p>{esc(feedback["notes"])}</p></details>'
        body += '</article>'
    if not group['sessions'] and not review and not group['reports']:
        body += '<p class="empty">练完后告诉我实际完成情况，或发视频路径；分析结果会更新在这里。</p>'
    if group['reports'] and not group.get('analysis_reports'):
        body += '<details><summary>完整复盘与原始证据</summary><ul>'
        for r in group['reports']:
            body += '<li>' + link('../form-reviews/' + quote(r['url']), r.get('title', r['url'])) + '</li>'
        body += '</ul></details>'
    body += '</section>'
    if group.get('analysis_reports'):
        body += analysis_html(group['analysis_reports'])
    nav = link('../dashboard.html', '← 所有训练') + link('#plan', '方案') + link('#actual', '完成情况') + link('#review', '结论')
    if group.get('analysis_reports'):
        nav += link('#video-review', '动作片段') + link('#analysis', '完整分析')
    return document(group['date'] + ' · ' + title, body, nav, page_class='workout-page')


def period_data(project, start, end):
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    if a > b:
        raise ValueError('start must not exceed end')
    groups = [g for g in collect(project) if start <= g['date'] <= end]
    trained = training_groups(groups)
    totals = {}
    for g in trained:
        for s in g['sessions']:
            for ex in s.get('exercises', []):
                key = ex['exercise_id']
                t = totals.setdefault(key, {'exercise_id': key, 'name': ex['name'], 'workout_ids': [], 'sets': 0, 'reps': 0, 'history': []})
                if g['workout_id'] not in t['workout_ids']:
                    t['workout_ids'].append(g['workout_id'])
                rows = ex.get('sets', [])
                t['sets'] += len(rows)
                t['reps'] += sum(int(x.get('completed_reps') or 0) for x in rows)
                t['history'].append({'date': g['date'], 'session_id': s['session_id'], 'sets': rows})
    return {'start': start, 'end': end, 'training_count': len(trained), 'training_dates': sorted({g['date'] for g in trained}),
            'workout_ids': [g['workout_id'] for g in trained], 'planned_only_ids': [g['workout_id'] for g in groups if g.get('plan') and not g['sessions']],
            'exercises': list(totals.values()), 'source_revisions': {s['session_id']: s.get('revision') for g in groups for s in g['sessions']},
            'limits': '仅统计已归档实际训练；未记录日期不等于休息。热身、拉伸、专项复核不单独计训练次数；单侧组分别计。次数增加本身不证明力量或体型改善。'}


def period_html(report, stats):
    body = f'<header><span class="tag">阶段报告</span><h1>{esc(report["title"])}</h1><p>{esc(stats["start"])} — {esc(stats["end"])}</p></header><div class="card accent"><strong>已归档 {stats["training_count"]} 次训练</strong><p>{esc(stats["limits"])}</p></div>'
    analysis = report.get('analysis') or {}
    for heading, key in [('训练趋势', 'summary'), ('下一阶段建议', 'next_steps')]:
        if analysis.get(key):
            body += f'<section><h2>{heading}</h2><p>{esc(analysis[key])}</p></section>'
    if not analysis:
        body += '<p class="muted">这是数据统计，尚未补充教练分析。</p>'
    if report.get('analysis_source_revisions') and report['analysis_source_revisions'] != stats['source_revisions']:
        body += '<p class="card">基础记录已有修订：下方统计已刷新，上方文字分析需重新核对。</p>'
    body += '<h2>按动作统计</h2><div class="scroll"><table><tr><th>动作 / 变式</th><th>训练次数</th><th>组数</th><th>完成次数</th></tr>'
    for ex in stats['exercises']:
        body += f'<tr><td>{esc(ex["name"])}</td><td>{len(ex["workout_ids"])}</td><td>{ex["sets"]}</td><td>{ex["reps"]}</td></tr>'
    body += '</table></div><h2>本期训练</h2><ul>' + ''.join('<li>' + link('../workouts/' + wid + '.html', wid) + '</li>' for wid in stats['workout_ids']) + '</ul>'
    return document(report['title'], body, link('../dashboard.html', '← 所有训练'))



def activity_days(groups):
    """One mark per actual training date, with all same-day workouts retained."""
    days = defaultdict(list)
    for group in training_groups(groups):
        days[group['date']].append(group)
    return dict(sorted(days.items()))


def day_href(day, groups):
    if len(groups) == 1:
        return 'workouts/' + groups[0]['workout_id'] + '.html'
    return '#day-' + day


def activity_html(groups, today=None):
    today = today or datetime.now().astimezone().date()
    days = activity_days(groups)
    start = today - timedelta(days=364)
    start -= timedelta(days=start.weekday())
    end = today + timedelta(days=6-today.weekday())
    columns = (end-start).days // 7 + 1
    cells, labels = [], []
    last_month = None
    for i in range((end-start).days + 1):
        d = start + timedelta(days=i)
        key = d.isoformat()
        if i % 7 == 0 and d.month != last_month:
            labels.append(f'<span style="grid-column:{i//7+1}">{d.month}月</span>')
            last_month = d.month
        count = len(days.get(key, []))
        label = f'{key} · {count} 次训练' if count else f'{key} · 未记录训练'
        css = 'heat-cell' + (' trained' if count else '') + (' future' if d > today else '')
        attrs = f'class="{css}" data-date="{key}" title="{esc(label)}" aria-label="{esc(label)}"'
        if count and d <= today:
            cells.append(f'<a {attrs} href="{esc(day_href(key, days[key]))}"></a>')
        else:
            cells.append(f'<span {attrs}></span>')
    count = sum(start.isoformat() <= d <= today.isoformat() for d in days)
    body = f'<section id="activity" class="card activity"><div class="row"><h2>训练打卡</h2><span class="muted">过去一年 · {count} 个训练日</span></div><div class="heat-scroll" tabindex="0" aria-label="过去一年训练打卡，可横向滚动"><div class="heat-layout" style="--weeks:{columns}"><div class="heat-months">{"".join(labels)}</div><div class="heat-weekdays"><span>一</span><span>三</span><span>五</span><span>日</span></div><div class="heat-grid">{"".join(cells)}</div></div></div><div class="legend"><span><i></i>未记录</span><span><i class="trained"></i>有训练</span><span>空白不代表没练</span></div></section>'
    # Include the entire recorded history and current month; no remote calendar dependency.
    first = min([today.replace(day=1), *[date.fromisoformat(d).replace(day=1) for d in days]])
    last = max([today.replace(day=1), *[date.fromisoformat(d).replace(day=1) for d in days]])
    months = []
    cursor = first
    while cursor <= last:
        months.append(cursor)
        cursor = date(cursor.year + cursor.month//12, cursor.month%12+1, 1)
    options, panels = [], []
    for month in months:
        key = month.strftime('%Y-%m')
        active = key == today.strftime('%Y-%m')
        options.append(f'<option value="{key}"{" selected" if active else ""}>{month.year} 年 {month.month} 月</option>')
        month_count = sum(d.startswith(key) for d in days)
        content = '<div class="calendar-weekdays">' + ''.join(f'<span>{d}</span>' for d in '一二三四五六日') + '</div><div class="calendar-grid">'
        for week in calendar.Calendar(firstweekday=0).monthdatescalendar(month.year, month.month):
            for d in week:
                if d.month != month.month:
                    content += '<span class="calendar-day outside" aria-hidden="true"></span>'
                    continue
                day = d.isoformat()
                trained = days.get(day, [])
                css = 'calendar-day' + (' trained' if trained else '') + (' today' if d == today else '')
                label = f'{day} · {len(trained)} 次训练' if trained else f'{day} · 未记录训练'
                attrs = f'class="{css}" data-date="{day}" title="{label}" aria-label="{label}"' + (' aria-current="date"' if d == today else '')
                if trained:
                    content += f'<a {attrs} href="{esc(day_href(day, trained))}"><span>{d.day}</span><i aria-hidden="true"></i></a>'
                else:
                    content += f'<span {attrs}><span>{d.day}</span></span>'
        content += f'</div><p class="calendar-caption muted">本月记录 {month_count} 个训练日 · 绿色日期可打开记录</p>'
        panels.append(f'<div class="calendar-month" data-month="{key}"{" hidden" if not active else ""}>{content}</div>')
    body += '<div class="calendar-overview"><section class="card calendar"><div class="calendar-toolbar"><h2>训练日历</h2><div class="month-controls"><button type="button" id="month-prev" aria-label="上个月">‹</button><select id="calendar-month" aria-label="选择月份">' + ''.join(options) + '</select><button type="button" id="month-next" aria-label="下个月">›</button></div></div>' + ''.join(panels) + '<noscript><p class="muted">启用 JavaScript 可切换月份；全部记录见下方。</p></noscript></section>'
    trained = training_groups(groups)
    recent = groups[-1] if groups else None
    body += '<aside class="card recent"><span class="eyebrow">最近安排 / 记录</span>'
    if recent:
        body += f'<p class="recent-date">{esc(recent["date"])}</p><h3>{esc(recent.get("title", "训练记录"))}</h3><p class="muted">{esc(status(recent))}</p><p>{link("workouts/" + recent["workout_id"] + ".html", "打开这次训练 →")}</p>'
    else:
        body += '<h3>从第一次训练开始</h3><p class="muted">保存训练后，这里会显示最近记录。</p>'
    body += f'<div class="stats"><div><strong>{len(days)}</strong><span>累计训练日</span></div><div><strong>{len(trained)}</strong><span>已记录训练</span></div></div></aside></div>'
    return body


CALENDAR_SCRIPT = """<script>
(() => {
  const select = document.getElementById('calendar-month');
  const prev = document.getElementById('month-prev');
  const next = document.getElementById('month-next');
  function showMonth() {
    document.querySelectorAll('.calendar-month').forEach(panel => { panel.hidden = panel.dataset.month !== select.value; });
    prev.disabled = select.selectedIndex === 0;
    next.disabled = select.selectedIndex === select.options.length - 1;
  }
  select.addEventListener('change', showMonth);
  prev.addEventListener('click', () => { select.selectedIndex--; showMonth(); });
  next.addEventListener('click', () => { select.selectedIndex++; showMonth(); });
  showMonth();
  const heat = document.querySelector('.heat-scroll');
  heat.scrollLeft = heat.scrollWidth;
  function revealDay() {
    const target = document.getElementById(location.hash.slice(1));
    if (!target || !target.id.startsWith('day-')) return;
    const details = target.closest('details');
    if (details) details.open = true;
    target.scrollIntoView({block:'center'});
  }
  window.addEventListener('hashchange', revealDay);
  revealDay();
})();
</script>"""


def dashboard_html(groups, reports):
    body = '<header class="dashboard-header"><span class="eyebrow">居家训练 · 长期记录</span><h1>我的训练</h1><p class="muted">每一次训练，都留在这里。</p></header>'
    body += activity_html(groups)
    body += '<section id="training-records"><h2>每次训练</h2>'
    months = defaultdict(list)
    for group in reversed(groups):
        months[group['date'][:7]].append(group)
    for i, (month, entries) in enumerate(months.items()):
        count = len(training_groups(entries))
        body += f'<details class="record-month"{" open" if i == 0 else ""}><summary><strong>{esc(month)}</strong><span class="muted">{count} 次训练 · {len(entries)} 条记录</span></summary><div class="record-list">'
        last_day = None
        for g in entries:
            title = g.get('title', '训练记录')
            names = list(dict.fromkeys(ex['name'] for s in g['sessions'] for ex in s.get('exercises', [])))
            desc = ' · '.join(names) or (g.get('plan') or {}).get('estimated_duration', '专项复盘与记录')
            anchor = f' id="day-{g["date"]}"' if last_day != g['date'] else ''
            last_day = g['date']
            body += f'<article class="record-entry"{anchor}><div><h3>{link("workouts/" + g["workout_id"] + ".html", g["date"][5:] + " · " + title)}</h3><p class="muted">{esc(desc)}</p></div><span class="tag">{esc(status(g))}</span></article>'
        body += '</div></details>'
    if not groups:
        body += '<p class="empty">还没有训练记录。</p>'
    body += '</section><section id="period-reports"><h2>阶段分析</h2>'
    if reports:
        for rid, r in reversed(reports):
            body += f'<article class="card"><h3>{link("reports/" + rid + ".html", r["title"])}</h3><p>{esc(r["start"])} — {esc(r["end"])}</p></article>'
    else:
        body += '<p class="empty">在 Codex 中说“统计最近一个月”，即可生成阶段回顾。</p>'
    body += '</section>' + CALENDAR_SCRIPT
    return document('我的训练', body, link('#activity', '训练打卡') + link('#training-records', '训练记录') + link('#period-reports', '阶段分析'))


def theme_review_html(html):
    """Add an idempotent presentation layer without reinterpreting old evidence."""
    html = re.sub(r'<style id="fitness-review-theme">.*?</style>', '', html, flags=re.S)
    css = Path(__file__).with_name('review_theme.css').read_text(encoding='utf-8')
    if '</head>' not in html:
        raise ValueError('review HTML has no head')
    return html.replace('</head>', '<style id="fitness-review-theme">' + css + '</style></head>', 1)


def theme_linked_reviews(project, groups):
    for name in sorted({r['url'] for g in groups if not g.get('analysis_reports') for r in g['reports']}):
        path = (project / 'form-reviews' / name).resolve()
        if path.parent != (project / 'form-reviews').resolve() or path.suffix != '.html':
            raise ValueError('review theme requires a local report HTML')
        original = path.read_text(encoding='utf-8')
        themed = theme_review_html(original)
        if original == themed:
            continue
        backup = project / 'data/backups/review-theme/form-reviews' / path.name
        if not backup.exists():
            write(backup, original)
        write(path, themed)


def render(project):
    groups = collect(project)
    theme_linked_reviews(project, groups)
    for g in groups:
        write(project / 'workouts' / (g['workout_id'] + '.html'), workout_html(g))
        if g.get('analysis_reports'):
            for report in g['reports']:
                target = '../workouts/' + g['workout_id'] + '.html'
                alias = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><meta http-equiv="refresh" content="0;url=' + target + '#analysis"><title>训练记录</title><p><a href="' + target + '#analysis">打开本次训练与完整分析</a></p><script>location.replace(' + json.dumps(target) + '+(location.hash||"#analysis"))</script></html>'
                write(project / 'form-reviews' / report['url'], alias)
    reports = []
    for path in sorted((project / 'data/period-reports').glob('*.json')):
        r = read(path)
        stats = period_data(project, r['start'], r['end'])
        write(project / 'derived/period-reports' / path.name, stats)
        write(project / 'reports' / (path.stem + '.html'), period_html(r, stats))
        reports.append((path.stem, r))
    write(project / 'dashboard.html', dashboard_html(groups, reports))
    write(project / 'derived/workout-index.json', {'generated_at': now(), 'workouts': [{'workout_id': g['workout_id'], 'date': g['date'], 'status': status(g), 'url': 'workouts/' + g['workout_id'] + '.html', 'session_ids': [s['session_id'] for s in g['sessions']]} for g in groups]})
    return groups


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path.cwd())
    subs = parser.add_subparsers(dest='command', required=True)
    p = subs.add_parser('save-workout'); p.add_argument('--input', type=Path, required=True)
    subs.add_parser('render')
    p = subs.add_parser('period'); p.add_argument('--start', required=True); p.add_argument('--end', required=True); p.add_argument('--analysis', type=Path)
    args = parser.parse_args(); project = args.project.resolve()
    if args.command == 'save-workout':
        saved = save_workout(project, read(args.input))
        print('saved workout:', saved['workout_id'])
    if args.command == 'period':
        stats = period_data(project, args.start, args.end)
        rid = args.start + '_' + args.end
        path = project / 'data/period-reports' / (rid + '.json')
        previous = read(path) if path.exists() else {}
        if previous:
            archive = project / 'data/revisions/period-reports' / f'{rid}-r{previous["revision"]:03d}.json'
            if not archive.exists():
                write(archive, previous)
        r = {**previous, 'title': args.start + ' — ' + args.end + ' 训练回顾', 'start': args.start, 'end': args.end, 'revision': previous.get('revision', 0) + 1}
        if args.analysis:
            r['analysis'] = read(args.analysis)
            r['analysis_source_revisions'] = stats['source_revisions']
        write(path, r)
        print('period report:', 'reports/' + rid + '.html')
    groups = render(project)
    print('workout pages:', len(groups))


if __name__ == '__main__':
    main()
