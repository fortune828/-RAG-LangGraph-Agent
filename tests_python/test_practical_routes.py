"""No classical evidence or tool loop is needed for risk/new-reading guidance."""
import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from backend.graph import build_graph, initial_state
from tests_python.test_migration import response, session


@pytest.mark.parametrize('question', [
    '医生给我开了药，我能根据这个卦停药吗？',
    '我不想活了，手边有药。',
    '这个卦能保证借钱加杠杆买股票赚钱吗？',
])
async def test_practical_routes_do_not_receive_classical_evidence(question):
    calls = 0

    async def model(messages, tools=None):
        nonlocal calls
        calls += 1
        assert tools is None
        context = json.loads(messages[1]['content'])
        assert '参考原文' not in context and '卦象' not in context
        from backend.prompts import RISK_SYSTEM
        assert messages[0]['content'] == RISK_SYSTEM
        assert all('user' in turn and 'assistant' in turn for turn in context['近期对话'])
        assert context['当前问题'] == question
        assert context['近期对话'][0]['assistant'].endswith('旧经文解释')
        return response(dict(status='answered',
                             paragraphs=[dict(text='此处是无需经典解释的现实支持。', citations=[])], limitations=[]))

    s = session()
    s['turns'] = [dict(status='completed', question='之前的问题', evidence=[],
                      answer=dict(summary='旧解卦', paragraphs=[dict(text='旧经文解释', citations=[])]))]
    result = await build_graph(InMemorySaver(), model).ainvoke(
        initial_state(s, question, 'followup'), {'configurable': {'thread_id': question}})
    assert calls == 1 and not result['errors']
    assert not result['evidence'] and result['session']['cast'] == s['cast']


async def test_new_reading_request_returns_operation_guidance_without_model():
    async def model(*args, **kwargs):
        pytest.fail('A new reading requires explicit user action, no model call')

    s = session()
    result = await build_graph(InMemorySaver(), model).ainvoke(
        initial_state(s, '这是另一件事，请重新起卦。', 'followup'),
        {'configurable': {'thread_id': 'new-reading'}})
    assert result['answer']['status'] == 'answered' and not result['errors']
    assert result['session']['cast'] == s['cast']
    assert not result['evidence'] and result['attempts'] == 0
    assert all('kind' not in p and not p['citations'] for p in result['answer']['paragraphs'])
