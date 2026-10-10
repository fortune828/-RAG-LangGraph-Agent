"""Candidate answer/tool loop contracts, not a natural-language quality score.

All model responses are scripted or use a local HTTP mock. Retrieval and the
graph, budget, recorder, and citation validator are the actual implementations.
"""
import copy
import json

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from backend import llm
from backend.evaluate import BudgetedChat
from backend.graph import build_graph, initial_state
from backend.knowledge import TOOLS, retrieve_exact
from evaluation.paired import RecordedChat, Recorder
from tests_python.test_migration import answer, response, session, tool_call
from tests_python.test_model_configuration import mock_transport


FOLLOWUP = '我刚确认了录用条件，接下来怎么权衡？'


def practical_answer():
    return dict(status='answered', paragraphs=[dict(text='可以先对照已确认的录用条件。', citations=[])])


async def run_followup(chat, *, question=FOLLOWUP, saved=None, observe=None):
    saved = saved or session()
    graph = build_graph(InMemorySaver(), chat, observe=observe, answer_with_tools=True)
    return await graph.ainvoke(initial_state(saved, question, 'followup'),
                              {'configurable': {'thread_id': 'candidate-loop'}})


async def test_followup_can_answer_once_without_router_or_tool_execution():
    requests, events = [], []
    saved = session()
    before = copy.deepcopy(saved)

    async def chat(messages, tools=None):
        requests.append((messages, tools))
        assert tools == TOOLS
        context = json.loads(messages[1]['content'])
        assert context['当前问题'] == FOLLOWUP
        assert context['卦象']['六爻值'] == saved['cast']['lines']
        assert context['卦象']['爻名'] == ['初九', '九二', '九三', '九四', '九五', '上九']
        return response(practical_answer())

    result = await run_followup(chat, saved=saved, observe=events.append)
    assert result['answer'] == practical_answer() | {'limitations': []}
    assert result['errors'] == [] and result['attempts'] == result['repairs'] == 0
    assert len(requests) == 1 and result['session'] == saved == before
    assert [e['phase'] for e in events if e['kind'] == 'model_phase'] == ['answer_or_tools']
    assert next(e for e in events if e['kind'] == 'prepare')['nextStep'] == 'respond'
    assert not any(e['kind'] == 'tool_request' for e in events)


async def test_tool_transcript_and_accepted_evidence_are_available_before_citing(monkeypatch, tmp_path):
    evidence = retrieve_exact(dict(hexagramId=1, sections=['line'], lineIndices=[2]))['evidence'][0]
    requests = []
    requested = tool_call(line=2)

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            context = json.loads(payload['messages'][1]['content'])
            assert evidence['id'] not in {e['id'] for e in context['参考原文']}
            message = response(calls=[requested])['message'] | {'reasoning_content': 'PRIVATE_TEST_REASONING'}
        else:
            assert payload['messages'][-2]['tool_calls'] == [requested]
            assert payload['messages'][-2]['reasoning_content'] == 'PRIVATE_TEST_REASONING'
            tool_message = payload['messages'][-1]
            assert tool_message['role'] == 'tool' and tool_message['tool_call_id'] == requested['id']
            provided = json.loads(tool_message['content'])['evidence'][0]
            assert provided['id'] == evidence['id'] and provided['text'] == evidence['text']
            message = response(answer(evidence))['message']
        return httpx.Response(200, json={'choices': [{'finish_reason': 'tool_calls' if len(requests) == 1 else 'stop',
            'message': message}], 'usage': {'total_tokens': 20}})

    mock_transport(monkeypatch, handler)
    budget = BudgetedChat(llm.configured_chat(llm.ChatConfig(thinking='enabled', reasoning_effort='low')), max_calls=2)
    recorder = Recorder(tmp_path / 'events.jsonl', 'tool-use', 'candidate')
    result = await run_followup(RecordedChat(budget, recorder), observe=recorder)
    assert result['errors'] == [] and result['attempts'] == 1 and result['repairs'] == 0
    assert result['answer']['paragraphs'][0]['citations'][0]['evidenceId'] == evidence['id']
    assert next(e for e in result['evidence'] if e['id'] == evidence['id']) == evidence
    assert all(payload['tools'] == TOOLS for payload in requests)
    logged = [e for e in recorder.events if e['kind'] == 'model_request']
    assert [e['stage'] for e in logged] == ['answer_or_tools', 'answer_or_tools']
    assert [e['callId'] for e in logged] == [1, 2]
    assert budget.calls == 2 and budget.reported == 40
    assert 'PRIVATE_TEST_REASONING' not in (tmp_path / 'events.jsonl').read_text()


