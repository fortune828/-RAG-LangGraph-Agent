import json

from langgraph.checkpoint.memory import InMemorySaver

from backend.graph import build_graph, initial_state, evidence_scope
from backend.knowledge import canonical_tool_args, retrieve_exact
from backend.validation import validate_answer
from tests_python.test_migration import session, response, answer


def test_canonical_tool_parameters():
    assert canonical_tool_args('search_knowledge', {'query': ' 履霜，堅冰至 '}) == canonical_tool_args(
        'search_knowledge', {'query': '履霜坚冰至', 'topK': 4})
    assert canonical_tool_args('get_passages', {'hexagramId': 1, 'sections': ['line', 'xiang', 'line']}) == canonical_tool_args(
        'get_passages', {'hexagramId': 1, 'sections': ['xiang', 'line']})


def test_fuzzy_source_scope_is_server_controlled():
    cast = session()['cast']
    assert evidence_scope('朱熹是不是说过，大意是困境时不要急着行动？', cast, 'source_request') is None
    assert evidence_scope('朱熹对本卦是不是说过不要急着行动？', cast, 'source_request') == [1]
    assert evidence_scope('是不是说过不要急着行动？', cast, 'situation_followup') == [1]


def test_partial_answer_does_not_waive_source_requirements():
    target = dict(hexagramId=1, layer='commentary', authorId='jiao-xun')
    value = dict(status='insufficient', summary='无法确认指定注解，但可讨论现实条件。',
                 paragraphs=[dict(kind='reflection', text='可先核对录用条件和必要支出。', citations=[])],
                 limitations=['未收录所问作者注解。'])
    assert validate_answer(value, [], [target], True)[0]
    value['status'] = 'answered'
    assert validate_answer(value, [], [target], True)[1]


async def test_life_followup_can_still_include_grounded_interpretation():
    async def model(messages, tools=None):
        if tools:
            return response({'ready': True})
        context = json.loads(messages[1]['content'])
        assert context['参考原文']
        return response(answer(context['参考原文'][0]))
    result = await build_graph(InMemorySaver(), model).ainvoke(
        initial_state(session(), '我想更积极地找工作，怎么考虑？', 'followup'),
        {'configurable': {'thread_id': 'life-with-citation'}})
    assert not result['errors']
    assert result['answer']['paragraphs'][0]['citations']


async def test_fuzzy_author_target_is_not_silently_bound_to_current_hexagram():
    async def model(messages, tools=None):
        if tools:
            context = json.loads(messages[1]['content'])
            assert context['allowedHexagramIds'] is None
            assert all(t['hexagramId'] is None for t in context['requiredEvidenceTargetsForAnswered'])
            return response({'ready': True})
        return response(dict(status='insufficient', summary='无法确认所问观点。', paragraphs=[], limitations=['未查得对应原文。']))
    result = await build_graph(InMemorySaver(), model).ainvoke(
        initial_state(session(), '朱熹是不是说过，大意是困境时不要急着行动？', 'followup'),
        {'configurable': {'thread_id': 'fuzzy-author'}})
    assert not result['errors']
