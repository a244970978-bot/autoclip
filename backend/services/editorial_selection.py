"""Whole-transcript reading and evidence-based short-clip selection, offline."""
from __future__ import annotations

from backend.services.editorial_recut import SYSTEM, SPAN, _array, _object, _overlap, _refine, _rows
from backend.services.editorial_read_cache import READING_CONTRACT
from backend.services.local_captions import CaptionError
from backend.services.local_reassembly import _span, build_candidate, windows

VERSION = 'editorial_v2'
RUBRIC = 'spread_v1'
CRITERIA = ('relevance', 'novelty', 'evidence', 'payoff', 'independence')
EVIDENCE = ('claim', 'support', 'closing')
KINDS = ('insight', 'contrast', 'how_to', 'case', 'emotion')
SYNTH_GROUP = 16
MAX_REVIEW = 12
MAX_TOPIC_TEXT = 3600

READ = SYSTEM + """
通读 rows，包括相邻语境，但只给 core_first 到 core_last 作一条客观阅读笔记。写明它在讲哪个问题、提出什么判断或例子、有何限制；如果只是过渡就说明其作用。不要选题、评传播力或写标题。anchor 是这段主旨在核心内最能核对的一到几条字幕的首尾 ID。note 用一到两句自然中文，最多 150 字；不要把总结写成原话。
"""
SYNTHESIS = SYSTEM + """
按时间通读给出的全部阅读笔记。写出完整材料如何展开、核心主题、关键观点及它们的关系，注明重要前提、后文修正或话题切换。不要把早期举例当最终结论，不编造笔记里没有的事实。中间合并保留能影响后期选材的反差和限制；最终总览给后续逐段核对原文用，并非引文。只给 summary，最多 420 字。
"""
SCREEN = SYSTEM + """
全文笔记和全片主线已建立。请回到这个窗口的原字幕，找真正有短视频传播价值的一到两个独立主题；没有就返回空，不因时间靠前、词激烈或能剪顺而入选。注意全文主线以及上下文有无限定、反例或反转，保留从问题到结论的完整区间。topic 中点必须在 core_first 和 core_last 之间。
哪些人遇到什么具体问题？相对常见说法得到什么新认识、意外反差、可用行动或故事转折？有什么原话支撑？这条结束时兑现什么？陌生观众不看原片前后文能否听懂？五项标准分别给 0/1/2：0 无，1 有但普通，2 清晰具体；criterion 是按下列固定键返回证据判断，不是播放量预测。只有观点、证据、收束和整体边界都能在原话核对时才返回。支持材料必须不同于观点本身，禁止把一句口号同时当作论点、根据和结尾。
kind 仅可为 insight（新判断）、contrast（对比）、how_to（方法）、case（有辨识度的案例）、emotion（情绪或故事转折）。title 是便于识别的主题，不是引文；audience 是推断的受众，payoff 是可兑现的收获，reason 用一句话说明为何值得剪。evidence 分别用原字幕首尾 ID 引用 claim（观点）、support（原因、方法或例子）、closing（兑现或有结尾）。三段证据在 topic 内、彼此不重叠；closing 在最后。每个证据一到数条字幕，禁止自行写引文或时间戳。单条字幕就把同一 ID 填两次。
五项的 2 档具体含义：relevance 有明确人群在具体场景中的真实问题；novelty 有被原文解释的反差、新判断或故事转折；evidence 有具体事例、理由、操作或对照，不是把口号重复一次；payoff 结尾兑现问题，观众得到可用做法或认识；independence 陌生人不依赖缺失的前文也懂，全文中改变意思的限制仍在区间里。不把夸张语气等同于新意。
"""
READ_SCHEMA = _object({'note': {'type': 'string', 'maxLength': 150}, 'anchor': SPAN})
SYNTH_SCHEMA = _object({'summary': {'type': 'string', 'maxLength': 420}})
SCREEN_SCHEMA = _object({'chapters': _array(_object({
    'title': {'type': 'string', 'maxLength': 70}, 'topic': SPAN, 'kind': {'enum': list(KINDS)},
    'audience': {'type': 'string', 'maxLength': 55}, 'payoff': {'type': 'string', 'maxLength': 100},
    'reason': {'type': 'string', 'maxLength': 140},
    'criteria': _object({key: {'enum': [0, 1, 2]} for key in CRITERIA}),
    'evidence': _object({key: SPAN for key in EVIDENCE}),
}), 2)})


def _string(item, name, length):
    value = item.get(name)
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= length:
        raise ValueError('invalid editorial text')
    return ' '.join(value.split())


def _read_note(raw, rows, a, b):
    if not isinstance(raw, dict):
        raise ValueError('unread section')
    ids = {c['id']: i for i, c in enumerate(rows)}
    x, y = _span(raw.get('anchor'), ids)
    if not a <= x <= y < b:
        raise ValueError('reading anchor out of core')
    return {'start': rows[a]['start'], 'end': rows[b-1]['end'],
            'first': rows[a]['id'], 'last': rows[b-1]['id'],
            'anchor': [rows[x]['id'], rows[y]['id']], 'summary': _string(raw, 'note', 150)}


