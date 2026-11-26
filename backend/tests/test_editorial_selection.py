"""Synthetic-only regression coverage for full-transcript editorial selection."""

from collections import Counter

import pytest

from backend.services import editorial_selection
from backend.services.editorial_recut import CHECK_KEYS
from backend.services.local_captions import CaptionError


TEXT = [
    '先从一个常见问题说起，纸面反馈为什么很难用起来？',
    '小组把观察写到同一张共享表。',
    '小组把观察写到同一张共享表。',
    '课堂试走时，团队把两组采集表并排复盘。',
    '真正有价值的反馈，来自同一问题的前后对照。',
    '接着把一周内各自碰到的情况按主题归类。',
    '最后记录现场达成的可复用做法。',
    '只有任务可比才下结论，不能拿不同作业打比方。',
    '另列一张表备注异常情况。',
    '按同一条件再试一次，这才回答了最开始的问题。',
]


def cue_id(topic, line):
    return f'c{topic * len(TEXT) + line + 1:05}'


def source(count):
    return [
        {'id': cue_id(topic, line), 'start': topic*160 + line*3 + .2,
         'end': topic*160 + line*3 + 2.7, 'text': value, 'original_text': value}
        for topic in range(count) for line, value in enumerate(TEXT)
    ]


def candidate(topic, strengths=(2, 2, 2, 2, 2)):
    return {'title': f'Synthetic topic {topic}', 'topic': [cue_id(topic, 0), cue_id(topic, 9)],
            'kind': 'contrast', 'audience': '正在整理小组反馈的授课者',
            'payoff': '用前后对照识别一下哪些反馈有帮助',
            'reason': '反转了反馈只是收集意见的常见想法，且给出对照方法。',
            'criteria': dict(zip(editorial_selection.CRITERIA, strengths)),
            'evidence': {key: [cue_id(topic, n)]*2
                         for key, n in (('claim', 4), ('support', 3), ('closing', 9))}}


class FixtureModel:
    def __init__(self, screen=None, *, fail=None, checks=None, hook=True):
        self.screen = screen or (lambda topic: [candidate(topic)])
        self.fail = fail or (lambda *_: False)
        self.checks = checks or (lambda *_: True)
        self.hook = hook
        self.events = []
        self.progress = []

    @staticmethod
    def topic(rows):
        return (int(rows[0][0][1:]) - 1)//len(TEXT)

    def __call__(self, _system, payload, *, schema, max_tokens):
        assert schema and max_tokens
        stage = payload['stage']
        self.events.append((stage, payload))
        if self.fail(stage, payload):
            raise TimeoutError('synthetic timeout')
        if stage == 'read':
            first = payload['core_first']
            return {'note': f'Section {first}. Late context: ask for comparable results.',
                    'anchor': [first, first]}
        if stage == 'synthesis':
            return {'summary': '; '.join(n['last'] for n in payload['notes'])}
        if stage == 'screen':
            topic = (int(payload['core_first'][1:]) - 1)//len(TEXT)
            return {'chapters': self.screen(topic)}
        if stage == 'body':
            topic = self.topic(payload['rows'])
            return {'drop': [{'span': [cue_id(topic, line)]*2, 'kind': 'repetition'}
                             for line in (2, 3, 7)]}
        if stage == 'hook':
            topic = self.topic(payload['kept_runs'][0])
            return {'hook': [cue_id(topic, 4)] * 2 if self.hook else None}
        assert stage in {'body_review', 'review'}
        accepted = self.checks(stage, payload)
        return dict.fromkeys(CHECK_KEYS, accepted)

    def measure(self, scopes):
        self.events.append(('measure', scopes))
        # Confirming a gap after the opening question does not make it expendable.
        return [(round(start + a, 3), round(start + b, 3))
                for start, _ in scopes for a, b in ((2.65, 3.03), (17.65, 18.03))]

    def run(self, count, scope=None):
        cues = source(count)
        duration = cues[-1]['end'] + 1
        return editorial_selection.select_edits(
            cues, scope or (0, duration), duration, self, self.measure,
            lambda *row: self.progress.append(row),
        )

    def for_stage(self, name):
        return [payload for stage, payload in self.events if stage == name]


