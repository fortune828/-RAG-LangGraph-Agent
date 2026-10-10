"""User-visible answer boundaries and regeneration of an ended checkpoint."""
import asyncio
import copy
import json
from uuid import uuid4

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from backend.app import create_app
from backend.graph import build_graph, initial_state
from backend.knowledge import retrieve_exact
from backend.validation import requested_targets, validate_answer
from tests_python.test_migration import answer, generic_chat, response, session


def test_mixed_paragraphs_and_scoped_citations_preserve_source_checks():
    e = retrieve_exact(dict(hexagramId=1, sections=['xiang']))['evidence'][0]
    result = answer(e)
    # The same paragraph may cite a classical claim and discuss a real decision.
    result['paragraphs'][0]['text'] += '先确认现金储备和录用条件，再比较选择。'
    assert validate_answer(result, [e], require_citations=True)[0]
    claim = result['paragraphs'][0]['citations'][0]['claim']
    assert '现金储备' not in claim
    no_sources = copy.deepcopy(result)
    no_sources['paragraphs'][0]['citations'] = []
    assert validate_answer(no_sources, [e], require_citations=True)[0] is None
    # The validator cannot infer which uncited sentences are traditional claims.
    assert validate_answer(no_sources, [e], require_citations=False)[0]
    for old_field in ('kind', 'summary'):
        legacy = copy.deepcopy(result)
        if old_field == 'kind':
            legacy['paragraphs'][0]['kind'] = 'reflection'
        else:
            legacy['summary'] = '旧结论'
        assert validate_answer(legacy, [e])[0] is None
    forged = copy.deepcopy(result)
    forged['paragraphs'][0]['citations'][0]['quote'] = '这不是所引原文'
    assert validate_answer(forged, [e])[0] is None
    unanchored = copy.deepcopy(result)
    unanchored['paragraphs'][0]['citations'][0]['claim'] = '正文没有这句话'
    assert validate_answer(unanchored, [e])[0] is None
    missing_claim = copy.deepcopy(result)
    del missing_claim['paragraphs'][0]['citations'][0]['claim']
    assert validate_answer(missing_claim, [e])[0] is None
    unknown = copy.deepcopy(result)
    unknown['paragraphs'][0]['citations'][0]['evidenceId'] = 'made-up'
    assert validate_answer(unknown, [e])[0] is None
    targets = requested_targets('本卦第二爻原文', session()['cast'])
    assert validate_answer(result, [e], targets)[0] is None


async def test_initial_graph_allows_separate_practical_paragraph():
    async def model(messages, tools=None):
        context = json.loads(messages[1]['content'])
        assert context['卦象']['爻名'] == ['少阳'] * 6
        result = answer(context['参考原文'][0])
        result['paragraphs'].append(dict(text='先核实你的现金储备与工作条件。', citations=[]))
        return response(result)
    graph = build_graph(InMemorySaver(), model)
    s = session()
    result = await graph.ainvoke(initial_state(s, s['question'], 'initial'),
                                {'configurable': {'thread_id': 'initial-citations'}})
    assert result['answer']['status'] == 'answered'
    assert result['errors'] == [] and result['repairs'] == 0


async def test_initial_explicit_source_question_can_retrieve_missing_target():
    from tests_python.test_migration import tool_call
    e = retrieve_exact(dict(hexagramId=1, sections=['line'], lineIndices=[2]))['evidence'][0]

    async def model(messages, tools=None):
        if tools:
            return response({'ready': True}) if any(m['role'] == 'tool' for m in messages) else response(calls=[tool_call()])
        return response(answer(e))

    graph = build_graph(InMemorySaver(), model)
    result = await graph.ainvoke(initial_state(session(), '请给出本卦第二爻原文', 'initial'),
                                {'configurable': {'thread_id': 'initial-source'}})
    assert result['errors'] == [] and result['attempts'] == 1
    assert result['answer']['paragraphs'][0]['citations'][0]['evidenceId'] == e['id']


