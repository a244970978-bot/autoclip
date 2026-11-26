"""Contracts for the staged local edit; only synthetic, made-up subtitle text."""
import pytest

from backend.services.editorial_recut import propose
from backend.services.local_captions import CaptionError, caption_track

TEXT = [
    '做复盘之前，先交代这个项目的背景。',
    '小组把反馈整理到同一张表上。',
    '小组把反馈整理到同一张表上。',
    '具体流程是先收集，再看有哪些原因。',
    '真正有用的反馈，来自每周的一次对照。',
    '再把团队现场碰到的难题列出来。',
    '每周把工单讲给参与者听。',
    '只有样本可比才可下结论，不能忽略差异。',
    '我们把例外问题另列一张表。',
    '用对照验证，再回来看是否解决了问题。',
]


@pytest.fixture
def story():
    return [{'id': f'c{i+1:05}', 'start': i*3+.2, 'end': i*3+2.7, 'text': s, 'original_text': s}
            for i, s in enumerate(TEXT)]


def plan(story, *, bad_hook=False, bad_review=False, bad_body_review=False, bad_scan=False, invalid_body=False,
         choose_safe_retry=False, decline_hook=False):
    calls = []

    def response(_system, payload, *, schema=None, max_tokens=None):
        assert schema and max_tokens
        stage = payload['stage']
        calls.append((stage, payload))
        if stage == 'scan':
            return None if bad_scan else {'chapters': [{'title': '复盘示例',
                                  'topic': ['c00001', 'c00010'], 'appeal': 3}]}
        if stage == 'body':
            if invalid_body:
                return {'drop': 'malformed'}
            return {'drop': [{'span': ['c00003', 'c00003'], 'kind': 'repetition'},
                             {'span': ['c00008', 'c00008'], 'kind': 'digression'}]}
        if stage == 'hook':
            retained = [r[0] for seq in payload['kept_runs'] for r in seq]
            assert 'c00003' not in retained and 'c00008' in retained
            if choose_safe_retry and payload.get('hook_options'):
                assert all('c00003' not in pair for pair in payload['hook_options'])
                return {'hook': payload['hook_options'][0]}
            if decline_hook:
                return {'hook': None}
            x = 'c00003' if bad_hook else 'c00005'
            return {'hook': [x, x]}
        if stage == 'review':
            shuffled = payload['edit'][0][0] == 'hook'
            flags = not (bad_review if shuffled else bad_body_review)
            return {'flow': flags, 'meaning': flags, 'opening': flags, 'ending': flags}
        assert False, 'unexpected stage'

    phases = []
    result = propose(story, (0,30), 30, [(8.77,9.13),(17.77,18.13)], response,
                     lambda done,total,phase: phases.append((done,total,phase)))
    return result, calls, phases


def test_audio_and_captions_follow_retained_body_then_hook_then_exact_review(story):
    r, calls, progress = plan(story)
    c, = r['chapters']
    assert [s for s,_ in calls] == ['scan','body','hook','review']
    assert [s for _,_,s in progress] == ['scan','body','hook','review','done']
    offered = calls[2][1]['suggested_hooks']
    assert len(offered) <= 8
    assert {'span': ['c00005', 'c00005'], 'quote': TEXT[4],
            'before': ['c00004', TEXT[3]], 'after': ['c00006', TEXT[5]]} in offered
    assert all('c00003' not in choice['span'] for choice in offered)
    final_request = calls[-1][1]
    assert final_request['edit'][0][1] == ['c00005', TEXT[4]]
    assert sum(row[0]=='c00005' for s in final_request['edit'] for row in s[1:]) == 1
    assert ['c00003','c00003'] in final_request['deleted']
    assert c['dropped'][0]['reason']=='repetition'
    assert c['last']=='c00010' and c['scenes'][-1]['last']=='c00010'
    assert c['removed_silence'] and c['editorial_review']=='passed'
    assert r['recommended']==c['id'] and r['needs_review']
    assert c['editorial_checks']['basis']=='transcript'
    draft = {'id':'captions','title':'test','language':'source','aspect':'original','subtitles':True,
             'scenes':[{k:s[k] for k in ('id','start','end')} for s in c['scenes']]}
    track = caption_track(story,draft,{'width':320,'height':180})
    texts = [s['text'] for s in track['output']]
    assert '真正有用' in ''.join(texts[:3])
    assert '只有样本可比' in ''.join(texts)
    assert 0 < track['duration'] < 27


