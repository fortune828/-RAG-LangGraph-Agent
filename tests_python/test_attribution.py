"""Attribution regressions: local refusals must not disable neighboring claims."""
import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from backend.domain import calculate_hexagram
from backend.graph import build_graph, initial_state
from backend.knowledge import CURRENT_RELEASE, retrieve_exact
from backend.validation import AUTHORS, WORKS, asserted_names, requested_targets, validate_answer


@pytest.mark.parametrize('text', [
    '王弼认为此时应当谨慎。',
    '王弼认为无法仅凭卦象决定行动。',
    '这不代表现实预测，但王弼认为应立刻辞职。',
    '资料不足，王弼指出应当行动。',
    '不能断言王弼认为应当行动，但王弼强调应立刻辞职。',
    '无法预测王弼认为可以立即辞职。',
    '王弼认为目前无法判断、不能决定结果。',
])
def test_positive_claim_still_requires_author_evidence(text):
    assert asserted_names(text, AUTHORS) == ['wang-bi']


@pytest.mark.parametrize('text', [
    '王弼的材料未收录，不能据此推断他的观点。',
    '无法确认王弼认为应当行动。',
    '不代表王弼认为应当行动。',
    '不能说王弼认为应立刻辞职。',
    '王弼没有说应当行动。',
    '王弼并未明确指出这个现实问题的答案。',
    '本库尚无王弼对该爻的解释。',
    '王弼的解释尚未收录。',
    '现有材料只包括朱熹，不能冒充王弼的观点。',
])
def test_refusal_or_missing_material_is_not_a_positive_claim(text):
    assert asserted_names(text, AUTHORS) == []


def commentary(author):
    return retrieve_exact(dict(hexagramId=1, sections=['line'], lineIndices=[2],
                               authorIds=[author]))['evidence'][0]


def answer(text, evidence=None, *, status='answered'):
    return dict(status=status, summary='说明现有材料的范围。', paragraphs=[dict(kind='interpretation' if evidence else 'reflection', text=text, citations=(
        [dict(evidenceId=evidence['id'], quote=evidence['text'][:8])] if evidence else []))],
        limitations=['本库未收录所问作者材料。'] if status == 'insufficient' else [])


def test_supported_claim_and_honest_missing_material_pass():
    e = commentary('wang-bi')
    assert validate_answer(answer('王弼解释了这一爻的含义。', e), [e])[0]
    missing = answer('本库未收录焦循的解释，不能声称焦循认为应当行动。', status='insufficient')
    assert validate_answer(missing, [])[0]


def test_wrong_author_cannot_use_disclaimer_or_another_author_citation():
    e = commentary('zhu-xi')
    value = answer('无法预测现实，但王弼认为你应当立即行动。', e)
    assert 'AUTHOR_ATTRIBUTION_MISMATCH:wang-bi' in validate_answer(value, [e])[1]
    # Even without an explicit viewpoint verb, a named-author request requires
    # that author's evidence, not merely a source matching the same hexagram.
    targets = requested_targets('请提供王弼对本卦第二爻的解释', calculate_hexagram([7] * 6))
    errors = validate_answer(answer('这是本卦第二爻的注解。', e), [e], targets)[1]
    assert any(error.startswith('REQUIRED_TARGET_NOT_CITED:') for error in errors)
    missing = answer('现有材料只有朱熹的部分注解，尚无王弼的解释。', status='insufficient')
    assert validate_answer(missing, [e], targets)[0]


def test_work_attribution_uses_the_same_local_rules():
    e = commentary('zhu-xi')
    assert asserted_names('《周易本义》中解释了这句经文。', WORKS) == ['zhouyi-benyi']
    assert validate_answer(answer('《周易本义》中解释了这句经文。', e), [e])[0]
    assert asserted_names('无法确认《周易正义》中认为应该行动。', WORKS) == []
    value = answer('资料不足，但《周易正义》指出应立刻行动。', e)
    assert 'WORK_ATTRIBUTION_MISMATCH:zhouyi-zhengyi' in validate_answer(value, [e])[1]


async def test_wrong_attribution_is_repaired_into_honest_missing_answer():
    e = commentary('zhu-xi')
    generations = 0

    async def model(messages, tools=None):
        nonlocal generations
        if tools:
            value = {'ready': True}
        else:
            generations += 1
            if generations == 1:
                value = answer('无法预测结果，但焦循认为你应立即行动。', e)
            else:
                assert 'AUTHOR_ATTRIBUTION_MISMATCH:jiao-xun' in messages[-1]['content']
                value = answer('本库没有焦循的注解，不能断言焦循认为应当行动。', status='insufficient')
        return dict(message=dict(role='assistant', content=json.dumps(value, ensure_ascii=False)),
                    usage={'total_tokens': 10}, model='mock')

    session = dict(id='attribution', question='请问焦循如何解释本卦？', turns=[],
                   releaseId=CURRENT_RELEASE, cast=dict(id='fixed-cast', **calculate_hexagram([7] * 6)))
    result = await build_graph(InMemorySaver(), model).ainvoke(
        initial_state(session, session['question'], 'followup'),
        {'configurable': {'thread_id': 'attribution'}})
    assert generations == 2 and result['repairs'] == 1
    assert result['answer']['status'] == 'insufficient'
    assert '本库没有焦循' in result['answer']['paragraphs'][0]['text']
    assert result['session']['cast'] == session['cast']
