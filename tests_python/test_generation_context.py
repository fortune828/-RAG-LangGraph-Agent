"""Generation receives relevant user facts and sources, not routing machinery."""
import json

from langgraph.checkpoint.memory import InMemorySaver

from backend.graph import build_graph, initial_state, target_availability
from backend.knowledge import retrieve_exact
from backend.validation import requested_targets
from tests_python.test_migration import response, session, tool_call


def missing_answer():
    return dict(status='insufficient',
                paragraphs=[dict(text='未取得对应材料，不能用他人注解代替。', citations=[])],
                limitations=['所问材料未取得。'])


def test_exact_coverage_distinguishes_retrievable_absent_and_unresolved():
    s = session()

    def availability(question, evidence=()):
        return target_availability(initial_state(s, question, 'followup') | dict(
            evidence=list(evidence), targets=requested_targets(question, s['cast'])))

    second = '本卦第二爻原文'
    assert availability(second)['retrievable']
    e = retrieve_exact(dict(hexagramId=1, sections=['line'], lineIndices=[2]))['evidence']
    assert availability(second, e)['available']
    assert availability('焦循对本卦第二爻的注解')['unavailable']
    unknown = availability('朱熹《尚待核实的别名》对本卦第二爻的注解')
    assert unknown['unresolvedBooks'] == ['尚待核实的别名']
    assert not unknown['unavailable'] and not unknown['available']
    # The author AND the requested work must match, not either separately.
    assert availability('王弼《周易本义》对本卦第二爻的注解')['unavailable']


async def test_known_absence_skips_tools_and_excludes_other_authors():
    calls = 0

    async def model(messages, tools=None):
        nonlocal calls
        calls += 1
        assert tools is None
        context = json.loads(messages[1]['content'])
        assert context['参考原文'] == []
        assert any('未收录' in text and '焦循' in text for text in context['资料说明'])
        assert '朱熹' not in messages[1]['content']
        return response(missing_answer())

    s = session()
    result = await build_graph(InMemorySaver(), model).ainvoke(
        initial_state(s, '请给焦循《易章句》对本卦第二爻的注解', 'followup'),
        {'configurable': {'thread_id': 'missing-author'}})
    assert calls == 1 and result['attempts'] == 0 and not result['errors']
    assert result['answer']['status'] == 'insufficient' and result['session']['cast'] == s['cast']


async def test_unknown_title_does_not_become_confirmed_absence():
    routed = 0

    async def model(messages, tools=None):
        nonlocal routed
        if tools:
            routed += 1
            return response({'ready': True})
        context = json.loads(messages[1]['content'])
        assert any('书名尚未确认' in text for text in context['资料说明'])
        assert not any(text.startswith('当前资料库未收录') for text in context['资料说明'])
        assert not context['参考原文']
        return response(missing_answer())

    result = await build_graph(InMemorySaver(), model).ainvoke(
        initial_state(session(), '朱熹《尚待核实的别名》对本卦第二爻的解释', 'followup'),
        {'configurable': {'thread_id': 'unknown-title'}})
    assert routed == 1 and not result['errors']


async def test_final_context_has_roles_and_no_routing_state():
    s = session()
    s['turns'] = [dict(status='completed', question='我只有一个月积蓄。', evidence=[],
                      answer=dict(summary='OLD_ASSISTANT_MARKER', paragraphs=[]))]

    async def model(messages, tools=None):
        if tools:
            return response({'ready': True})
        assert len(messages) == 2
        context = json.loads(messages[1]['content'])
        assert context['核心问题'] == s['question']
        assert context['近期对话'] == [dict(user='我只有一个月积蓄。', assistant='OLD_ASSISTANT_MARKER')]
        for internal in ['recentHistory', 'allowedHexagramIds',
                         'retrievableMissingTargets', 'coverageStatus', 'knowledgeCoverage']:
            assert internal not in messages[1]['content']
        return response(dict(status='answered', paragraphs=[dict(text='一个月积蓄需要优先保留必要生活费用。', citations=[])], limitations=[]))

    result = await build_graph(InMemorySaver(), model).ainvoke(
        initial_state(s, '房租也要付，下一步怎么考虑？', 'followup'),
        {'configurable': {'thread_id': 'clean-generation'}})
    assert not result['errors']


async def test_irrelevant_author_is_rejected_even_if_present_in_router_context():
    e = retrieve_exact(dict(hexagramId=1, sections=['line'], lineIndices=[2], authorIds=['zhu-xi']))['evidence'][0]
    generations = 0

    async def model(messages, tools=None):
        nonlocal generations
        assert tools is None
        generations += 1
        if generations == 1:
            value = missing_answer()
            value['paragraphs'] = [dict(text='朱熹的另一段注解。',
                citations=[dict(evidenceId=e['id'], quote=e['text'][:8], claim='朱熹的另一段注解。')])]
            return response(value)
        assert 'UNKNOWN_EVIDENCE' in messages[-1]['content']
        return response(missing_answer())

    result = await build_graph(InMemorySaver(), model).ainvoke(
        initial_state(session(), '请给焦循对本卦第二爻的注解', 'followup'),
        {'configurable': {'thread_id': 'wrong-substitution'}})
    assert generations == 2 and result['repairs'] == 1 and not result['errors']


async def test_search_miss_is_not_reported_as_corpus_absence():
    async def model(messages, tools=None):
        if tools:
            return response({'ready': True}) if any(m['role'] == 'tool' for m in messages) else response(
                calls=[tool_call(name='search_knowledge', args=dict(query='zzzznonexistentphrase', topK=4))])
        context = json.loads(messages[1]['content'])
        assert not context['参考原文']
        assert not any(text.startswith('当前资料库未收录') for text in context['资料说明'])
        return response(missing_answer())

    result = await build_graph(InMemorySaver(), model).ainvoke(
        initial_state(session(), 'zzzznonexistentphrase出自哪里？', 'followup'),
        {'configurable': {'thread_id': 'search-no-hit'}})
    assert result['attempts'] == 1 and not result['errors']
