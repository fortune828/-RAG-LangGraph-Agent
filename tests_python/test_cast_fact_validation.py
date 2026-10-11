"""Narrow deterministic body-fact checks are not a semantic quality judge."""
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from backend.domain import calculate_hexagram
from backend.graph import build_graph, initial_state
from backend.validation import explicit_cast_errors, validate_answer
from tests_python.test_migration import response, session


CAST = calculate_hexagram([9, 8, 8, 8, 7, 7])  # 益 -> 观, only 初九 changes.


@pytest.mark.parametrize('text, error', [
    ('本卦为坤。', 'original'),
    ('变卦仍为风雷益。', 'changed'),
    ('本卦无动爻。', 'moving'),
    ('动爻为九二。', 'moving'),
    ('动爻为初九、六二。', 'moving'),
    ('本卦的初爻是初六。', 'line'),
    ('变卦的初爻为初九。', 'line'),
])
def test_explicit_wrong_facts_are_detected_and_located(text, error):
    issues = []
    value = dict(status='answered', paragraphs=[dict(text=text, citations=[])], limitations=[])
    answer, errors = validate_answer(value, [], require_citations=False, cast=CAST, issues=issues)
    assert answer is None
    assert any(code.startswith('CAST_FACT_MISMATCH:' + error + ':') for code in errors)
    assert issues[0]['path'] == ['paragraphs', 0, 'text']


@pytest.mark.parametrize('text', [
    '本卦为风雷益，变卦为风地观。动爻为初九。',
    '本卦为益卦。变卦是观卦。',
    '本卦初爻是初九，变卦初爻是初六。',
    '如果本卦为坤，可以另作讨论。',
    '假如重新起卦结果为乾，本卦为乾。',
    '如果按另一个案例讨论，变卦为坤。',
    '本卦并不是坤。',
    '本卦为坤吗？',
    '本卦为坤，真的吗？',
    '用户之前说“本卦为坤”，这里需要核实。',
    '用户之前说‘请注意，本卦为坤，无动爻’，这里需要核实。',
    '“变卦为坤。”是一个错误说法。',
    '本卦的初爻不是初六，而是初九。',
    '我无法确认你说的“无动爻”。',
])
def test_correct_facts_and_nonassertive_mentions_are_not_rejected(text):
    assert explicit_cast_errors(text, CAST) == []


def test_static_and_all_moving_casts_use_actual_values_and_line_polarity():
    assert explicit_cast_errors('本卦无动爻。', calculate_hexagram([7] * 6)) == []
    assert explicit_cast_errors('动爻为初九。', calculate_hexagram([7] * 6))
    text = '动爻为初六、六二、六三、六四、六五和上六。变卦第六爻为上九。'
    assert explicit_cast_errors(text, calculate_hexagram([6] * 6)) == []


def test_quoted_error_does_not_exempt_adjacent_unquoted_error():
    errors = explicit_cast_errors('“本卦为坤”是旧说法。变卦为坤。', CAST)
    assert len(errors) == 1 and ':changed:' in errors[0]
    errors = explicit_cast_errors('如果重新起卦，变卦为坤。本卦为坤。', CAST)
    assert len(errors) == 1 and ':original:' in errors[0]


async def test_graph_repairs_explicit_cast_fact_using_same_cast_once():
    s = session([9, 8, 8, 8, 7, 7])
    generations = 0

    async def model(messages, tools=None):
        nonlocal generations
        if tools:
            return response({'ready': True})
        generations += 1
        if generations == 1:
            text = '本卦为坤。'
        else:
            assert 'CAST_FACT_MISMATCH:original:expected=风雷益' in messages[-1]['content']
            text = '本卦为风雷益。你刚补充的限制可以一起考虑。'
        return response(dict(status='answered', paragraphs=[dict(text=text, citations=[])], limitations=[]))

    result = await build_graph(InMemorySaver(), model).ainvoke(
        initial_state(s, '如果我的预算再少一点呢？', 'followup'),
        {'configurable': {'thread_id': 'cast-fact-repair'}})
    assert generations == 2 and result['repairs'] == 1 and not result['errors']
    assert result['session']['cast'] == s['cast']
    assert any('CAST_FACT_MISMATCH:' in event.get('code', '') for event in result['trace'])
