"""Small injectable model adapter; graph tests use a fake, not a paid API."""
import os

import httpx

from .config import MODEL, MAX_COMPLETION_TOKENS


class ModelError(Exception):
    pass


async def deepseek_chat(messages, tools=None):
    key = os.getenv('DEEPSEEK_API_KEY')
    if not key:
        raise ModelError('API_KEY_MISSING')
    payload = dict(model=MODEL, messages=messages, max_tokens=MAX_COMPLETION_TOKENS,
                   thinking={'type': 'enabled'}, reasoning_effort='high')
    payload.update(dict(tools=tools) if tools else dict(response_format={'type': 'json_object'}))
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post('https://api.deepseek.com/chat/completions', json=payload,
                                         headers={'Authorization': f'Bearer {key}'})
    except httpx.RequestError:
        raise ModelError('MODEL_TIMEOUT_OR_NETWORK') from None
    if response.status_code != 200:
        raise ModelError(f'MODEL_HTTP_{response.status_code}')
    try:
        data = response.json()
        choice = data['choices'][0]
        if choice.get('finish_reason') == 'length':
            raise ModelError('MODEL_INCOMPLETE_RESPONSE')
        return dict(message=choice['message'], usage=data.get('usage', {}), model=data.get('model', MODEL))
    except (ValueError, KeyError, IndexError, TypeError):
        raise ModelError('MODEL_INVALID_RESPONSE') from None
