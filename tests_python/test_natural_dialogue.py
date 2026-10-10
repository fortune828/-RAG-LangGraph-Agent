"""Natural prose contracts, not a mechanical score for conversational quality."""
import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from backend.graph import build_graph, classify, history, initial_state
from tests_python.test_migration import answer, response, session


@pytest.mark.parametrize('question', [
    '要不要再投资时间完善项目？',
    '这个项目值得继续投资精力吗？',
    '我最近在读《易经》，想借这卦聊换工作。',
])
def test_life_context_is_not_risk_or_source_merely_for_a_keyword(question):
    assert classify(question, 'initial') == 'explanation_followup'


@pytest.mark.parametrize('question,expected', [
    ('这个项目值得投资时间吗？另外股票可以加杠杆买吗？', 'out_of_scope_or_risk'),
    ('我能根据这个卦停药吗？', 'out_of_scope_or_risk'),
    ('我不想活了，手边有药。', 'out_of_scope_or_risk'),
    ('朱熹是不是说过，大意是困境时不要急着行动？', 'source_request'),
    ('请给出第三、第四爻原文', 'source_request'),
    ('王弼对本卦第二爻如何解释？', 'source_request'),
])
def test_real_risks_and_explicit_document_requests_remain_distinct(question, expected):
    assert classify(question, 'followup') == expected


def test_history_preserves_legacy_uncertainty_and_tail_in_assistant_role():
    s = session()
    s['turns'] = [dict(status='completed', question='纠正一下，我只有一个月积蓄，并没有录用通知。',
        answer=dict(summary='如果有正式录用条件，可以比较。',
                    paragraphs=[dict(kind='reflection', text='此前讨论' * 2000 + '尾部条件：录用仍未确认。')],
                    limitations=['助手无法确认用户是否已有录用通知。']))]
    visible = history(s)
    assert visible[0]['user'] == s['turns'][0]['question']
    assert '如果有正式录用条件' in visible[0]['assistant']
    assert '录用仍未确认' in visible[0]['assistant']
    assert '助手无法确认用户是否已有录用通知' in visible[0]['assistant']
    assert '中间内容省略' in visible[0]['assistant']
    assert '如果有正式录用条件' not in visible[0]['user']


async def test_three_turns_preserve_corrections_body_uncertainty_and_cast():
    s = session()
    original_cast = s['cast'].copy()
    s['question'] = '我要不要换工作？暂时还没有拿到录用通知。'
    questions = [s['question'], '纠正一下，我只有一个月积蓄，不是三个月。', '那在正式录用之前，我这一周可以先做什么？']
    generated = []

    async def model(messages, tools=None):
        if tools:
            return response({'ready': True})
        context = json.loads(messages[1]['content'])
        assert context['核心问题'] == s['question']
        expected_history = list(zip(questions[:len(generated)], generated))
        for seen, (question, prior_answer) in zip(context['近期对话'], expected_history):
            assert seen['user'] == question
            assert prior_answer['paragraphs'][0]['text'] in seen['assistant']
            assert '录用尚未确认' in seen['assistant']
        if len(generated) == 2:
            assert '只有一个月积蓄，不是三个月' in context['近期对话'][1]['user']
            assert '正式录用之前' in context['当前问题']
        value = answer(context['参考原文'][0])
        value['paragraphs'][0]['text'] += '录用尚未确认时，不妨先梳理现有机会。'
        value['limitations'] = ['录用尚未确认']
        generated.append(value)
        return response(value)

    graph = build_graph(InMemorySaver(), model)
    for i, question in enumerate(questions):
        result = await graph.ainvoke(initial_state(s, question, 'initial' if i == 0 else 'followup'),
            {'configurable': {'thread_id': f'natural-continuation-{i}'}})
        assert not result['errors'] and result['repairs'] == 0
        assert result['session']['cast'] == original_cast
        assert 'summary' not in result['answer']
        assert all('kind' not in p for p in result['answer']['paragraphs'])
        s['turns'].append(dict(status='completed', question=question, answer=result['answer'], evidence=result['evidence']))
    assert len(generated) == 3
    # These mocks establish preservation and boundaries; naturalness still needs
    # independent reading of actual model answers.


@pytest.mark.parametrize('question', [
    '我要不要再投资时间完善项目？',
    '我最近在读《易经》，想借这卦聊换工作。',
])
async def test_life_context_receives_cast_without_irrelevant_source_or_risk_notes(question):
    async def model(messages, tools=None):
        assert tools is None
        context = json.loads(messages[1]['content'])
        assert context['卦象'] and context['参考原文']
        assert not any('书名尚未确认' in note or '未收录' in note for note in context['资料说明'])
        assert '回答要求' not in context
        return response(answer(context['参考原文'][0]))

    result = await build_graph(InMemorySaver(), model).ainvoke(
        initial_state(session(), question, 'initial'),
        {'configurable': {'thread_id': question}})
    assert result['intent'] == 'explanation_followup' and not result['errors']
    assert result['attempts'] == result['repairs'] == 0


def test_schema_does_not_force_old_order_or_six_paragraph_ceiling():
    from backend.knowledge import retrieve_exact
    from backend.validation import validate_answer

    evidence = retrieve_exact(dict(hexagramId=1, sections=['xiang']))['evidence'][0]
    value = answer(evidence)
    # A discussion may start with the person's situation; only its cited claim
    # is anchored to the classic, regardless of paragraph position or length.
    value['paragraphs'].insert(0, dict(text='先来看你说的现实条件。' + '需要权衡各自代价。' * 250, citations=[]))
    value['paragraphs'] += [dict(text=f'第{i}个问题有不同的前提。', citations=[]) for i in range(5)]
    normalized, errors = validate_answer(value, [evidence])
    assert normalized and not errors
    assert len(normalized['paragraphs']) == 7
    assert len(normalized['paragraphs'][0]['text']) > 2000
    assert normalized['paragraphs'][0]['citations'] == []
    # This is only a permissive schema regression, never a quality reward for
    # longer answers or more paragraphs.


def test_long_assistant_replies_cannot_evict_recent_user_corrections():
    s = session()
    s['turns'] = [dict(status='completed', question=f'第{i}次更正：储蓄只有一个月。',
        answer=dict(paragraphs=[dict(text='开头条件。' + '分析' * 3000 + '尾部：录用未确认。', citations=[])],
                    limitations=['录用未确认。'])) for i in range(6)]
    selected = history(s)
    assert [item['user'] for item in selected] == [turn['question'] for turn in s['turns']]
    assert len(json.dumps(selected, ensure_ascii=False, separators=(',', ':'))) <= 16000
    assert all('开头条件' in item['assistant'] and '录用未确认' in item['assistant'] for item in selected)
