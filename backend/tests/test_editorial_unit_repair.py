"""Source-only structural recovery tests using a made-up transcript."""

import pytest

from backend.services.editorial_unit_repair import MENU_LIMIT, recover_cards
from backend.services.local_captions import CaptionError


def fixture(size=45, core=(4, 39)):
    units = [[{'id': f'c{2*i+j+1:05}', 'start': 4*i + 1.7*j, 'end': 4*i + 1.7*j + 1.5,
               'text': f'虚构课程实例第{i+1}组的文字段{j+1}。'} for j in range(2)] for i in range(size)]
    data = {'stage': 'screen', 'whole_summary': '全篇记录一个虚构的课堂活动。',
            'nearby_notes': ['前段描述常见问题，后文说明可以怎么改。'],
            'core': list(core), 'units': [[i, u[0]['start'], u[-1]['end'], ' '.join(c['text'] for c in u)]
                                         for i, u in enumerate(units, 1)]}
    return data, units


def editorial(code):
    return {'code': code, 'title': '人工构造的合成候选', 'kind': 'how_to',
            'audience': '希望复盘课程的授课人员', 'payoff': '获得实际可用的改进办法',
            'reason': '讲清楚如何在课堂用一个办法解决已观察到的问题。',
            'criteria': dict.fromkeys(('relevance', 'novelty', 'evidence', 'payoff', 'independence'), 2)}


def test_repair_offers_bounded_valid_menu_across_core_and_decodes_only_model_choice():
    data, units = fixture()
    malformed = {'opening': 9, 'claim': 11, 'support': 11, 'closing': 17}
    primary_schema = {'properties': {'audience': {'type': 'string', 'maxLength': 17},
                                     'opening': {'type': 'integer'}}}
    calls = []

    def call(_prompt, payload, *, schema, max_tokens):
        calls.append(payload)
        assert payload['stage'] == 'screen_recover' and payload['units'] == data['units']
        menu = payload['menu']
        assert 6 < len(menu) <= MENU_LIMIT
        assert len({tuple(row[1:]) for row in menu}) == len(menu)
        props = schema['properties']['chapters']['items']['properties']
        assert [p[0] for p in menu] == props['code']['enum'] and max_tokens < 900
        assert 'opening' not in props and props['audience']['maxLength'] == 17
        midpoints = []
        for _, o, c, s, z in menu:
            assert 1 <= o <= min(c, s) < max(c, s) < z <= len(units)
            assert 18 <= units[z-1][-1]['end'] - units[o-1][0]['start'] <= 155
            mid = (2*(o-1) + 2*z-1)//2
            assert 2*(data['core'][0]-1) <= mid < 2*data['core'][1]
            midpoints.append(mid)
        assert min(midpoints) < 33 < max(midpoints)
        assert any(o == malformed['opening'] and z == malformed['closing'] for _, o, _, _, z in menu)
        choice = menu[-1]
        return {'chapters': [{**editorial(choice[0]), 'topic': ['fake', 'fake'], 'evidence': {'wrong': 'fake'}}]}

    result, = recover_cards(call, data, units, [malformed, (9, 11, 11, 17)], primary_schema)
    assert len(calls) == 1
    _, o, c, s, z = calls[0]['menu'][-1]
    assert result['topic'] == [units[o-1][0]['id'], units[z-1][-1]['id']]
    assert result['evidence'] == {
        key: [units[i-1][0]['id'], units[i-1][-1]['id']]
        for key, i in (('claim', c), ('support', s), ('closing', z))
    }
    assert 'code' not in result


@pytest.mark.parametrize('bad', ['none', 'unknown', 'bool', 'string', 'missing-fields', 'timeout'])
def test_repair_never_automatically_chooses_something_after_rejected_reply(bad):
    data, units = fixture(10, (1, 10))
    calls = []

    def call(_prompt, payload, **_):
        calls.append(payload)
        i = payload['menu'][0][0]
        if bad == 'none':
            return {'chapters': []}
        if bad == 'timeout':
            raise TimeoutError
        if bad == 'missing-fields':
            return {'chapters': [{'code': i}]}
        return {'chapters': [editorial({'unknown': 999, 'bool': True, 'string': str(i)}[bad])]}

    assert recover_cards(call, data, units, [(99, 99, 99, 99)], []) == []
    assert len(calls) == 1


def test_physical_limits_are_not_relaxed_when_no_menu_is_possible():
    data, units = fixture(4, (1, 4))  # Four units at most cover 15.2 seconds.
    assert recover_cards(lambda *_a, **_k: pytest.fail('invalid span should not reach model'),
                         data, units, [(1, 1, 1, 4)], []) == []


def test_duplicate_option_and_fatal_model_failure_are_treated_differently():
    data, units = fixture(10, (1, 10))

    def same(_prompt, payload, **_):
        card = editorial(payload['menu'][0][0])
        return {'chapters': [card, card]}

    def integrity(*_a, **_k):
        raise CaptionError('analysis_model_changed')

    assert len(recover_cards(same, data, units, [(1, 2, 2, 7)], [])) == 1
    with pytest.raises(CaptionError, match='analysis_model_changed'):
        recover_cards(integrity, data, units, [(1, 2, 2, 7)], [])