def test_reads_all_scoped_cues_then_compares_later_topics_with_the_same_rubric():
    fixture = FixtureModel(lambda i: [
        candidate(i, strengths=((2, 1, 1, 2, 1), (2, 2, 0, 2, 2), (2, 2, 2, 2, 2))[i]),
    ])
    result = fixture.run(3)
    coverage = result['reading']['coverage']
    assert coverage == {'complete': True, 'selected_cues': 30, 'read_cues': 30, 'screened_cues': 30,
                        'segments_total': 3, 'segments_read': 3, 'segments_screened': 3}
    assert [(r['first'], r['last']) for r in result['reading']['sections']] == [
        (cue_id(i, 0), cue_id(i, 9)) for i in range(3)]
    for stage in ('read', 'screen'):
        core_cues = []
        for row in fixture.for_stage(stage):
            ids = [r[0] for r in row['rows']]
            core_cues += ids[ids.index(row['core_first']): ids.index(row['core_last'])+1]
        assert core_cues == [c['id'] for c in source(3)]
    assert [stage for stage, _ in fixture.events[:8]] == [
        'read', 'read', 'read', 'synthesis', 'screen', 'screen', 'screen', 'measure']
    final_cue = cue_id(2, 9)
    assert final_cue in fixture.for_stage('screen')[0]['whole_summary']
    assert all(p['whole_summary'] == result['reading']['summary'] for p in fixture.for_stage('screen'))
    assert fixture.for_stage('screen')[0]['nearby_notes'][-1]['last'] == cue_id(1, 9)
    late, early = result['chapters']
    assert late['first'] == cue_id(2, 0) and early['first'] == cue_id(0, 0)
    assert result['recommended'] == late['id']
    assert result['selection'] == {'rubric_version': 'spread_v1', 'discovered': 3, 'qualified': 2,
                                   'below_threshold': 1, 'invalid': 0, 'reviewed': 2, 'eligible': 2,
                                   'limited': False}
    assert fixture.for_stage('measure') == [[(c['start'], c['end']) for c in (late, early)]]
    for chapter, i in ((late, 2), (early, 0)):
        assert chapter['selection']['reason']
        assert chapter['selection']['evidence'] == {
            role: {'first': cue_id(i, n), 'last': cue_id(i, n), 'start': i*160+n*3+.2,
                   'end': i*160+n*3+2.7, 'quote': TEXT[n]}
            for role, n in (('claim', 4), ('support', 3), ('closing', 9))
        }
    steps = [done for done, _, _ in fixture.progress]
    assert steps == sorted(steps)
    done, total, stage = fixture.progress[-1]
    assert stage == 'done' and done == total


@pytest.mark.parametrize('problem', [
    'unknown', 'overlap', 'backwards', 'outside', 'closure_too_early',
    'wrong_rubric_type', 'unsupported', 'low_value',
])
def test_untraceable_or_weak_proposal_is_not_given_audio_analysis(problem):
    bad = candidate(0)
    c = bad['evidence']
    if problem == 'unknown':
        c['support'] = ['c99999']*2
    elif problem == 'overlap':
        c['support'] = c['claim'][:]
    elif problem == 'backwards':
        c['support'] = [cue_id(0, 3), cue_id(0, 2)]
    elif problem == 'outside':
        bad['topic'][0] = cue_id(0, 2)
        c['support'] = [cue_id(0, 1)]*2
    elif problem == 'closure_too_early':
        c['closing'] = [cue_id(0, 2)]*2
    elif problem == 'wrong_rubric_type':
        bad['criteria']['independence'] = True
    elif problem == 'unsupported':
        bad['criteria']['evidence'] = 0
    else:
        bad['criteria'] = dict.fromkeys(editorial_selection.CRITERIA, 1)
    fixture = FixtureModel(lambda _: [bad])
    result = fixture.run(1)
    assert result['reading']['coverage']['complete'] is True
    assert result['selection']['discovered'] == 1
    assert result['selection']['qualified'] == result['selection']['eligible'] == 0
    assert result['selection']['invalid'] + result['selection']['below_threshold'] == 1
    assert result['chapters'] == [] and result['recommended'] is None
    assert not fixture.for_stage('measure') and not fixture.for_stage('body')


