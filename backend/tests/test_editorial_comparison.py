"""Synthetic tests for the grounded, bounded global priority protocol."""

import json
import re

import pytest

from backend.services.editorial_comparison import (
    PANEL_SIZE, compare_candidates, comparison_call_limit,
)
from backend.services.local_captions import CaptionError


BRIEF = '全片先讲常见困境，再用对照和案例说明行动。越符合具体改进场景越优先。'


def grounded_candidate(i):
    return {'plan': {'first': f'c{i:05}', 'last': f'c{i+4:05}',
                     'start': i*160, 'end': i*160+24, 'title': f'Fixture topic {i}'},
            'selection': {'audience': '正在准备同类课的授课者',
                          'payoff': '独立的可验证结论',
                          'reason': '给出对照方法和解释，不靠语气',
                          'criteria': {'relevance': 2, 'evidence': 2, 'payoff': 2},
                          'evidence': {'claim': {'quote': f'Claim {i}'},
                                       'support': {'quote': '陈述例子，作为论点的真实支持。'},
                                       'closing': {'quote': '概括应用条件，而非只喊口号。'}}}}


def comparison_scorer(stage, instruction, payload, schema, max_tokens):
    assert stage == 'compare'
    assert payload['whole_summary'] == BRIEF
    assert '只返回' in instruction
    assert 35 <= max_tokens <= 222
    items = payload['items']
    ids = [it[0] for it in items]
    assert ids == sorted(ids) and all(re.fullmatch(r'p[0-9a-f]{6}', h) for h in ids)
    array = schema['properties']['order']
    assert array['items']['enum'] == ids
    assert array['minItems'] == array['maxItems'] == len(ids)
    assert all(len(it) == 7 for it in items)
    # The model sees grounded excerpts but neither original IDs/timestamps nor rubric scores.
    dumped = json.dumps(payload, ensure_ascii=False)
    assert not any(word in dumped for word in ('c000', '"start"', '"end"', 'criteria', 'Fixture topic'))
    order = sorted(items, key=lambda it: int(it[4].split()[1]), reverse=True)
    return {'order': [it[0] for it in order]}


def test_ranked_comparison_is_source_blinded_and_uses_real_support_from_all_candidates():
    input_order = [grounded_candidate(i) for i in (4, 0, 3, 7, 1)]
    ranked, method = compare_candidates(input_order, BRIEF, comparison_scorer)
    assert method == 'comparative_model'
    assert ranked == sorted(input_order, key=lambda c: c['plan']['start'], reverse=True)
    restored, reordered_method = compare_candidates(reversed(input_order), BRIEF, comparison_scorer)
    assert restored == ranked and reordered_method == method


def test_48_full_candidates_are_compared_in_balanced_panels_and_a_single_playoff():
    seen = []

    def compare(stage, instruction, payload, schema, max_tokens):
        result = comparison_scorer(stage, instruction, payload, schema, max_tokens)
        seen.append({int(it[4].split()[1]) for it in payload['items']})
        return result

    inputs = [grounded_candidate(i) for i in range(48)]
    ranked, method = compare_candidates(inputs, BRIEF, compare)
    assert method == 'comparative_balanced'
    assert len(seen) == comparison_call_limit(48) == 4
    panels, playoff = seen[:-1], seen[-1]
    assert set().union(*panels) == set(range(48))
    assert sum(map(len, panels)) == 48  # Every candidate appears in one and only one first-round panel.
    assert max(map(len, panels)) <= PANEL_SIZE
    assert all(any(i < 8 for i in p) and any(i >= 40 for i in p) for p in panels)
    assert all(max(p) in playoff for p in panels)
    assert ranked[0] == inputs[47]
    assert sorted(r['plan']['first'] for r in ranked) == sorted(c['plan']['first'] for c in inputs)


@pytest.mark.parametrize('kind', ['ids', 'duplicate', 'partial', 'shape', 'timeout'])
def test_invalid_model_rank_permutation_has_honest_unchanged_rubric_fallback(kind):
    def malformed(_stage, _instruction, payload, _schema, _tokens):
        handles = [item[0] for item in payload['items']]
        if kind == 'ids':
            return {'order': ['ghost', *handles[1:]]}
        if kind == 'duplicate':
            return {'order': [handles[0], handles[0], *handles[2:]]}
        if kind == 'partial':
            return {'order': handles[:-1]}
        if kind == 'shape':
            return {'order': handles, 'score': 0.99}
        raise TimeoutError

    baseline = [grounded_candidate(i) for i in (3, 8, 5, 9)]
    assert compare_candidates(baseline, BRIEF, malformed) == (baseline, 'rubric_fallback')


def test_wrong_final_round_falls_back_instead_of_representing_panels_as_global():
    calls = 0

    def malformed(stage, instruction, payload, schema, max_tokens):
        nonlocal calls
        calls += 1
        return comparison_scorer(stage, instruction, payload, schema, max_tokens) if calls < 4 else None

    baseline = [grounded_candidate(i) for i in range(48)]
    assert compare_candidates(baseline, BRIEF, malformed) == (baseline, 'rubric_fallback')
    assert calls == 4


def test_model_integrity_failure_does_not_disguise_itself_as_a_rubric_result():
    def changed(*_):
        raise CaptionError('analysis_model_changed')

    with pytest.raises(CaptionError, match='analysis_model_changed'):
        compare_candidates([grounded_candidate(0), grounded_candidate(1)], BRIEF, changed)


def test_identical_grounded_suggestions_still_have_unique_opaque_handles():
    baseline = [grounded_candidate(9), grounded_candidate(9)]

    def inspect(stage, instruction, payload, schema, max_tokens):
        assert len({r[0] for r in payload['items']}) == 2
        return comparison_scorer(stage, instruction, payload, schema, max_tokens)

    assert compare_candidates(baseline, BRIEF, inspect) == (baseline, 'comparative_model')


@pytest.mark.parametrize('count', [0, 1, (PANEL_SIZE + 1)*PANEL_SIZE])
def test_model_work_remains_bounded_for_identity_or_over_capacity_input(count):
    baseline = [grounded_candidate(i) for i in range(count)]
    assert comparison_call_limit(count) == 0
    actual, method = compare_candidates(baseline, BRIEF, lambda *_: pytest.fail('unexpected invocation'))
    assert actual == baseline
    assert method == ('rubric_only' if count <= 1 else 'rubric_fallback')
