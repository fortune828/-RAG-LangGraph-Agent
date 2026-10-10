import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from backend.graph import build_graph, initial_state, history, dumps
from tests_python.test_migration import session, response, generic_chat, tool_call


async def test_explicit_new_topic_stops_without_model_or_tool():
    async def model(*args, **kwargs):
        pytest.fail('Explicit topic change needs only a fixed handoff')
    s = session()
    result = await build_graph(InMemorySaver(), model).ainvoke(
        initial_state(s, '先不谈工作了。我改问和朋友合租的事，该怎样商量家务分工？', 'followup'),
        {'configurable': {'thread_id': 'explicit-topic'}})
    assert result['answer']['status'] == 'answered'
    assert '新建' in result['answer']['paragraphs'][0]['text']
    assert not result['evidence'] and result['attempts'] == 0
    assert result['session']['cast'] == s['cast']


async def test_router_new_topic_handoff_ignores_tool_request_and_preserves_cast():
    calls = 0
    async def model(messages, tools=None):
        nonlocal calls
        calls += 1
        assert tools
        assert json.loads(messages[1]['content'])['question'] == s['question']
        return response({'newTopic': True}, calls=[tool_call()])
    s = session()
    result = await build_graph(InMemorySaver(), model).ainvoke(
        initial_state(s, '我想了解孩子转学的事。', 'followup'),
        {'configurable': {'thread_id': 'semantic-topic'}})
    assert calls == 1 and result['attempts'] == 0
    assert result['session']['cast'] == s['cast']
    assert result['trace'][-1]['code'] == 'NEW_CORE_QUESTION'


@pytest.mark.parametrize('question', ['那另一个工作机会呢？', '纠正一下，我只有一个月积蓄。', '请解释本卦的含义。'])
async def test_same_question_can_continue(question):
    result = await build_graph(InMemorySaver(), generic_chat).ainvoke(
        initial_state(session(), question, 'followup'),
        {'configurable': {'thread_id': question}})
    assert not result['errors']
    assert not any(e['step'] == 'handoff' for e in result['trace'])


def test_history_bounds_actual_dialog_and_retains_latest_large_answer():
    s = session()
    s['turns'] = [dict(status='completed', question=f'第{i}次更新',
        answer=dict(summary='此前结论', paragraphs=[dict(text='很长的旧回答' * 3000)])) for i in range(9)]
    h = history(s)
    assert h and h[-1]['user'] == '第8次更新'
    assert len(dumps(h)) <= 16000
    assert all(set(t) == {'user', 'assistant'} for t in h)
    fresh = session()
    assert history(fresh) == []


async def test_risk_on_new_topic_is_not_delayed_by_new_chat_handoff():
    async def model(messages, tools=None):
        assert tools is None
        return response(dict(status='answered',
            paragraphs=[dict(text='请远离药物并联系身边可信的人或当地急救。', citations=[])], limitations=[]))
    result = await build_graph(InMemorySaver(), model).ainvoke(
        initial_state(session(), '先不谈工作了，我现在不想活了，手边有药。', 'followup'),
        {'configurable': {'thread_id': 'risk-before-handoff'}})
    assert result['intent'] == 'out_of_scope_or_risk'
    assert not any(t['step'] == 'handoff' for t in result['trace'])


async def test_risk_repair_does_not_request_traditional_explanation():
    calls = 0
    async def model(messages, tools=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            return response(dict(status='answered',
                paragraphs=[dict(kind='insufficient', text='没有原文。', citations=[])], limitations=[]))
        instruction = json.loads(messages[-1]['content'])['instruction']
        assert '本轮仅提供现实安全支持' in instruction
        assert '根据已有原文补充' not in instruction
        return response(dict(status='answered',
            paragraphs=[dict(text='不要依据卦象停药，请联系开药医生或药师。', citations=[])], limitations=[]))
    result = await build_graph(InMemorySaver(), model).ainvoke(
        initial_state(session(), '可以根据这个卦停药吗？', 'followup'),
        {'configurable': {'thread_id': 'risk-repair'}})
    assert calls == 2 and result['repairs'] == 1 and not result['errors']
