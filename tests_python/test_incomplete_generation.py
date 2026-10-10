"""Length recovery uses the existing repair and shared budget; no live API."""
import json

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from backend import llm
from backend.evaluate import BudgetedChat
from backend.graph import build_graph, initial_state
from evaluation.paired import RecordedChat, Recorder
from tests_python.test_migration import answer, response, session
from tests_python.test_model_configuration import mock_transport


def profile():
    return llm.configured_chat(llm.ChatConfig(thinking='enabled', reasoning_effort='low', max_tokens=64),
        repair_config=llm.ChatConfig(thinking='disabled', max_tokens=128))


def length_response(content, usage=None):
    return httpx.Response(200, json={
        'choices': [{'finish_reason': 'length', 'message': dict(role='assistant', content=content,
            reasoning_content='PRIVATE_INCOMPLETE_REASONING')}], **({'usage': usage} if usage is not None else {})})


async def run_initial(budget, recorder):
    saved = session()
    return await build_graph(InMemorySaver(), RecordedChat(budget, recorder), observe=recorder).ainvoke(
        initial_state(saved, saved['question'], 'initial'), {'configurable': {'thread_id': 'length'}})


@pytest.mark.parametrize('partial', ['partial', '', None, 'valid_json'])
@pytest.mark.parametrize('reported_usage', [True, False])
async def test_primary_length_uses_one_repair_with_visible_partial_and_original_usage(monkeypatch, tmp_path, partial, reported_usage):
    requests, source_content = [], []
    usage = {'prompt_tokens': 40, 'completion_tokens': 64, 'total_tokens': 104,
             'completion_tokens_details': {'reasoning_tokens': 61}}

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        context = json.loads(payload['messages'][1]['content'])
        fixed = answer(context['参考原文'][0])
        if len(requests) == 1:
            content = json.dumps(fixed, ensure_ascii=False) if partial == 'valid_json' else '{"status":' if partial == 'partial' else partial
            source_content.append(content)
            return length_response(content, usage if reported_usage else None)
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': response(fixed)['message']}],
            'usage': {'total_tokens': 30}})

    mock_transport(monkeypatch, handler)
    budget = BudgetedChat(profile(), max_calls=2)
    recorder = Recorder(tmp_path / 'events.jsonl', 'length', 'repair')
    result = await run_initial(budget, recorder)
    assert len(requests) == 2 and result['repairs'] == 1 and not result['errors']
    assert result['generation_error'] is None
    assert requests[0]['thinking'] == {'type': 'enabled'}
    assert requests[1]['thinking'] == {'type': 'disabled'} and 'reasoning_effort' not in requests[1]
    assert all('tools' not in request for request in requests)
    repaired_messages = requests[1]['messages']
    if source_content[0]:
        assert repaired_messages[-2] == dict(role='assistant', content=source_content[0])
    else:
        assert not any(m['role'] == 'assistant' for m in repaired_messages)
    feedback = json.loads(repaired_messages[-1]['content'])
    assert feedback['instruction'] == '上一响应因长度上限截断，请返回完整 JSON。'
    assert feedback['errors'][0]['code'] == 'MODEL_INCOMPLETE_RESPONSE'
    validations = [e for e in recorder.events if e['kind'] == 'validation']
    assert len(validations) == 2 and validations[0]['answer'] is None
    assert validations[0]['errors'] == ['MODEL_INCOMPLETE_RESPONSE']
    assert validations[0]['rawResponse'] == dict(role='assistant', content=source_content[0])
    if partial == 'valid_json':
        assert validations[0]['candidate'] is not None  # parseability never overrides finish_reason=length
    failed = [e for e in result['trace'] if e['step'] == 'model' and e['status'] == 'error']
    assert len(failed) == 1 and failed[0]['code'] == 'MODEL_INCOMPLETE_RESPONSE'
    assert failed[0]['usage'] == (usage if reported_usage else None)
    for field, source in [('promptTokens', 'prompt_tokens'), ('completionTokens', 'completion_tokens'), ('totalTokens', 'total_tokens')]:
        assert failed[0][field] == (usage[source] if reported_usage else None)
    assert budget.calls == 2 and budget.reported == (134 if reported_usage else 30)
    assert budget.unknown == (0 if reported_usage else 1)
    logged = [e for e in recorder.events if e['kind'] == 'model_request']
    assert [e['stage'] for e in logged] == ['generate', 'repair']
    assert [e['callId'] for e in logged] == [1, 2]
    assert 'PRIVATE_INCOMPLETE_REASONING' not in json.dumps(requests[1])
    assert 'PRIVATE_INCOMPLETE_REASONING' not in json.dumps(result)
    assert 'PRIVATE_INCOMPLETE_REASONING' not in (tmp_path / 'events.jsonl').read_text()