def test_measures_selected_pauses_before_body_work_keeps_question_pause_and_supported_claim():
    fixture = FixtureModel()
    chapter, = fixture.run(1)['chapters']
    assert [s for s, _ in fixture.events] == [
        'read', 'synthesis', 'screen', 'measure', 'body', 'body_review', 'hook', 'review']
    body, = fixture.for_stage('body')
    assert body['keep_cues'] == [cue_id(0, n) for n in (3, 4, 9)]
    assert body['quiet_trims'] == [{'start': 17.85, 'end': 18.14}]
    review, = fixture.for_stage('body_review')
    assert review['edit'][0][0] != 'hook'
    assert review['deleted'] == [[cue_id(0, 2)]*2]
    final, = fixture.for_stage('review')
    assert final['edit'][0][0] == 'hook'
    assert final['edit'][0][1] == [cue_id(0, 4), TEXT[4]]
    played = [r[0] for segment in final['edit'] for r in segment[1:]]
    assert played.count(cue_id(0, 4)) == 1
    assert cue_id(0, 3) in played and cue_id(0, 7) in played
    assert cue_id(0, 2) not in played
    assert chapter['editorial_checks']['clean_body_confirmed'] is True
    assert chapter['editorial_checks']['outcome'] == 'hook_accepted'
    assert chapter['removed_silence'] == body['quiet_trims']


def test_review_must_clear_body_before_opening_and_failure_rechecks_reverted_body():
    def checks(stage, payload):
        return stage == 'body_review' and not payload['deleted']

    fixture = FixtureModel(checks=checks)
    chapter, = fixture.run(1)['chapters']
    assert [s for s, _ in fixture.events][-5:] == ['body', 'body_review', 'body_review', 'hook', 'review']
    rejected, fallback = fixture.for_stage('body_review')
    assert rejected['deleted'] and not fallback['deleted']
    assert chapter['dropped'] == []
    assert chapter['mode'] == 'source_order'
    assert chapter['editorial_review'] == 'passed'
    assert chapter['editorial_checks']['clean_body_confirmed'] is True
    assert chapter['editorial_checks']['outcome'] == 'body_accepted'
    assert not chapter['editorial_checks']['reorder_checks']['meaning']


def test_never_attempts_moved_hook_when_both_body_checks_fail():
    fixture = FixtureModel(checks=lambda *_: False)
    result = fixture.run(1)
    chapter, = result['chapters']
    assert not fixture.for_stage('hook') and not fixture.for_stage('review')
    assert len(fixture.for_stage('body_review')) == 2
    assert chapter['editorial_review'] == 'needs_review'
    assert chapter['editorial_checks']['clean_body_confirmed'] is False
    assert result['selection']['eligible'] == 0 and result['recommended'] is None


