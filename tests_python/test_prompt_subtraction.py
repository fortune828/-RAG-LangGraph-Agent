"""Prompt delivery and protocol regressions, not a naturalness score."""
import json
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from backend import prompts
from backend.graph import build_graph, initial_state
from backend.knowledge import TOOLS
from tests_python.test_migration import answer, response, session


@pytest.mark.parametrize('question,mode,risk', [
    ('我今天中午吃什么？', 'initial', False),
    ('那具体选什么？', 'followup', False),
    ('医生给我开了药，能根据卦停药吗？', 'followup', True),
])
async def test_delivered_prompts_keep_current_protocol_and_facts(question, mode, risk):
    seen = []
    async def chat(messages, tools=None):
        seen.append((messages, tools))
        if tools:
            return response({'ready': True})
        context = json.loads(messages[1]['content'])
        assert '回答要求' not in context
        assert context['当前问题'] == question
        assert messages[0]['content'] == (prompts.RISK_SYSTEM if risk else prompts.SYSTEM)
        if risk:
            assert '卦象' not in context and '参考原文' not in context
            return response(dict(status='answered', paragraphs=[dict(text='请与开药医生确认用药安排。', citations=[])]))
        assert context['卦象'] and context['参考原文']
        return response(answer(context['参考原文'][0]))
    result = await build_graph(InMemorySaver(), chat).ainvoke(
        initial_state(session(), question, mode), {'configurable': {'thread_id': question}})
    assert not result['errors'] and result['repairs'] == 0
    assert seen


def test_protocol_examples_parse_as_current_answer_schema():
    from backend.models import Answer
    for prompt in (prompts.SYSTEM, prompts.RISK_SYSTEM):
        start = prompt.index('{"status"')
        example, _ = json.JSONDecoder().raw_decode(prompt[start:])
        Answer.model_validate(example)
    assert {t['function']['name'] for t in TOOLS} == {'get_passages', 'search_knowledge'}


def test_instructions_do_not_teach_retired_output_fields():
    # Historical fields remain readable in storage. They are not the model's task.
    for prompt in (prompts.SYSTEM, prompts.ROUTER, prompts.RISK_SYSTEM):
        assert 'summary' not in prompt and 'kind' not in prompt
        assert 'interpretation' not in prompt and 'reflection' not in prompt