@pytest.mark.parametrize('first_failure', ['length', 'schema'])
async def test_repair_length_is_final_failure_without_another_request(monkeypatch, tmp_path, first_failure):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1 and first_failure == 'schema':
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': response({'bad': True})['message']}],
                'usage': {'total_tokens': 10}})
        return length_response('{"status":', {'total_tokens': 20})

    mock_transport(monkeypatch, handler)
    budget = BudgetedChat(profile(), max_calls=5)
    recorder = Recorder(tmp_path / 'events.jsonl', 'twice', first_failure)
    result = await run_initial(budget, recorder)
    assert len(requests) == budget.calls == 2 and result['repairs'] == 1
    assert result['errors'] == ['MODEL_INCOMPLETE_RESPONSE']
    assert result['generation_error'] == 'MODEL_INCOMPLETE_RESPONSE'
    validations = [e for e in recorder.events if e['kind'] == 'validation']
    assert len(validations) == 2 and validations[-1]['answer'] is None
    assert validations[-1]['rawResponse'] == dict(role='assistant', content='{"status":')
    failed = [e for e in result['trace'] if e['step'] == 'model' and e['status'] == 'error']
    assert len(failed) == (2 if first_failure == 'length' else 1)
    assert failed[-1]['phase'] == 'repair' and failed[-1]['totalTokens'] == 20


async def test_router_length_propagates_without_generation_repair(monkeypatch, tmp_path):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return length_response(None, {'total_tokens': 20})

    mock_transport(monkeypatch, handler)
    budget = BudgetedChat(profile(), max_calls=3)
    recorder = Recorder(tmp_path / 'events.jsonl', 'route', 'length')
    graph = build_graph(InMemorySaver(), RecordedChat(budget, recorder), observe=recorder)
    with pytest.raises(llm.ModelError, match='MODEL_INCOMPLETE_RESPONSE'):
        await graph.ainvoke(initial_state(session(), '我现在应该怎么考虑？', 'followup'),
                           {'configurable': {'thread_id': 'route'}})
    assert len(requests) == budget.calls == 1 and requests[0]['tools']
    assert [e['stage'] for e in recorder.events if e['kind'] == 'model_request'] == ['route']
    assert not any(e['kind'] == 'validation' for e in recorder.events)


@pytest.mark.parametrize('error', ['MODEL_TIMEOUT_OR_NETWORK', 'MODEL_HTTP_429'])
async def test_other_generation_errors_are_not_retried(error):
    calls = 0

    async def chat(messages, tools=None):
        nonlocal calls
        calls += 1
        raise llm.ModelError(error)

    saved = session()
    with pytest.raises(llm.ModelError, match=error):
        await build_graph(InMemorySaver(), chat).ainvoke(initial_state(saved, saved['question'], 'initial'),
            {'configurable': {'thread_id': 'unrelated-error'}})
    assert calls == 1


async def test_existing_budget_blocks_repair_before_second_transport(monkeypatch, tmp_path):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return length_response('', {'total_tokens': 64})

    mock_transport(monkeypatch, handler)
    budget = BudgetedChat(profile(), max_calls=1)
    recorder = Recorder(tmp_path / 'events.jsonl', 'budget', 'length')
    with pytest.raises(llm.ModelError, match='EVALUATION_BUDGET_EXHAUSTED'):
        await run_initial(budget, recorder)
    assert len(requests) == budget.calls == 1 and budget.reported == 64
    results = [e for e in recorder.events if e['kind'] == 'model_result']
    assert [e['status'] for e in results] == ['error', 'budget_stopped']
    assert results[-1]['budgetCounted'] is False and results[-1]['stage'] == 'repair'
