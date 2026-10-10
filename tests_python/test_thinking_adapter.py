import httpx

from backend import llm


async def test_thinking_adapter_preserves_tool_reasoning_and_json_contract(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')
    requests = []
    import json
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body['thinking'] == {'type': 'enabled'}
        assert body['max_tokens'] == 8192 and body['reasoning_effort'] == 'high'
        assert 'temperature' not in body and 'tool_choice' not in body
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'role': 'assistant', 'content': '{"ready":true}', 'reasoning_content': 'TEST_PROTOCOL_MARKER'}}],
            'usage': {'total_tokens': 20}})
    client = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, 'AsyncClient', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))
    chat = llm.configured_chat(llm.ChatConfig(thinking='enabled', reasoning_effort='high', max_tokens=8192))
    messages = [{'role': 'user', 'content': 'test'}]
    result = await chat(messages, tools=[{'type': 'function'}])
    messages = messages + [result['message'], {'role': 'user', 'content': 'continue'}]
    await chat(messages, tools=[{'type': 'function'}])
    assert requests[1]['messages'][1]['reasoning_content'] == 'TEST_PROTOCOL_MARKER'
    await chat([{'role': 'user', 'content': 'JSON'}])
    assert requests[2]['response_format'] == {'type': 'json_object'}
