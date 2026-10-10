import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from backend.graph import build_graph, initial_state
from tests_python.test_migration import answer, response, session
from evaluation.development_checks import CASES, session_for


def choice():
    return dict(status='answered',
        paragraphs=[dict(text='今天就吃一份热汤面吧，配一份青菜，简单吃好这一顿。', citations=[])], limitations=[])


@pytest.mark.parametrize('question', ['我今天中午吃啥好', '头发长了，今天要不要去理发？'])
async def test_small_first_reading_cannot_skip_cast_evidence(question):
    async def model(messages, tools=None):
        assert tools is None
        return response(choice())
    result = await build_graph(InMemorySaver(), model).ainvoke(
        initial_state(session(), question, 'initial'),
        {'configurable': {'thread_id': 'practical-first'}})
    assert result['require_citations'] and 'MISSING_ANSWER_CITATION' in result['errors']
    assert result['repairs'] == 1


async def test_small_first_reading_accepts_short_meaning_and_practical_advice():
    async def model(messages, tools=None):
        assert tools is None
        context = json.loads(messages[1]['content'])
        value = choice()
        grounded = answer(context['参考原文'][0])['paragraphs'][0]
        value['paragraphs'][0]['text'] += grounded['text']
        value['paragraphs'][0]['citations'] = grounded['citations']
        return response(value)
    result = await build_graph(InMemorySaver(), model).ainvoke(
        initial_state(session(), '我今天中午吃啥好', 'initial'),
        {'configurable': {'thread_id': 'short-reading'}})
    assert result['require_citations'] and not result['errors']
    assert result['repairs'] == result['attempts'] == 0


async def test_same_topic_followup_can_carry_forward_without_repeating_citation():
    from backend.knowledge import initial_evidence
    s = session()
    s['question'] = '我今天中午吃啥好'
    evidence = initial_evidence(s['cast'], s['question'])
    s['turns'] = [dict(status='completed', question=s['question'], evidence=evidence, answer=answer(evidence[0]))]

    async def model(messages, tools=None):
        if tools:
            return response({'ready': True})
        context = json.loads(messages[1]['content'])
        assert context['近期对话'][0]['user'] == s['question']
        return response(choice())
    result = await build_graph(InMemorySaver(), model).ainvoke(
        initial_state(s, '那我具体该吃什么', 'followup'),
        {'configurable': {'thread_id': 'small-followup'}})
    assert not result['require_citations'] and not result['errors']
    assert result['attempts'] == 0
    # Whether the prose actually carries the meaning forward is a semantic review.


async def test_explicit_traditional_request_still_requires_sources():
    async def model(messages, tools=None):
        return response({'ready': True}) if tools else response(choice())
    result = await build_graph(InMemorySaver(), model).ainvoke(
        initial_state(session(), '请解释本卦卦辞', 'initial'),
        {'configurable': {'thread_id': 'explicit-source'}})
    assert result['require_citations'] and 'MISSING_ANSWER_CITATION' in result['errors']
    assert result['repairs'] == 1


@pytest.mark.parametrize('case_id,original,changed', [
    ('project-decision',40,7),('project-next-step',40,7),
    ('lunch-choice',43,1),('lunch-specific',43,1)])
def test_user_failure_fixtures_preserve_question_and_cast(case_id,original,changed):
    case = next(c for c in CASES if c['id'] == case_id)
    s = session_for(case)
    first = next(c for c in CASES if c['session'] == case['session'])
    assert s['question'] == first['question']
    assert s['cast']['original']['number'] == original
    assert s['cast']['changed']['number'] == changed


def test_empty_list_defaults_do_not_require_report_tail_but_require_body():
    from backend.validation import validate_answer
    value = dict(status='answered', paragraphs=[dict(text='吃一份你平时接受的热汤面。')])
    normalized, errors = validate_answer(value, [], require_citations=False)
    assert not errors and normalized['paragraphs'][0]['citations'] == [] and normalized['limitations'] == []
    value['status'] = 'insufficient'
    value['paragraphs'][0]['text'] = '我还没有找到你所问的原文，暂时无法确认。'
    assert validate_answer(value, [], require_citations=False)[0]
    value['paragraphs'] = []
    assert validate_answer(value, [], require_citations=False)[0] is None
