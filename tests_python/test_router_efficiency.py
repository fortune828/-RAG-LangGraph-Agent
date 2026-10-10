"""Reduce routing work without changing evidence, scope or recovery contracts."""
import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from backend import graph as graph_module
from backend.graph import build_graph, initial_state, routing_evidence
from backend.knowledge import coverage
from tests_python.test_migration import answer, response, session, tool_call


def generation_response(messages):
    evidence = json.loads(messages[1]['content'])['参考原文']
    if not evidence:
        return response(dict(status='insufficient', paragraphs=[dict(text='尚未取得对应资料。', citations=[])]))
    return response(dict(status='answered', paragraphs=[answer(e)['paragraphs'][0] for e in evidence]))


async def run_question(question, chat, events=None):
    return await build_graph(InMemorySaver(), chat, observe=events.append if events is not None else None).ainvoke(
        initial_state(session(), question, 'followup'), {'configurable': {'thread_id': question}})


async def test_exact_target_skips_ready_confirmation_and_keeps_full_records():
    events, requests = [], []

    async def chat(messages, tools=None):
        requests.append((messages, tools))
        if tools:
            return response(calls=[tool_call(line=2)])
        return generation_response(messages)

    result = await run_question('请给出本卦第二爻原文。', chat, events)
    assert len(requests) == 2 and result['attempts'] == 1 and not result['errors']
    assert any(t.get('code') == 'EXPLICIT_TARGETS_SATISFIED' for t in result['trace'])
    prepared = next(e['evidence'] for e in events if e['kind'] == 'prepare')
    context = json.loads(requests[0][0][1]['content'])
    assert context['evidence'] == routing_evidence(prepared)
    for original, projected in zip(prepared, context['evidence']):
        assert original['text'] == projected['text'] and original['id'] == projected['id']
        assert 'source' in original and 'source' not in projected
        assert 'textOriginal' in original and 'textOriginal' not in projected
    full_coverage = coverage(result['release'])
    for author, data in full_coverage['authors'].items():
        assert data['anchorIds']
        assert context['knowledgeCoverage']['authors'][author] == {
            key: value for key, value in data.items() if key != 'anchorIds'}
    tool_event = next(e for e in events if e['kind'] == 'tool_result')
    stored = next(e for e in result['evidence'] if e['id'] == 'zy-1-line-2')
    assert tool_event['returnedResult']['evidence'][0] == stored
    assert tool_event['retrievedResult']['evidence'][0] == stored
    model_tool = json.loads(next(m['content'] for m in result['messages'] if m['role'] == 'tool'))
    assert model_tool['evidence'] == routing_evidence([stored])
    assert model_tool['evidence'][0]['text'] == stored['text']


async def test_partial_targets_continue_until_all_requested_evidence_is_accepted():
    routed = 0

    async def chat(messages, tools=None):
        nonlocal routed
        if tools:
            routed += 1
            return response(calls=[tool_call(line=3 if routed == 1 else 4)])
        return generation_response(messages)

    result = await run_question('请给出本卦第三爻和第四爻原文。', chat)
    assert routed == 2 and result['attempts'] == 2 and not result['errors']
    assert {'zy-1-line-3', 'zy-1-line-4'} <= {e['id'] for e in result['evidence']}


@pytest.mark.parametrize('question,filters', [
    ('朱熹如何解释本卦的取舍？', dict(authorIds=['zhu-xi'])),
    ('《周易本义》如何解释本卦的取舍？', dict(workIds=['zhouyi-benyi'])),
])
async def test_author_or_work_only_target_keeps_relevance_decision(question, filters):
    routed = 0

    async def chat(messages, tools=None):
        nonlocal routed
        if tools:
            routed += 1
            return response(calls=[tool_call(args=dict(hexagramId=1, sections=['line'], lineIndices=[2],
                layers=['commentary'], **filters))]) if routed == 1 else response({'ready': True})
        return generation_response(messages)

    result = await run_question(question, chat)
    assert routed == 2 and result['attempts'] == 1 and not result['errors']
    assert result['targets'] and all(not target.get('section') for target in result['targets'])
    assert not any(t.get('code') == 'EXPLICIT_TARGETS_SATISFIED' for t in result['trace'])


@pytest.mark.parametrize('section', ['line', 'line_xiang', 'special'])
async def test_line_section_without_specific_position_keeps_router(monkeypatch, section):
    # Check the shortcut contract independently of today's target parser: a
    # broad future/custom line target must not be satisfied by any single line.
    monkeypatch.setattr(graph_module, 'requested_targets', lambda question, cast: [
        dict(hexagramId=1, section=section, line=None, layer='classic')])
    routed = 0

    async def chat(messages, tools=None):
        nonlocal routed
        if tools:
            routed += 1
            return response(calls=[tool_call(args=dict(hexagramId=1, sections=[section],
                lineIndices=[7 if section == 'special' else 2]))]) if routed == 1 else response({'ready': True})
        return generation_response(messages)

    result = await run_question('请查询本卦原文。', chat)
    assert routed == 2 and result['attempts'] == 1 and not result['errors']
    assert not any(t.get('code') == 'EXPLICIT_TARGETS_SATISFIED' for t in result['trace'])