def _summary(raw):
    if not isinstance(raw, dict):
        raise ValueError('no synthesis')
    return _string(raw, 'summary', 420)


def _rank(criteria):
    # Explicit editorial rubric. Scores are not calibrated probabilities.
    return (sum(criteria.values()), criteria['evidence'], criteria['payoff'], criteria['relevance'],
            criteria['novelty'], criteria['independence'])


def _candidate(raw, rows, a, b, cues, duration, scope):
    if not isinstance(raw, dict):
        raise ValueError('invalid candidate')
    ids = {c['id']: i for i, c in enumerate(rows)}
    x, y = _span(raw.get('topic'), ids)
    if not (a <= (x+y)//2 < b and sum(len(r['text']) for r in rows[x:y+1]) <= MAX_TOPIC_TEXT):
        raise ValueError('candidate outside scope')
    criteria = raw.get('criteria')
    if (not isinstance(criteria, dict) or set(criteria) != set(CRITERIA)
            or any(type(n) is not int or not 0 <= n <= 2 for n in criteria.values())
            or any(criteria[k] == 0 for k in CRITERIA if k != 'novelty') or sum(criteria.values()) < 7):
        raise ValueError('low editorial value')
    if raw.get('kind') not in KINDS:
        raise ValueError('unknown editorial kind')
    citations = raw.get('evidence')
    if not isinstance(citations, dict) or set(citations) != set(EVIDENCE):
        raise ValueError('missing citation')
    evidence, occupied = {}, set()
    ranges = {k: _span(citations[k], ids) for k in EVIDENCE}
    if ranges['closing'][0] <= max(ranges[k][1] for k in ('claim', 'support')):
        raise ValueError('closing must follow claim and support')
    for k, (m, n) in ranges.items():
        quote = ' '.join(c['text'] for c in rows[m:n+1])
        indexes = set(range(m, n+1))
        if (not x <= m <= n <= y or n-m > 9 or len(quote) > 440
                or rows[n]['end'] - rows[m]['start'] > 33 or (occupied & indexes)):
            raise ValueError('evidence must be exact, short, disjoint and within topic')
        occupied.update(indexes)
        evidence[k] = {'first': rows[m]['id'], 'last': rows[n]['id'],
                       'start': rows[m]['start'], 'end': rows[n]['end'], 'quote': quote}
    plan = build_candidate({'title': _string(raw, 'title', 70), 'topic': raw['topic']},
                           cues, [], duration, scope=scope)
    fields = {k: _string(raw, k, n) for k, n in (('audience', 55), ('payoff', 100), ('reason', 140))}
    return {'plan': plan, 'appeal': sum(criteria.values()),
            'selection': {**fields, 'kind': raw['kind'], 'criteria': criteria, 'evidence': evidence}}


def _synthesis_calls(count):
    calls = 1
    while count > SYNTH_GROUP:
        count = (count + SYNTH_GROUP - 1) // SYNTH_GROUP
        calls += count
    return calls


def _known_reading(prior, sections):
    """Revalidate every core and anchor. Cached screening/priority is never trusted."""
    if not isinstance(prior, dict) or prior.get('contract') != READING_CONTRACT:
        return None
    try:
        summary = _summary(prior)
        notes = prior['sections']
        bounds = prior['coverage']
        amount = sum(b-a for _, a, b in sections)
        if (not isinstance(notes, list) or len(notes) != len(sections)
                or not bounds['selected_cues'] == bounds['read_cues'] == amount
                or not bounds['segments_read'] == bounds['segments_total'] == len(notes)):
            return None
        checked = []
        for note, (rows, a, b) in zip(notes, sections):
            v = _read_note({'note': note['summary'], 'anchor': note['anchor']}, rows, a, b)
            if any(v[k] != note[k] for k in ('first', 'last', 'start', 'end')):
                return None
            checked.append(v)
        return checked, summary
    except (KeyError, TypeError, ValueError):
        return None


def select_edits(cues, scope, duration, call, measure, progress=lambda *_: None, *, prior_reading=None):
    """Read every cue and synthesize context before selecting or measuring any audio."""
    source = [c for c in cues if c['start'] >= scope[0] and c['end'] <= scope[1]]
    sections = list(windows(source))
    if not sections:
        raise CaptionError('analysis_no_cues')
    count = len(sections)
    done = 0
    total = 4*count + 2*_synthesis_calls(count) + 1 + MAX_REVIEW*8 + 20

    def invoke(stage, instruction, payload, schema, max_tokens):
        nonlocal done
        progress(done, total, stage)
        try:
            return call(instruction, {**payload, 'stage': stage}, schema=schema, max_tokens=max_tokens)
        except CaptionError:
            raise
        except (ValueError, TimeoutError):
            return None
        finally:
            done += 1

    def retry(stage, instruction, payload, schema, max_tokens, parse):
        for _ in range(2):
            answer = invoke(stage, instruction, payload, schema, max_tokens)
            try:
                return parse(answer)
            except (ValueError, TypeError, KeyError):
                pass
        return None

    reading, read_cues, screened_cues, scanned, skipped = [], 0, 0, 0, 0
    summary = None
    discovered, qualified, below_threshold, invalid = 0, [], 0, 0
    cached = _known_reading(prior_reading, sections)
    comparison = 'rubric_only'

    def report(chapters, complete, *, limited=False):
        progress(total, total, 'done')
        eligible = [c['id'] for c in chapters if c['editorial_review'] == 'passed']
        return {'analysis_version': VERSION, 'scope': list(scope), 'window_count': count,
                'failed_windows': skipped, 'needs_review': True,
                'comparison': comparison,
                'reading': {'contract': READING_CONTRACT, 'reused': cached is not None,
                            'summary': summary, 'sections': reading,
                            'coverage': {'complete': complete, 'selected_cues': len(source),
                                         'read_cues': read_cues, 'screened_cues': screened_cues,
                                         'segments_total': count, 'segments_read': len(reading),
                                         'segments_screened': scanned}},
                'selection': {'rubric_version': RUBRIC, 'discovered': discovered,
                              'qualified': len(qualified), 'below_threshold': below_threshold,
                              'invalid': invalid, 'reviewed': len(chapters),
                              'eligible': len(eligible), 'limited': limited},
                'chapters': chapters, 'recommended': eligible[0] if eligible else None}

    if cached is not None:
        reading, summary = cached
        read_cues = len(source)
    for rows, a, b in ([] if cached is not None else sections):
        payload = {'rows': _rows(rows), 'core_first': rows[a]['id'], 'core_last': rows[b-1]['id']}
        note = retry('read', READ, payload, READ_SCHEMA, 265, lambda raw: _read_note(raw, rows, a, b))
        if note is not None:
            reading.append(note)
            read_cues += b - a
    if len(reading) != count:
        skipped = count - len(reading)
        return report([], False)

    nodes = reading
    while cached is None and len(nodes) > SYNTH_GROUP:
        reduced = []
        for i in range(0, len(nodes), SYNTH_GROUP):
            group = nodes[i:i+SYNTH_GROUP]
            note = retry('synthesis', SYNTHESIS, {'notes': group}, SYNTH_SCHEMA, 650, _summary)
            if note is None:
                return report([], False)
            reduced.append({'first': group[0]['first'], 'last': group[-1]['last'],
                            'start': group[0]['start'], 'end': group[-1]['end'], 'summary': note})
        nodes = reduced
    if cached is None:
        summary = retry('synthesis', SYNTHESIS, {'notes': nodes}, SYNTH_SCHEMA, 650, _summary)
    if summary is None:
        return report([], False)

    def screen_result(raw):
        if not isinstance(raw, dict) or not isinstance(raw.get('chapters'), list) or len(raw['chapters']) > 2:
            raise ValueError('unreviewed selection window')
        return raw['chapters']

    for index, (rows, a, b) in enumerate(sections):
        notes = reading[max(0, index-1): index+2]
        payload = {'whole_summary': summary, 'nearby_notes': notes, 'rows': _rows(rows),
                   'core_first': rows[a]['id'], 'core_last': rows[b-1]['id']}
        suggestions = retry('screen', SCREEN, payload, SCREEN_SCHEMA, 850, screen_result)
        if suggestions is None:
            skipped += 1
            continue
        scanned += 1
        screened_cues += b - a
        discovered += len(suggestions)
        for raw in suggestions:
            try:
                qualified.append(_candidate(raw, rows, a, b, cues, duration, scope))
            except (ValueError, TypeError, KeyError) as problem:
                if str(problem) == 'low editorial value':
                    below_threshold += 1
                else:
                    invalid += 1
    if skipped:
        return report([], False)

    ranked = sorted(qualified, key=lambda c: _rank(c['selection']['criteria']), reverse=True)
    from backend.services.editorial_comparison import compare_candidates
    ranked, comparison = compare_candidates(ranked, summary, invoke)
    shortlist = []
    for candidate in ranked:
        if not any(_overlap(candidate['plan'], other['plan']) for other in shortlist):
            shortlist.append(candidate)
    if not shortlist:
        return report([], True)
    selected = shortlist[:MAX_REVIEW]
    progress(done, total, 'audio')
    pauses = sorted(measure([(c['plan']['start'], c['plan']['end']) for c in selected]))
    done += 1
    ids = {c['id']: i for i, c in enumerate(cues)}
    chapters = [{**_refine(c, cues, ids, scope, duration, pauses, invoke, verify_body=True), 'id': f'T{i+1:02}'}
                for i, c in enumerate(selected)]
    return report(chapters, True, limited=len(shortlist) > len(selected))