async def test_unretrieved_evidence_cannot_be_cited_and_repair_remains_bounded():
    evidence = retrieve_exact(dict(hexagramId=1, sections=['line'], lineIndices=[2]))['evidence'][0]
    events, requests = [], []

    async def chat(messages, tools=None):
        requests.append((messages, tools))
        return response(answer(evidence))

    result = await run_followup(chat, observe=events.append)
    assert len(requests) == 2 and requests[0][1] == TOOLS and requests[1][1] is None
    assert result['repairs'] == 1 and result['attempts'] == 0
    assert 'UNKNOWN_EVIDENCE:' + evidence['id'] in result['errors']
    validations = [e for e in events if e['kind'] == 'validation']
    assert [e['repairNumber'] for e in validations] == [0, 1]
    assert all(e['answer'] is None for e in validations)
    assert not any(e['id'] == evidence['id'] for e in result['evidence'])


async def test_scope_rejection_is_visible_and_can_recover_in_same_answer_loop():
    events, count = [], 0
    evidence = retrieve_exact(dict(hexagramId=1, sections=['line'], lineIndices=[2]))['evidence'][0]
    saved = session()
    original_cast = copy.deepcopy(saved['cast'])

    async def chat(messages, tools=None):
        nonlocal count
        count += 1
        assert tools == TOOLS
        if count == 1:
            return response(calls=[tool_call(h=2)])
        if count == 2:
            assert json.loads(messages[-1]['content']) == {'error': 'EVIDENCE_SCOPE_DENIED'}
            return response(calls=[tool_call(h=1)])
        return response(answer(evidence))

    result = await run_followup(chat, saved=saved, observe=events.append)
    tools = [e for e in events if e['kind'] == 'tool_result']
    assert count == 3 and result['attempts'] == 2 and result['errors'] == []
    assert [e['executionStatus'] for e in tools] == ['rejected', 'ok']
    assert all(e['budgetCounted'] for e in tools)
    assert all(e['hexagramId'] == 1 for e in result['evidence'])
    assert result['session']['cast'] == saved['cast'] == original_cast


async def test_duplicate_request_is_counted_rejected_and_does_not_duplicate_evidence():
    count, events = 0, []
    evidence = retrieve_exact(dict(hexagramId=1, sections=['line'], lineIndices=[2]))['evidence'][0]

    async def chat(messages, tools=None):
        nonlocal count
        count += 1
        if count == 1:
            return response(calls=[tool_call(line=2)])
        if count == 2:
            # Different JSON key order, same normalized request.
            return response(calls=[tool_call(args=dict(lineIndices=[2], sections=['line'], hexagramId=1))])
        assert json.loads(messages[-1]['content']) == {'error': 'REPEATED_TOOL_CALL'}
        return response(answer(evidence))

    result = await run_followup(chat, observe=events.append)
    tools = [e for e in events if e['kind'] == 'tool_result']
    assert result['errors'] == [] and result['attempts'] == 2
    assert len(result['seen']) == 1
    assert [e['executionStatus'] for e in tools] == ['ok', 'rejected']
    assert [e['attemptsAfter'] for e in tools] == [1, 2]
    assert sum(e['id'] == evidence['id'] for e in result['evidence']) == 1


async def test_three_attempts_exhaust_tools_but_allow_a_final_answer():
    count, events = 0, []
    evidence = retrieve_exact(dict(hexagramId=1, sections=['line'], lineIndices=[2]))['evidence'][0]

    async def chat(messages, tools=None):
        nonlocal count
        count += 1
        if count == 1:
            assert tools == TOOLS
            return response(calls=[tool_call(line=line) for line in (2, 3, 4, 5)])
        assert tools is None
        results = [json.loads(m['content']) for m in messages if m['role'] == 'tool']
        assert len(results) == 4 and results[-1] == {'error': 'TOOL_BUDGET_EXHAUSTED'}
        return response(answer(evidence))

    result = await run_followup(chat, observe=events.append)
    tools = [e for e in events if e['kind'] == 'tool_result']
    assert count == 2 and result['attempts'] == 3 and result['errors'] == []
    assert [e['executionStatus'] for e in tools] == ['ok', 'ok', 'ok', 'rejected']
    assert [e['budgetCounted'] for e in tools] == [True, True, True, False]
    assert [e['attemptsAfter'] for e in tools] == [1, 2, 3, 3]
    ids = {e['id'] for e in result['evidence']}
    assert {'zy-1-line-2', 'zy-1-line-3', 'zy-1-line-4'} <= ids and 'zy-1-line-5' not in ids