@pytest.mark.parametrize('stop_stage', ['read', 'synthesis', 'screen'])
def test_incomplete_read_synthesis_or_screen_explicitly_forbids_preview(stop_stage):
    def fails(stage, payload):
        return stage == stop_stage and payload.get('core_first', cue_id(1, 0)) == cue_id(1, 0)

    fixture = FixtureModel(fail=fails)
    result = fixture.run(2)
    assert result['reading']['coverage']['complete'] is False
    assert result['chapters'] == [] and result['recommended'] is None
    assert not fixture.for_stage('measure')
    attempts = Counter(p.get('core_first') for p in fixture.for_stage(stop_stage))
    assert attempts[None if stop_stage == 'synthesis' else cue_id(1, 0)] == 2
    if stop_stage == 'read':
        assert result['reading']['coverage']['read_cues'] == len(TEXT)
        assert not fixture.for_stage('synthesis') and not fixture.for_stage('screen')
    elif stop_stage == 'synthesis':
        assert result['reading']['coverage']['read_cues'] == 2*len(TEXT)
        assert not fixture.for_stage('screen')
    else:
        assert result['reading']['coverage']['screened_cues'] == len(TEXT)
        assert result['selection']['qualified'] == 1  # Not secretly shown as complete.


def test_hierarchical_synthesis_covers_all_source_ranges_and_bounded_ranked_review(monkeypatch):
    monkeypatch.setattr(editorial_selection, 'SYNTH_GROUP', 2)
    monkeypatch.setattr(editorial_selection, 'MAX_REVIEW', 2)
    fixture = FixtureModel(lambda i: [candidate(i, (2, 2, 2, 2, 2) if i >= 3 else (2, 1, 2, 1, 2))]
                           + ([candidate(i)] if i == 4 else []), hook=False)
    result = fixture.run(5)
    groups = fixture.for_stage('synthesis')
    assert [len(p['notes']) for p in groups] == [2, 2, 1, 2, 1, 2]
    count, = [p for p in groups if p['notes'][0]['first'] == cue_id(0, 0)
              and p['notes'][-1]['last'] == cue_id(4, 9)]
    assert count == groups[-1]
    assert result['reading']['coverage']['read_cues'] == 50
    assert result['selection'] == {'rubric_version': 'spread_v1', 'discovered': 6, 'qualified': 6,
                                   'below_threshold': 0, 'invalid': 0, 'reviewed': 2, 'eligible': 2,
                                   'limited': True}
    assert [c['first'] for c in result['chapters']] == [cue_id(3, 0), cue_id(4, 0)]
    assert [c['editorial_checks']['outcome'] for c in result['chapters']] == ['body_accepted']*2


def test_missing_local_model_failure_is_not_swallowed_into_a_valid_no_candidate_report():
    def unavailable(_system, _payload, **_options):
        raise CaptionError('model_unavailable')

    with pytest.raises(CaptionError, match='model_unavailable'):
        editorial_selection.select_edits(source(1), (0, 30), 30, unavailable,
                                          lambda _: pytest.fail('audio must stay untouched'))


def test_manual_scope_counts_selected_cues_and_does_not_decode_excluded_ranges():
    fixture = FixtureModel(hook=False)
    result = fixture.run(3, scope=(155, 201))
    assert result['scope'] == [155, 201]
    assert result['reading']['coverage']['selected_cues'] == len(TEXT)
    for stage in ('read', 'screen', 'body'):
        payload, = fixture.for_stage(stage)
        assert [r[0] for r in payload['rows']] == [cue_id(1, n) for n in range(len(TEXT))]
    scopes, = fixture.for_stage('measure')
    assert scopes == [(160.2, 189.7)]


def test_one_invalid_read_retries_the_same_core_without_duplicating_coverage():
    attempts = []

    def delayed(stage, payload):
        if stage == 'read':
            attempts.append(payload['core_first'])
            return attempts == [cue_id(0, 0)]
        return False

    fixture = FixtureModel(fail=delayed, hook=False)
    result = fixture.run(2)
    assert attempts == [cue_id(0, 0), cue_id(0, 0), cue_id(1, 0)]
    assert result['reading']['coverage'] == {
        'complete': True, 'selected_cues': 20, 'read_cues': 20, 'screened_cues': 20,
        'segments_total': 2, 'segments_read': 2, 'segments_screened': 2,
    }
    assert result['selection']['eligible'] == 2 and result['failed_windows'] == 0
