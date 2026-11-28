"""Model transport regression: synthetic source cues only, no model/network."""
from backend.services.editorial_units import grounded_calls, make_units
from backend.services.editorial_selection import SCREEN_SCHEMA, _candidate


def fixture():
    return [{'id': f'c{i:05}', 'start': 3.0*i, 'end': 3.0*i+2.6, 'text': f'合成的第{i}段话。'}
            for i in range(12)]


def message(cues):
    return {'stage': 'screen', 'rows': [[c['id'], c['text']] for c in cues],
            'core_first': cues[1]['id'], 'core_last': cues[-2]['id'],
            'nearby_notes': [], 'whole_summary': '一个已有阅读笔记的合成问题。'}


def card():
    return {'opening': 2, 'claim': 7, 'support': 4, 'closing': 10,
            'title': '合成短片', 'kind': 'case', 'audience': '合成受众',
            'reason': '一段经验有具体例子。', 'payoff': '看完能理解做法。',
            'criteria': dict.fromkeys(('relevance', 'novelty', 'evidence', 'payoff', 'independence'), 2)}


def test_finite_speaking_units_preserve_every_cue_and_no_generated_timestamps():
    cues = fixture()
    def call(_, payload, *, schema, max_tokens):
        assert payload['units'][0] == [1, 0., 2.6, cues[0]['text']]
        assert payload['core'] == [2, 11]
        assert 'topic' not in schema['properties']['chapters']['items']['properties']
        return {'chapters': [card(), {**card(), 'claim': 4}]}
    result = grounded_calls(call, cues)('', message(cues), schema=SCREEN_SCHEMA, max_tokens=700)
    good, bad = result['chapters']
    assert not bad
    assert good['topic'] == [cues[1]['id'], cues[9]['id']]
    assert good['evidence']['claim'] == [cues[6]['id']] * 2
    check = _candidate(good, cues, 1, 11, cues, 36, (0, 36))
    assert check['selection']['evidence']['support']['quote'] == cues[3]['text']
    assert check['selection']['evidence']['closing']['start'] == 27


def test_sparse_punctuation_does_not_destroy_core_boundaries_or_merge_long_silence():
    cues = fixture()
    for cue in cues:
        cue['text'] = '组合'
    units = make_units(cues, cues[2]['id'], cues[8]['id'])
    assert [c['id'] for part in units for c in part] == [c['id'] for c in cues]
    assert cues[2]['id'] in [u[0]['id'] for u in units]
    assert cues[8]['id'] in [u[-1]['id'] for u in units]
    assert all(len(u) <= 9 and u[-1]['end'] - u[0]['start'] <= 8 for u in units)