async def test_scope_rejection_continues_recovery_without_accepting_other_hexagram():
    routed, events = 0, []

    async def chat(messages, tools=None):
        nonlocal routed
        if tools:
            routed += 1
            return response(calls=[tool_call(h=2 if routed == 1 else 1, line=2)])
        return generation_response(messages)

    result = await run_question('请给出本卦第二爻原文。', chat, events)
    assert routed == 2 and not result['errors']
    results = [e for e in events if e['kind'] == 'tool_result']
    assert results[0]['executionStatus'] == 'rejected'
    assert results[0]['returnedResult']['error'] == 'EVIDENCE_SCOPE_DENIED'
    assert all(e['hexagramId'] == 1 for e in result['evidence'])
    assert results[1]['executionStatus'] == 'ok'


@pytest.mark.parametrize('question,arguments', [
    ('朱熹是否说过，大意是可以走出去见一见有德之人？',
     dict(hexagramId=1, sections=['line'], lineIndices=[2], layers=['commentary'], authorIds=['zhu-xi'])),
    ('那这一步应该怎么考虑？', dict(hexagramId=1, sections=['line'], lineIndices=[2])),
    ('朱熹《尚待核实的别名》对本卦第二爻的解释',
     dict(hexagramId=1, sections=['line'], lineIndices=[2], layers=['commentary'], authorIds=['zhu-xi'])),
])
async def test_discovery_no_targets_and_unresolved_books_keep_router_judgment(question, arguments):
    routed = 0

    async def chat(messages, tools=None):
        nonlocal routed
        if tools:
            routed += 1
            return response(calls=[tool_call(args=arguments)]) if routed == 1 else response({'ready': True})
        return generation_response(messages)

    result = await run_question(question, chat)
    assert routed >= 2 and not result['errors']
    assert not any(t.get('code') == 'EXPLICIT_TARGETS_SATISFIED' for t in result['trace'])


async def test_empty_result_does_not_skip_recovery_even_when_target_was_present():
    routed = 0

    async def chat(messages, tools=None):
        nonlocal routed
        if tools:
            routed += 1
            return response(calls=[tool_call(args=dict(hexagramId=1, sections=['line'], lineIndices=[2],
                layers=['commentary'], authorIds=['jiao-xun']))]) if routed == 1 else response({'ready': True})
        return generation_response(messages)

    result = await run_question('请给出本卦卦辞原文。', chat)
    assert routed == 2 and not result['errors']
    assert any(t['step'] == 'tool' and t['status'] == 'empty' for t in result['trace'])
    assert not any(t.get('code') == 'EXPLICIT_TARGETS_SATISFIED' for t in result['trace'])


async def test_retrieved_but_dropped_target_does_not_trigger_shortcut(monkeypatch):
    original_pack = graph_module.pack_evidence

    def drop_target(required, optional, **kwargs):
        if any(e['id'] == 'zy-1-line-2' for e in optional):
            return list(required), ['zy-1-line-2']
        return original_pack(required, optional, **kwargs)

    monkeypatch.setattr(graph_module, 'pack_evidence', drop_target)
    routed, events = 0, []

    async def chat(messages, tools=None):
        nonlocal routed
        if tools:
            routed += 1
            return response(calls=[tool_call(line=2)]) if routed == 1 else response({'ready': True})
        return generation_response(messages)

    result = await run_question('请给出本卦第二爻原文。', chat, events)
    assert routed >= 2 and not result['errors'] and result['answer']['status'] == 'insufficient'
    tool_event = next(e for e in events if e['kind'] == 'tool_result')
    assert tool_event['retrievedResult']['evidence'][0]['id'] == 'zy-1-line-2'
    assert tool_event['returnedResult']['evidence'] == []
    assert not any(t.get('code') == 'EXPLICIT_TARGETS_SATISFIED' for t in result['trace'])


async def test_book_mentioned_in_life_discussion_is_not_an_unresolved_source_request():
    async def chat(messages, tools=None):
        if tools:
            context = json.loads(messages[1]['content'])
            assert context['intent'] == 'explanation_followup'
            assert context['unresolvedBooks'] == []
            return response({'ready': True})
        context = json.loads(messages[1]['content'])
        assert not any('书名尚未确认' in note for note in context['资料说明'])
        return generation_response(messages)

    result = await run_question('最近在读《一本尚未识别的书》，想借这卦聊换工作。', chat)
    assert not result['errors']
