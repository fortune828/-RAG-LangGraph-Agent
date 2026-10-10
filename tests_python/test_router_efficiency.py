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


async def test_exact_target_skips_model_planning_and_keeps_full_records():
    events, requests = [], []

    async def chat(messages, tools=None):
        requests.append((messages, tools))
        assert tools is None
        return generation_response(messages)

    result = await run_question('请给出本卦第二爻原文。', chat, events)
    assert len(requests) == 1 and result['attempts'] == 1 and not result['errors']
    assert any(t.get('code') == 'EXACT_TARGET_POLICY' for t in result['trace'])
    assert any(t.get('code') == 'EXPLICIT_TARGETS_SATISFIED' for t in result['trace'])
    assert [e['phase'] for e in events if e['kind'] == 'model_phase'] == ['generate']
    tool_event = next(e for e in events if e['kind'] == 'tool_result')
    stored = next(e for e in result['evidence'] if e['id'] == 'zy-1-line-2')
    assert tool_event['returnedResult']['evidence'][0] == stored
    assert tool_event['retrievedResult']['evidence'][0] == stored
    assert 'source' in stored and 'textOriginal' in stored
    model_tool = json.loads(next(m['content'] for m in result['messages'] if m['role'] == 'tool'))
    assert model_tool['evidence'] == routing_evidence([stored])
    assert model_tool['evidence'][0]['text'] == stored['text']


async def test_model_router_receives_source_projection_and_reduced_coverage_only():
    events, requests = [], []

    async def chat(messages, tools=None):
        requests.append((messages, tools))
        if tools:
            return response({'ready': True}) if any(m['role'] == 'tool' for m in messages) else response(calls=[tool_call(line=2)])
        return generation_response(messages)

    result = await run_question('请查查当前卦的相关资料。', chat, events)
    assert len(requests) == 3 and result['attempts'] == 1 and not result['errors']
    assert [bool(tools) for _, tools in requests] == [True, True, False]
    assert result['targets'] == [] and result['scope_ids'] == [1]
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


async def test_exact_targets_are_batched_without_adding_unrequested_lines():
    requests, events = [], []

    async def chat(messages, tools=None):
        requests.append((messages, tools))
        assert tools is None
        return generation_response(messages)

    result = await run_question('请给出本卦第三爻和第四爻原文。', chat, events)
    assert len(requests) == 1 and result['attempts'] == 1 and not result['errors']
    assert {'zy-1-line-3', 'zy-1-line-4'} <= {e['id'] for e in result['evidence']}
    tool_results = [e for e in events if e['kind'] == 'tool_result']
    assert len(tool_results) == 1 and tool_results[0]['args']['lineIndices'] == [3, 4]
    assert {e['id'] for e in tool_results[0]['returnedResult']['evidence']} == {'zy-1-line-3', 'zy-1-line-4'}
    assert [e['phase'] for e in events if e['kind'] == 'model_phase'] == ['generate']


async def test_life_followup_with_exact_source_target_still_uses_router():
    requests = []

    async def chat(messages, tools=None):
        requests.append(bool(tools))
        if tools:
            return response(calls=[tool_call(line=2)])
        return generation_response(messages)

    result = await run_question('请给出本卦第二爻原文，再谈谈这对我换工作有什么启发。', chat)
    assert requests == [True, False] and result['attempts'] == 1 and not result['errors']
    assert not any(t.get('code') == 'EXACT_TARGET_POLICY' for t in result['trace'])


async def test_implicit_new_topic_with_exact_line_can_handoff_before_tools():
    saved = session()
    saved['question'] = '我今天午饭吃什么？'
    calls = []

    async def chat(messages, tools=None):
        calls.append(bool(tools))
        assert json.loads(messages[1]['content'])['question'] == saved['question']
        return response({'newTopic': True}, calls=[tool_call(line=2)])

    result = await build_graph(InMemorySaver(), chat).ainvoke(
        initial_state(saved, '我和对象分手了，本卦第二爻对复合这件事有什么启发？', 'followup'),
        {'configurable': {'thread_id': 'exact-line-new-topic'}})
    assert calls == [True] and result['attempts'] == 0
    assert result['trace'][-1]['code'] == 'NEW_CORE_QUESTION'
    assert result['session']['cast'] == saved['cast']
    assert not any(t.get('code') == 'EXACT_TARGET_POLICY' for t in result['trace'])


async def test_multi_hexagram_line_request_keeps_router_and_exposes_old_parser_limit():
    requests = []

    async def chat(messages, tools=None):
        requests.append(bool(tools))
        if tools:
            return response({'ready': True})
        return response(dict(status='insufficient', paragraphs=[dict(text='需要确认每个卦对应的所问爻位。', citations=[])]))

    result = await run_question('请给出乾卦九二和坤卦六三的爻辞原文。', chat)
    assert requests[0] is True and result['attempts'] == 0
    assert not any(t.get('code') == 'EXACT_TARGET_POLICY' for t in result['trace'])
    # The guard deliberately does not claim to repair parsing or the final
    # required-target checks: two requested pairs still become four targets.
    assert {(t['hexagramId'], t['line']) for t in result['targets']} == {(1, 2), (1, 3), (2, 2), (2, 3)}


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
            return response(calls=[tool_call(h=2 if routed == 1 else 1, line=2)]) if routed <= 2 else response({'ready': True})
        return generation_response(messages)

    result = await run_question('请查查当前卦的相关资料。', chat, events)
    assert routed == 3 and result['attempts'] == 2 and not result['errors']
    assert result['targets'] == [] and result['scope_ids'] == [1]
    results = [e for e in events if e['kind'] == 'tool_result']
    assert results[0]['executionStatus'] == 'rejected'
    assert results[0]['returnedResult']['error'] == 'EVIDENCE_SCOPE_DENIED'
    assert all(e['hexagramId'] == 1 for e in result['evidence'])
    assert results[1]['executionStatus'] == 'ok'
    assert len(results) == 2


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