async def test_repair_uses_explicit_profile_and_records_answer_phase_not_router(monkeypatch, tmp_path):
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        value = {'bad': True} if len(requests) == 1 else practical_answer()
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': response(value)['message']}],
            **({'usage': {'total_tokens': 25}} if len(requests) == 2 else {})})

    mock_transport(monkeypatch, handler)
    chat = llm.configured_chat(llm.ChatConfig(thinking='enabled', reasoning_effort='low', max_tokens=4096),
        repair_config=llm.ChatConfig(thinking='disabled', max_tokens=2048))
    budget = BudgetedChat(chat, max_calls=2)
    recorder = Recorder(tmp_path / 'events.jsonl', 'repair', 'candidate')
    result = await run_followup(RecordedChat(budget, recorder), observe=recorder)
    assert result['errors'] == [] and result['repairs'] == 1 and result['attempts'] == 0
    assert requests[0]['tools'] == TOOLS and requests[0]['reasoning_effort'] == 'low'
    assert requests[0]['thinking'] == {'type': 'enabled'} and requests[0]['max_tokens'] == 4096
    assert requests[1]['thinking'] == {'type': 'disabled'} and requests[1]['max_tokens'] == 2048
    assert 'reasoning_effort' not in requests[1] and 'tools' not in requests[1]
    assert requests[1]['response_format'] == {'type': 'json_object'}
    assert json.loads(requests[1]['messages'][-2]['content']) == {'bad': True}
    feedback = json.loads(requests[1]['messages'][-1]['content'])['errors']
    assert any(e['path'] == ['paragraphs'] for e in feedback)
    logged = [e for e in recorder.events if e['kind'] == 'model_request']
    assert [e['stage'] for e in logged] == ['answer_or_tools', 'repair']
    assert [e['callId'] for e in logged] == [1, 2]
    assert [e['payload'] for e in logged] == requests
    responses = [e for e in recorder.events if e['kind'] == 'model_result']
    assert responses[0]['response']['usage'] is None
    assert budget.calls == 2 and budget.unknown == 1 and budget.reported == 25


@pytest.mark.parametrize('question,expected_calls', [
    ('医生开的药可以根据卦象停药吗？', 1),
    ('换个话题，我今天中午吃什么？', 0),
])
async def test_risk_and_explicit_new_question_bypass_answer_with_tools(question, expected_calls):
    calls, events = [], []

    async def chat(messages, tools=None):
        calls.append(messages)
        assert tools is None
        context = json.loads(messages[1]['content'])
        assert '卦象' not in context and '参考原文' not in context
        return response(practical_answer())

    result = await run_followup(chat, question=question, observe=events.append)
    assert len(calls) == expected_calls and result['attempts'] == 0 and result['errors'] == []
    assert not any(e['kind'] == 'model_phase' and e['phase'] == 'answer_or_tools' for e in events)
    assert next(e for e in events if e['kind'] == 'prepare')['nextStep'] != 'respond'
    assert result['evidence'] == []


async def test_quote_location_event_retains_original_candidate_and_exact_source():
    arguments = dict(hexagramId=32, sections=['line'], lineIndices=[4], layers=['commentary'], authorIds=['zhu-xi'])
    evidence = retrieve_exact(arguments)['evidence'][0]
    quote = '占者田无所获而凡事亦不得其所求也'
    candidate = dict(status='answered', paragraphs=[dict(text='朱熹写道：“' + quote + '”。',
        citations=[dict(evidenceId=evidence['id'], quote=quote, claim=quote)])])
    original = copy.deepcopy(candidate)
    count, events = 0, []

    async def chat(messages, tools=None):
        nonlocal count
        count += 1
        return response(calls=[tool_call(args=arguments)]) if count == 1 else response(candidate)

    result = await run_followup(chat, saved=session((8, 7, 7, 7, 8, 8)), observe=events.append)
    assert result['errors'] == [] and result['repairs'] == 0 and candidate == original
    validation = next(e for e in events if e['kind'] == 'validation')
    assert validation['candidate'] == original
    assert validation['candidate']['paragraphs'][0]['citations'][0]['quote'] == quote
    resolution = validation['quoteResolutions'][0]
    assert resolution['requestedQuote'] == quote and resolution['evidenceId'] == evidence['id']
    restored = result['answer']['paragraphs'][0]['citations'][0]['quote']
    assert restored == resolution['sourceQuote'] == '占者田无所獲而凡事亦不得其所求也'
    assert evidence['text'][resolution['start']:resolution['end']] == restored
    assert result['answer']['paragraphs'][0]['citations'][0]['claim'] == quote