async def test_failed_checkpoint_retry_is_real_and_concurrent_retry_is_idempotent(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    fail, generations = True, 0

    async def model(messages, tools=None):
        nonlocal generations
        if fail:
            generations += 1
            return response({'broken': True})
        generations += 1
        # A retry starts with clean generation input, not accumulated failures.
        assert not any('"broken"' in str(m.get('content')) for m in messages)
        entered.set()
        await release.wait()
        return await generic_chat(messages, tools)

    app = create_app(tmp_path, model)
    sid, tid, token = [str(uuid4()) for _ in range(3)]
    headers = {'Authorization': 'Bearer ' + token}
    payload = dict(requestId=tid, question='面对工作变化怎么权衡？')
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            created = (await client.post('/api/sessions', headers=headers,
                                        json=dict(requestId=sid, question=payload['question']))).json()
            path = f'/api/sessions/{sid}/turns'
            failed = (await client.post(path, headers=headers, json=payload)).json()
            turn = failed['turns'][0]
            assert turn['status'] == 'failed' and turn['error'] == 'ANSWER_VALIDATION_FAILED'
            assert 'answer' not in turn
            assert generations == 2
            checkpoint = await app.state.graph.aget_state({'configurable': {'thread_id': turn['checkpointThreadId']}})
            assert not checkpoint.next and checkpoint.values['errors']
            assert (await client.post(path, headers=headers, json=payload)).json() == failed
            assert generations == 2
            fail = False
            pending = asyncio.create_task(client.post(path, headers=headers, json=payload | {'retry': True}))
            await asyncio.wait_for(entered.wait(), 5)
            try:
                duplicate = await client.post(path, headers=headers, json=payload | {'retry': True})
                assert duplicate.status_code == 202
            finally:
                release.set()
            done = (await pending).json()
            assert done['cast'] == created['cast']
            assert len(done['turns']) == 1 and done['turns'][0]['id'] == tid
            assert done['turns'][0]['status'] == 'completed' and generations == 3
            assert any(e['step'] == 'retry' for e in done['turns'][0]['trace'])


async def test_business_insufficient_is_completed_and_does_not_regenerate(tmp_path):
    calls = 0

    async def model(messages, tools=None):
        nonlocal calls
        calls += 1
        return response(dict(status='insufficient',
                             paragraphs=[dict(text='本库未收录指定作者的注解。', citations=[])], limitations=['缺少该作者材料。']))

    app = create_app(tmp_path, model)
    sid, tid, token = [str(uuid4()) for _ in range(3)]
    headers = {'Authorization': 'Bearer ' + token}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            await client.post('/api/sessions', headers=headers, json=dict(requestId=sid, question='请给焦循注解'))
            path = f'/api/sessions/{sid}/turns'
            payload = dict(requestId=tid, question='请给焦循注解')
            done = (await client.post(path, headers=headers, json=payload)).json()
            assert done['turns'][0]['status'] == 'completed'
            assert done['turns'][0]['answer']['status'] == 'insufficient'
            assert (await client.post(path, headers=headers, json=payload | {'retry': True})).json() == done
            assert calls == 1


@pytest.mark.parametrize('legacy_kind', [None, 'reflection'])
async def test_saved_legacy_answer_is_read_unchanged(tmp_path, legacy_kind):
    """The new generation schema must not invalidate already saved sessions."""
    from backend.store import Store

    sid, token, tid = [str(uuid4()) for _ in range(3)]
    store = Store(tmp_path)
    saved = store.create(sid, token, '旧会话')
    legacy = dict(status='answered', summary='以前的回答。',
                  paragraphs=[dict(text='请根据现实条件考虑。', citations=[])], limitations=[])
    if legacy_kind:
        legacy['paragraphs'][0]['kind'] = legacy_kind
    saved['turns'] = [dict(id=tid, status='completed', question='旧会话', answer=legacy,
                          evidence=[], trace=[])]
    store.save(saved)

    async def forbidden_model(*args, **kwargs):
        raise AssertionError('Reading an old answer must not regenerate it')

    app = create_app(tmp_path, forbidden_model)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.get(f'/api/sessions/{sid}', headers={'Authorization': 'Bearer ' + token})
            assert response.status_code == 200
            assert response.json()['turns'][0]['answer'] == legacy
    assert Store(tmp_path).get(sid)['turns'][0]['answer'] == legacy
