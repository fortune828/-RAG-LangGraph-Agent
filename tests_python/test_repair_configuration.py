import json

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from backend import llm
from backend.evaluate import BudgetedChat
from backend.graph import build_graph, initial_state
from evaluation.paired import RecordedChat, Recorder
from tests_python.test_migration import answer, session
from tests_python.test_model_configuration import mock_transport


@pytest.mark.parametrize('production_default', [True, False])
async def test_repair_configuration_is_explicit_and_uses_original_single_repair(monkeypatch, tmp_path, production_default):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        context = json.loads(body['messages'][1]['content'])
        content = {'bad': True} if len(requests) == 1 else answer(context['参考原文'][0])
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'role': 'assistant', 'content': json.dumps(content, ensure_ascii=False)}}],
            'usage': {'total_tokens': 20}})

    mock_transport(monkeypatch, handler)
    base = llm.deepseek_chat if production_default else llm.configured_chat(
        llm.ChatConfig(thinking='enabled', reasoning_effort='high', max_tokens=8192))
    budget = BudgetedChat(base, max_calls=2)
    recorder = Recorder(tmp_path / 'events.jsonl', 'repair', 'production' if production_default else 'fixed')
    result = await build_graph(InMemorySaver(), RecordedChat(budget, recorder)).ainvoke(
        initial_state(session(), '面对新的工作机会，我该如何权衡？', 'initial'),
        {'configurable': {'thread_id': 'repair'}})
    assert not result['errors'] and result['repairs'] == 1 and len(requests) == 2
    assert requests[0]['thinking'] == {'type': 'enabled'}
    assert requests[0]['reasoning_effort'] == ('low' if production_default else 'high')
    assert requests[1]['thinking'] == {'type': 'disabled' if production_default else 'enabled'}
    assert ('reasoning_effort' in requests[1]) is not production_default
    assert requests[0]['model'] == requests[1]['model']
    assert requests[0]['max_tokens'] == requests[1]['max_tokens'] == 8192
    assert requests[1]['messages'][-2]['role'] == 'assistant'
    assert json.loads(requests[1]['messages'][-2]['content']) == {'bad': True}
    feedback = json.loads(requests[1]['messages'][-1]['content'])['errors']
    assert feedback and all('code' in issue and 'path' in issue for issue in feedback)
    assert any(issue['path'] == ['paragraphs'] for issue in feedback)
    logged = [e for e in recorder.events if e['kind'] == 'model_request']
    assert [e['callId'] for e in logged] == [1, 2]
    assert [e['stage'] for e in logged] == ['generate', 'repair']
    assert [e['payload'] for e in logged] == requests
    assert budget.calls == 2 and budget.reported == 40


@pytest.mark.parametrize('limit', ['calls', 'tokens'])
async def test_nested_repair_cannot_escape_global_budget(monkeypatch, tmp_path, limit):
    dispatched = []

    def handler(request):
        dispatched.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'role': 'assistant', 'content': '{}'}}]})

    mock_transport(monkeypatch, handler)
    base = llm.configured_chat(llm.ChatConfig(thinking='enabled', max_tokens=64),
        repair_config=llm.ChatConfig(thinking='disabled', max_tokens=64))
    shared = BudgetedChat(max_calls=1 if limit == 'calls' else 10, max_tokens=1500 if limit == 'tokens' else 100000)
    budget = BudgetedChat(base, max_calls=10, max_tokens=100000, parent=shared)
    recorder = Recorder(tmp_path / 'events.jsonl', 'budget', limit)
    chat = RecordedChat(budget, recorder)
    await chat([{'role': 'user', 'content': 'synthetic'}])
    repair = chat.repair_chat
    assert repair.budget.parent is budget and budget.parent is shared
    with pytest.raises(llm.ModelError, match='EVALUATION_BUDGET_EXHAUSTED'):
        await repair([{'role': 'user', 'content': 'synthetic'}])
    assert len(dispatched) == budget.calls == shared.calls == 1
    assert repair.budget.calls == 0
    assert budget.unknown == shared.unknown == 1 and budget.accounted == shared.accounted > 0
    assert recorder.events[-1]['status'] == 'budget_stopped'
    assert recorder.events[-1]['budgetCounted'] is False


async def test_repair_trace_ids_and_unknown_usage_propagate_to_every_ancestor(monkeypatch, tmp_path):
    dispatched = []

    def handler(request):
        dispatched.append(json.loads(request.content))
        # Missing usage remains unknown; only the subsequent requests report it.
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'role': 'assistant', 'content': '{}'}}],
            **({'usage': {'total_tokens': 7}} if len(dispatched) > 1 else {})})

    mock_transport(monkeypatch, handler)
    base = llm.configured_chat(llm.ChatConfig(thinking='enabled', max_tokens=64),
        repair_config=llm.ChatConfig(thinking='disabled', max_tokens=64))
    shared = BudgetedChat(max_calls=3)
    budget = BudgetedChat(base, max_calls=3, parent=shared)
    recorder = Recorder(tmp_path / 'events.jsonl', 'usage', 'stages')
    chat = RecordedChat(budget, recorder)
    await chat([{'role': 'user', 'content': 'synthetic'}])
    unknown_reservation = shared.accounted
    repair = chat.repair_chat
    await repair([{'role': 'user', 'content': 'synthetic'}, {'role': 'assistant', 'content': '{}'},
                  {'role': 'user', 'content': 'repair'}])
    await chat([{'role': 'user', 'content': 'synthetic'}])
    requests = [e for e in recorder.events if e['kind'] == 'model_request']
    results = [e for e in recorder.events if e['kind'] == 'model_result']
    assert [e['callId'] for e in requests] == [e['callId'] for e in results] == [1, 2, 3]
    assert [e['payload'] for e in requests] == dispatched
    assert budget.calls == shared.calls == 3 and repair.budget.calls == 1
    assert budget.unknown == shared.unknown == 1
    assert budget.reported == shared.reported == 14 and repair.budget.reported == 7
    assert budget.accounted == shared.accounted == unknown_reservation + 14
    assert results[0]['response']['usage'] is None
