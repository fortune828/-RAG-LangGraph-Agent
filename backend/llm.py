"""Small injectable model adapter; graph tests use a fake, not a paid API."""
import os

import httpx

from .config import MODEL, MAX_COMPLETION_TOKENS


class ModelError(Exception):
    def __init__(self, code, metadata=None):
        super().__init__(code)
        self.metadata = metadata or {}


def request_payload(messages, tools=None):
    """Public request options, without transport headers or credentials."""
    payload = dict(model=MODEL, messages=messages, max_tokens=MAX_COMPLETION_TOKENS,
                   thinking={'type': 'enabled'}, reasoning_effort='high')
    payload.update(dict(tools=tools) if tools else dict(response_format={'type': 'json_object'}))
    return payload


async def deepseek_chat(messages, tools=None):
    key = os.getenv('DEEPSEEK_API_KEY')
    if not key:
        raise ModelError('API_KEY_MISSING')
    payload = request_payload(messages, tools)
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post('https://api.deepseek.com/chat/completions', json=payload,
                                         headers={'Authorization': f'Bearer {key}'})
    except httpx.RequestError:
        raise ModelError('MODEL_TIMEOUT_OR_NETWORK') from None
    try:
        data = response.json()
    except ValueError:
        data = {}
    metadata = {k: data.get(k) for k in ('usage', 'model', 'id', 'system_fingerprint')} if isinstance(data, dict) else {}
    metadata['httpStatus'] = response.status_code
    if response.status_code != 200:
        raise ModelError(f'MODEL_HTTP_{response.status_code}', metadata)
    try:
        choice = data['choices'][0]
        metadata.update(message=choice.get('message'), finishReason=choice.get('finish_reason'))
        if choice.get('finish_reason') == 'length':
            raise ModelError('MODEL_INCOMPLETE_RESPONSE', metadata)
        return dict(metadata, message=choice['message'], model=data.get('model', MODEL))
    except (ValueError, KeyError, IndexError, TypeError):
        raise ModelError('MODEL_INVALID_RESPONSE', metadata) from None
