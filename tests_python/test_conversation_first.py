import pytest
from langgraph.checkpoint.memory import InMemorySaver
from backend.graph import build_graph, initial_state
from tests_python.test_migration import response, session
from evaluation.development_checks import CASES, session_for


def choice():
    return dict(status='answered', summary='今天就吃一份热汤面吧。',
        paragraphs=[dict(kind='reflection', text='配一份青菜，简单吃好这一顿。', citations=[])], limitations=[])


async def test_practical_initial_answer_needs_no_decorative_citation():
    async def model(messages, tools=None):
        assert tools is None
        return response(choice())
    result = await build_graph(InMemorySaver(), model).ainvoke(
        initial_state(session(), '我今天中午吃啥好', 'initial'),
        {'configurable': {'thread_id': 'practical-first'}})
    assert not result['errors'] and result['answer']['status'] == 'answered'
    assert not result['require_citations']


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


def test_empty_list_defaults_preserve_evidence_and_insufficient_checks():
    from backend.validation import validate_answer
    value = dict(status='answered', summary='给一个具体选择。',
        paragraphs=[dict(kind='reflection', text='吃一份你平时接受的热汤面。')])
    normalized, errors = validate_answer(value, [], require_citations=False)
    assert not errors and normalized['paragraphs'][0]['citations'] == [] and normalized['limitations'] == []
    value['paragraphs'][0]['kind'] = 'interpretation'
    assert validate_answer(value, [], require_citations=False)[1]
    value['status'] = 'insufficient'
    assert 'MISSING_LIMITATION' in validate_answer(value, [], require_citations=False)[1]
