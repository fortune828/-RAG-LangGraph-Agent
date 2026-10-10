import json
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError

import httpx
import pytest

from backend import llm
from backend.evaluate import BudgetedChat
from evaluation.paired import RecordedChat, Recorder


def mock_transport(monkeypatch, handler):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'CONFIG_TEST_ONLY')
    client = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, 'AsyncClient', lambda **kwargs: client(
        transport=httpx.MockTransport(handler), **kwargs))


def test_configuration_is_immutable_and_does_not_change_default_requests():
    baseline = llm.request_payload([])
    config = llm.ChatConfig(model='experimental-pro', thinking='disabled', max_tokens=512)
    with pytest.raises(FrozenInstanceError):
        config.max_tokens = 8192
    payload = llm.request_payload([], config=config)
    assert payload['model'] == 'experimental-pro' and payload['max_tokens'] == 512
    assert payload['thinking'] == {'type': 'disabled'} and 'reasoning_effort' not in payload
    assert llm.request_payload([]) == baseline
    for effort in ('low', 'high', 'max'):
        assert llm.request_payload([], config=llm.ChatConfig(thinking='enabled', reasoning_effort=effort))['reasoning_effort'] == effort


async def test_recorded_options_match_transport_for_each_configuration(monkeypatch, tmp_path):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'role': 'assistant', 'content': '{}'}}], 'usage': {'total_tokens': 12}})

    mock_transport(monkeypatch, handler)
    recorder = Recorder(tmp_path / 'events.jsonl', 'isolation', 'experiment')
    configs = [llm.ChatConfig(model='experimental-pro', thinking='disabled', max_tokens=512),
               llm.ChatConfig(model='experimental-flash', thinking='enabled', reasoning_effort='low', max_tokens=2048)]
    for index, config in enumerate(configs):
        chat = llm.configured_chat(config)
        budget = BudgetedChat(chat, max_calls=1, max_tokens=100000)
        tools = [{'type': 'function'}] if index else None
        result = await RecordedChat(budget, recorder)([{'role': 'user', 'content': 'synthetic'}], tools=tools)
        assert result['model'] == config.model
    logged = [event['payload'] for event in recorder.events if event['kind'] == 'model_request']
    assert logged == requests
    assert requests[0]['response_format'] == {'type': 'json_object'}
    assert 'tools' not in requests[0] and 'reasoning_effort' not in requests[0]
    assert requests[1]['tools'] and 'response_format' not in requests[1]
    assert 'CONFIG_TEST_ONLY' not in (tmp_path / 'events.jsonl').read_text()


async def test_budget_uses_configured_completion_limit_before_transport(monkeypatch):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'role': 'assistant', 'content': '{}'}}]})

    mock_transport(monkeypatch, handler)
    messages = [{'role': 'user', 'content': 'synthetic'}]
    small = BudgetedChat(llm.configured_chat(llm.ChatConfig(max_tokens=256)), max_tokens=2000)
    large = BudgetedChat(llm.configured_chat(llm.ChatConfig(max_tokens=8192)), max_tokens=2000)
    reserved = small.reservation(messages)
    await small(messages)
    assert small.calls == 1 and small.unknown == 1 and small.reported == 0
    assert small.accounted == reserved
    with pytest.raises(llm.ModelError, match='EVALUATION_BUDGET_EXHAUSTED'):
        await large(messages)
    assert large.calls == 0 and len(requests) == 1


async def test_configured_length_failure_retains_usage_and_visible_response(monkeypatch, tmp_path):
    def handler(request):
        assert json.loads(request.content)['max_tokens'] == 64
        return httpx.Response(200, json={'model': 'returned-version',
            'usage': {'prompt_tokens': 20, 'completion_tokens': 64, 'total_tokens': 84},
            'choices': [{'finish_reason': 'length', 'message': {'role': 'assistant',
                'content': '{"partial":', 'reasoning_content': 'DO_NOT_EXPORT'}}]})

    mock_transport(monkeypatch, handler)
    budget = BudgetedChat(llm.configured_chat(llm.ChatConfig(max_tokens=64)), max_tokens=10000)
    recorder = Recorder(tmp_path / 'events.jsonl', 'length', 'experiment')
    with pytest.raises(llm.ModelError, match='MODEL_INCOMPLETE_RESPONSE'):
        await RecordedChat(budget, recorder)([{'role': 'user', 'content': 'synthetic'}])
    assert budget.reported == budget.accounted == 84 and budget.unknown == 0
    response = recorder.events[-1]['response']
    assert response['usage']['completion_tokens'] == 64 and response['model'] == 'returned-version'
    assert response['message']['content'] == '{"partial":'
    assert 'DO_NOT_EXPORT' not in (tmp_path / 'events.jsonl').read_text()


def config_process(options):
    # dotenv is disabled in this subprocess so local credentials/settings cannot
    # affect the checked defaults or leak into a failure message.
    env = {key: value for key, value in os.environ.items() if not key.startswith('DEEPSEEK_')}
    env.update(PYTHON_DOTENV_DISABLED='1', **options)
    return subprocess.run([sys.executable, '-c',
        'import json; from backend.llm import request_payload; print(json.dumps(request_payload([])))'],
        capture_output=True, text=True, env=env)


def test_default_and_environment_options_are_reflected_in_requests():
    process = config_process({})
    assert process.returncode == 0
    payload = json.loads(process.stdout)
    assert payload['model'] == 'deepseek-flash'
    assert payload['thinking'] == {'type': 'enabled'} and payload['max_tokens'] == 8192
    assert payload['reasoning_effort'] == 'low'
    process = config_process(dict(DEEPSEEK_MODEL='configured-model', DEEPSEEK_THINKING='disabled',
        DEEPSEEK_REASONING_EFFORT='low', DEEPSEEK_MAX_TOKENS='4096'))
    assert process.returncode == 0
    payload = json.loads(process.stdout)
    assert payload['model'] == 'configured-model' and payload['thinking'] == {'type': 'disabled'}
    assert payload['max_tokens'] == 4096 and 'reasoning_effort' not in payload


@pytest.mark.parametrize('name,value', [
    ('DEEPSEEK_THINKING', 'sometimes'),
    ('DEEPSEEK_REASONING_EFFORT', 'medium'),
    ('DEEPSEEK_MAX_TOKENS', '0'),
    ('DEEPSEEK_MAX_TOKENS', '-1'),
    ('DEEPSEEK_MAX_TOKENS', '1.5'),
])
def test_invalid_environment_configuration_fails_before_requests(name, value):
    process = config_process({name: value})
    assert process.returncode != 0 and 'ValueError' in process.stderr
    assert process.stdout == ''
