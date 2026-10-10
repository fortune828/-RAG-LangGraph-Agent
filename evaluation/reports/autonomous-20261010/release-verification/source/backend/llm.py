"""Small injectable model adapter; graph tests use a fake, not a paid API."""
import os
from dataclasses import dataclass
from functools import partial
from typing import Literal

import httpx

from .config import MODEL, MAX_COMPLETION_TOKENS, THINKING, REASONING_EFFORT


class ModelError(Exception):
    def __init__(self, code, metadata=None):
        super().__init__(code)
        self.metadata = metadata or {}


@dataclass(frozen=True)
class ChatConfig:
    """Request-local options; experiments cannot mutate production defaults."""
    model: str = MODEL
    thinking: Literal['enabled', 'disabled'] = THINKING
    reasoning_effort: Literal['low', 'high', 'max'] = REASONING_EFFORT
    max_tokens: int = MAX_COMPLETION_TOKENS

    def __post_init__(self):
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError('model must be a nonempty identifier')
        if self.thinking not in ('enabled', 'disabled'):
            raise ValueError('thinking must be enabled or disabled')
        if self.reasoning_effort not in ('low', 'high', 'max'):
            raise ValueError('reasoning_effort must be low, high or max')
        if not isinstance(self.max_tokens, int) or isinstance(self.max_tokens, bool) or self.max_tokens < 1:
            raise ValueError('max_tokens must be a positive integer')


DEFAULT_CHAT_CONFIG = ChatConfig()


def request_payload(messages, tools=None, *, config=None):
    """Public request options, without transport headers or credentials."""
    config = config or DEFAULT_CHAT_CONFIG
    payload = dict(model=config.model, messages=messages, max_tokens=config.max_tokens,
                   thinking={'type': config.thinking})
    if config.thinking == 'enabled':
        payload['reasoning_effort'] = config.reasoning_effort
    payload.update(dict(tools=tools) if tools else dict(response_format={'type': 'json_object'}))
    return payload


def configured_chat(config):
    """Inject one configuration, exposing the same payload used by transport."""
    chat = partial(deepseek_chat, config=config)
    chat.config = config
    chat.request_payload = partial(request_payload, config=config)
    return chat


async def deepseek_chat(messages, tools=None, *, config=None):
    key = os.getenv('DEEPSEEK_API_KEY')
    if not key:
        raise ModelError('API_KEY_MISSING')
    payload = request_payload(messages, tools, config=config)
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
        return dict(metadata, message=choice['message'], model=data.get('model', payload['model']))
    except (ValueError, KeyError, IndexError, TypeError):
        raise ModelError('MODEL_INVALID_RESPONSE', metadata) from None