@pytest.mark.parametrize('bad_hook,bad_review',[ (True,False),(False,True) ])
def test_rejects_deleted_hook_or_bad_reorder_and_reviewed_body_stays_in_source_order(story,bad_hook,bad_review):
    r, calls, _ = plan(story,bad_hook=bad_hook,bad_review=bad_review)
    c, = r['chapters']
    assert c['mode']=='source_order' and c['editorial_checks']['outcome']=='body_accepted'
    assert c['editorial_review']=='passed'
    assert all(a['end']<=b['start'] for a,b in zip(c['scenes'],c['scenes'][1:]))
    assert calls[-1][1]['edit'][0][0]!='hook'


def test_failed_review_reverts_to_unmodified_continuous_candidate(story):
    r, _, _ = plan(story,bad_review=True,bad_body_review=True)
    c, = r['chapters']
    assert c['mode']=='source_order' and len(c['scenes'])==1 and c['dropped']==[]
    assert c['editorial_review']=='needs_review' and r['recommended'] is None


def test_bad_hook_can_be_replaced_by_one_safe_option_choice(story):
    r, calls, _ = plan(story,bad_hook=True,choose_safe_retry=True)
    assert [s for s,_ in calls] == ['scan','body','hook','hook','review']
    c, = r['chapters']
    assert c['mode']=='hook_first' and c['editorial_review']=='passed'
    assert c['editorial_checks']['hook_choice']=='safe_options'


def test_explicit_hook_decline_does_not_force_reorder(story):
    result, calls, _ = plan(story, decline_hook=True)
    chapter, = result['chapters']
    assert [stage for stage, _ in calls] == ['scan', 'body', 'hook', 'review']
    assert chapter['mode'] == 'source_order'
    assert chapter['editorial_checks']['outcome'] == 'body_accepted'


def test_missing_body_plan_does_not_silently_choose_hook(story):
    r, calls, _ = plan(story,invalid_body=True)
    assert [s for s,_ in calls]==['scan','body']
    assert r['recommended'] is None and r['chapters'][0]['editorial_review']=='needs_review'


def test_unusable_scan_is_failure_not_a_certified_edit(story):
    with pytest.raises(CaptionError,match='analysis_failed'):
        plan(story,bad_scan=True)


@pytest.mark.parametrize('good_topic,expected_visited', [(6, 6), (7, 7), (None, 12)])
def test_widens_topic_search_only_when_first_batch_has_no_safe_moved_hook(story, good_topic, expected_visited):
    cues = [{**cue, 'id': f'c{i*len(story)+n+1:05}', 'start': cue['start'] + i*160,
             'end': cue['end'] + i*160} for i in range(14) for n, cue in enumerate(story)]
    visited, progress = [], []

    def respond(_system, payload, **_kwargs):
        stage = payload['stage']
        if stage == 'scan':
            return {'chapters': [{'title': '合成选题', 'topic': [payload['core_first'], payload['core_last']],
                                  'appeal': 3}]}
        if stage == 'body':
            first = int(payload['rows'][0][0][1:]); visited.append(first)
            return {'drop': [{'span': [f'c{first+2:05}'] * 2, 'kind': 'repetition'}]}
        if stage == 'hook':
            first = int(payload['kept_runs'][0][0][0][1:])
            return {'hook': [f'c{first+4:05}'] * 2 if good_topic == (first-1)//len(story)+1 else None}
        assert stage == 'review'
        return dict.fromkeys(['flow', 'meaning', 'opening', 'ending'], True)

    # The optional second batch scans all six candidates, unless only one was available.
    bounded = cues[:7*len(story)] if good_topic is not None else cues
    duration = 990 if good_topic is not None else 2120
    result = propose(bounded, (0, duration), duration, [], respond, lambda *args: progress.append(args))
    assert visited == list(range(1, expected_visited*len(story), len(story)))
    assert [x[0] for x in progress] == sorted(x[0] for x in progress)
    assert progress[-1][0] == progress[-1][1] and progress[-1][2] == 'done'
    moved = [chapter['id'] for chapter in result['chapters'] if chapter['mode'] == 'hook_first']
    if good_topic is not None:
        assert moved == [result['recommended']] == [f'T{good_topic:02}']
    else:
        assert not moved
