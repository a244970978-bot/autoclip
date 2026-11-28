"""Second pass on the actual retained cue order; never use transcript text as audio timing."""
import math
import re
from pathlib import Path
from subprocess import SubprocessError

from backend.services.editorial_recut import (
    CHECK_KEYS, QUESTION_END, REVIEW, REVIEW_SCHEMA, SYSTEM, _array, _object, _review_payload, _trim_pauses,
)

PURE = re.compile(r'[嗯啊呃哦额唔]{1,3}[，。,.…]?\Z')
REPLY = re.compile(r'[吗呢么嘛?？][？?。！!」”]*$')
VERSION = 'polish_1'


def invoke(call, system, payload, schema):
    try:
        return call(system, payload, schema=schema, max_tokens=180)
    except (ValueError, TimeoutError):
        return None


def refine_clip(c, cues, ids, quiet, call):
    evidence = c['selection']['evidence']
    protected = {i for p in evidence.values() for i in range(ids[p['first']], ids[p['last']] + 1)}
    options = {}
    for s in c['scenes']:
        for i in range(ids[s['first']]+1, ids[s['last']]):
            l, m, r = cues[i-1:i+2]
            if (i in protected or not PURE.fullmatch(m['text'].strip()) or m['text'] != m.get('original_text', m['text'])
                    or REPLY.search(l['text'].strip()) or not .10 <= m['end']-m['start'] <= .85
                    or l['end'] > m['start'] or m['end'] > r['start']):
                continue
            options[m['id']] = [l['text'], m['text'], r['text']]
    drop = set()
    if options:
        answer = invoke(call, SYSTEM+'只选确实无信息的独立迟疑声。问答、确认、惊讶、强调或不确定的全部保留。返回可删项的 id。',
                        {'options': options}, _object({'drop': _array({'type': 'string'}, 8)}))
        if isinstance(answer, dict) and isinstance(answer.get('drop'), list):
            drop = {v for v in answer['drop'][:8] if isinstance(v, str) and v in options}
    scenes, omissions = [], []
    for s in c['scenes']:
        cur = dict(s)
        for i in range(ids[s['first']]+1, ids[s['last']]):
            m = cues[i]
            if m['id'] not in drop or len(omissions)+len(c['scenes']) >= 30:
                continue
            x = min(m['start'], cues[i-1]['end']+.075)
            y = max(m['end'], cues[i+1]['start']-.085)
            if x-cur['start'] < .48 or s['end']-y < .48 or ids[cur['first']] >= i:
                continue
            scenes.append(dict(cur, end=x, last=cues[i-1]['id']))
            cur.update(start=y, first=cues[i+1]['id'])
            omissions.append(dict(first=m['id'], last=m['id'], start=x, end=y, reason='filler'))
        scenes.append(cur)
    # Whisper timestamps often meet even when the recording is quiet. Only relax
    # subtitle edges inside measured silence, never through a cited or short cue.
    edges = [dict(v) for v in cues]
    for s in scenes:
        for i in range(ids[s['first']], ids[s['last']]):
            l,r = cues[i:i+2]
            if i in protected or i+1 in protected or REPLY.search(l['text'].strip()) or QUESTION_END.search(l['text'].strip()):
                continue
            for x,y in quiet:
                if x > l['end']: break
                if x <= l['end'] <= r['start'] <= y and y-x >= .32:
                    edges[i]['end'] = min(edges[i]['end'], max(l['start']+.24, l['end']-.45, x))
                    edges[i+1]['start'] = max(edges[i+1]['start'], min(r['end']-.24, r['start']+.45, y))
                    break
    scenes = [dict(scene, id=f'cut{n+1}') for n, scene in enumerate(scenes)]
    base = dict(c, scenes=scenes, seconds=round(sum(s['end']-s['start'] for s in scenes),2),
                dropped=c['dropped']+omissions, removed_silence=[])
    edited = _trim_pauses(base, edges, ids, quiet, preserve_questions=True)
    def verify(p):
        a,b = ids[c['first']], ids[c['last']]
        payload = _review_payload(p, cues[a:b+1], {'before':[v['text'] for v in cues[max(0,a-2):a]],
                                                  'after':[v['text'] for v in cues[b+1:b+3]]}, cues, ids)
        payload.update(evidence=evidence, joins=[[s['start'],s['end']] for s in p['scenes']])
        v = invoke(call, REVIEW+'这是二检：读最终顺序。特别注意误删语气回应、主题跳转或同一句被拆断。不要从时间推断听感。',
                   payload, REVIEW_SCHEMA)
        return isinstance(v, dict) and all(v.get(k) is True for k in CHECK_KEYS)
    good = verify(edited)
    added = len(edited['removed_silence'])
    if not good and (omissions or added):
        edited, good, omissions, added = dict(c), verify(c), [], 0
    else:
        edited['removed_silence'] = c['removed_silence'] + edited['removed_silence']
    return dict(edited, second_pass={'version':VERSION,'passed':good,'filler':len(omissions),'pause':added},
                editorial_review='passed' if good else 'needs_review')


def portrait(video, s, run):
    helper = Path(__file__).resolve().parents[2]/'.local/prepared/local-portrait'
    if helper.is_symlink() or not helper.is_file() or s['end']-s['start'] < 1.5:
        return {}
    try:
        result = run([str(helper),str(video),str(s['start']),str(s['end'])], capture_output=True, timeout=18, check=True)
        x,y = map(float,result.stdout.split())
        if not all(math.isfinite(v) and 0 <= v <= 1 for v in (x,y)): return {}
        return {'punch':1.16, 'focus_x':round(x,4), 'focus_y':round(y,4)}
    except (OSError, SubprocessError, ValueError):
        return {}


def polish_report(report, cues, measure, call, frame, progress):
    eligible = [c for c in report['chapters'] if c.get('editorial_review') == 'passed']
    quiet = sorted(measure([(c['start'], c['end']) for c in eligible])) if eligible else []
    ids = {v['id']:i for i,v in enumerate(cues)}
    revised = {}
    for n,c in enumerate(eligible):
        progress(n,len(eligible),'polish')
        item = refine_clip(c,cues,ids,quiet,call)
        # Punch in on the opening shot only, return to wide at the next edit.
        if item['second_pass']['passed']:
            s = item['scenes'][0]
            item['scenes'] = [dict(s, **frame(s)), *item['scenes'][1:]]
        revised[c['id']] = item
    results = [revised.get(c['id'],c) for c in report['chapters']]
    ok = [c['id'] for c in results if c.get('second_pass',{}).get('passed') is True]
    return dict(report, chapters=results, polish_version=VERSION, recommended=next(iter(ok),None),
                selection=dict(report['selection'],eligible=len(ok)))
