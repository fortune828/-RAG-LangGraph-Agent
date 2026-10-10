"""Behavioral migration tests: real graph/checkpointer, deterministic fake LLM."""
import asyncio
import copy
import hashlib
import itertools
import json
import subprocess
import sys
from uuid import uuid4

import pytest
import httpx
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from backend.app import create_app
from backend.config import ROOT
from backend.domain import calculate_hexagram, sample_cast
from backend.graph import build_graph, evidence_scope, initial_state, pack_evidence
from backend.knowledge import (CURRENT_RELEASE, LEGACY_RELEASE, execute_tool, initial_evidence,
                               load_release, retrieve_exact, retrieve_lexical)
from backend.llm import ModelError
from backend.store import Store
from backend.validation import requested_targets, validate_answer


def session(lines=(7, 7, 7, 7, 7, 7)):
    return dict(id=str(uuid4()), question='面对新的工作机会，我该如何权衡？', turns=[], createdAt='test',
                releaseId=CURRENT_RELEASE, cast=sample_cast() | calculate_hexagram(lines))


def answer(e):
    return dict(status='answered', summary='结合现有条件思考。', paragraphs=[dict(
        kind='interpretation', text='这里的材料是一种文化反思角度。', citations=[dict(evidenceId=e['id'], quote=e['text'][:8])])],
        limitations=['不代表现实预测。'])


def response(content=None, calls=None):
    message = dict(role='assistant', content=json.dumps(content, ensure_ascii=False) if content is not None else None)
    if calls:
        message['tool_calls'] = calls
    return dict(message=message, usage={'total_tokens': 10}, model='mock')


def tool_call(h=1, line=2, name='get_passages', args=None):
    return dict(id='call-' + str(uuid4()), type='function', function=dict(name=name, arguments=json.dumps(
        args or dict(hexagramId=h, sections=['line'], lineIndices=[line]))))


async def generic_chat(messages, tools=None):
    if tools:
        return response({'ready': True})
    context = json.loads(messages[1]['content'])
    return response(answer(context['参考原文'][0]))


def test_all_4096_calculations_and_evidence_budgets():
    names = json.loads((ROOT / 'data/upstream/names.json').read_text())
    for lines in itertools.product(range(6, 10), repeat=6):
        cast = calculate_hexagram(lines)
        assert cast['originalBinary'] == ''.join(str(v % 2) for v in lines)
        assert cast['changedBinary'] == ''.join('1' if v in (6, 7) else '0' for v in lines)
        for which in ['original', 'changed']:
            name = cast[which]['name']
            assert names[cast[which]['binary']] == (name.split('为')[0] if '为' in name else name[2:])
        evidence = initial_evidence(cast, '面对变化，我如何思考？')
        packed, _ = pack_evidence([e for e in evidence if e['layer'] == 'classic'],
                                  [e for e in evidence if e['layer'] != 'classic'])
        for i in cast['changingIndices']:
            assert any(e['id'] == f"zy-{cast['original']['number']}-line-{i + 1}" for e in packed)


def test_recorded_coins_recalculate_and_invalid_inputs():
    bits = iter([0, 0, 0, 0, 0, 1, 0, 1, 1, 1, 1, 1, 0, 0, 1, 0, 1, 1])
    cast = sample_cast(lambda: next(bits))
    assert cast['lines'] == [6, 7, 8, 9, 7, 8]
    assert [6 + sum(row) for row in cast['coins']] == cast['lines']
    for bad in [[], [True] * 6, [7.] * 6, [5] * 6]:
        with pytest.raises(ValueError):
            calculate_hexagram(bad)


def test_release_compatibility_filters_corrections_and_bm25():
    corpus, manifest = load_release()
    assert len(corpus) == 1076
    assert retrieve_exact(dict(hexagramId=32, sections=['tuan']), LEGACY_RELEASE)['evidence'] == []
    assert retrieve_exact(dict(hexagramId=32, sections=['tuan']))['evidence']
    for author in ['wang-bi', 'cheng-yi', 'zhu-xi']:
        records = retrieve_exact(dict(hexagramId=1, sections=['line'], lineIndices=[2], authorIds=[author]))['evidence']
        assert records and all(author in e['authorIds'] for e in records)
    assert retrieve_exact(dict(hexagramId=64, sections=['line'], lineIndices=[2], authorIds=['zhu-xi']))['coverageStatus'] == 'not_ingested'
    for q in ['潜龙勿用', '潛龍勿用']:
        assert 'zy-1-line-1' in [e['id'] for e in retrieve_lexical(dict(query=q, topK=6))['evidence']]
    assert 'zy-2-line-1' in [e['id'] for e in retrieve_lexical(dict(query='履霜，坚冰至', topK=6))['evidence']]
    fixes = json.loads((ROOT / f'data/knowledge/releases/{CURRENT_RELEASE}/corrections.json').read_text())
    parent = {e['id']: e for e in load_release(manifest['parentReleaseId'])[0]}
    for fix in fixes:
        e = next(e for e in corpus if e['id'] == fix['evidenceId'])
        assert e['text'] == fix['correctedText']
        assert parent[e['id']]['text'] == fix['originalText']
        assert validate_answer(answer(e), [e])[0]


def test_scope_is_enforced_by_server_not_prompt():
    cast = session()['cast']
    assert evidence_scope('我担心工资不稳定', cast, 'situation_followup') == [1]
    assert evidence_scope('履霜坚冰至出自哪里', cast, 'source_request') is None
    with pytest.raises(ValueError, match='EVIDENCE_SCOPE_DENIED'):
        execute_tool('get_passages', dict(hexagramId=2, sections=['judgment']), CURRENT_RELEASE, [1])
    hits = retrieve_lexical(dict(query='地势坤'), scope_ids=[1])['evidence']
    assert all(e['hexagramId'] == 1 for e in hits)
    with pytest.raises(ValueError, match='UNKNOWN_TOOL'):
        execute_tool('cast_again', {}, CURRENT_RELEASE, [1])


def test_validation_repair_paths_and_attribution():
    e = retrieve_exact(dict(hexagramId=1, sections=['line'], lineIndices=[2]))['evidence'][0]
    a = answer(e)
    a['paragraphs'][0]['citations'] *= 6
    errors = validate_answer(a, [e])[1]
    assert any('paragraphs.0.citations' in error and '5' in error for error in errors)
    missing = dict(status='insufficient', summary='本库未收录焦循《易章句》。', paragraphs=[dict(
        kind='reflection', text='本库有王弼、程颐、朱熹的部分注解，不能冒充焦循的观点。', citations=[])], limitations=['未收录。'])
    assert validate_answer(missing, [])[0]
    missing['paragraphs'][0]['text'] = '焦循认为你应立即行动。'
    assert 'AUTHOR_ATTRIBUTION_MISMATCH:jiao-xun' in validate_answer(missing, [])[1]
    a = answer(e)
    a['paragraphs'][0]['citations'][0]['quote'] = '编造引文'
    assert validate_answer(a, [e])[0] is None
    a = answer(e)
    targets = requested_targets('请给出本卦第五爻原文', session()['cast'])
    assert validate_answer(a, [e], targets)[0] is None


async def test_graph_exact_tool_and_one_repair():
    e = retrieve_exact(dict(hexagramId=1, sections=['line'], lineIndices=[2]))['evidence'][0]
    calls, generation = [], 0
    async def model(messages, tools=None):
        nonlocal generation
        calls.append(bool(tools))
        if len(calls) == 1:
            return response(calls=[tool_call()])
        if tools:
            assert any(m['role'] == 'tool' and e['id'] in m['content'] for m in messages)
            return response({'ready': True})
        generation += 1
        a = answer(e)
        if generation == 1:
            a['paragraphs'][0]['citations'] *= 6
        else:
            assert 'paragraphs.0.citations' in messages[-1]['content']
        return response(a)
    s = session()
    graph = build_graph(InMemorySaver(), model)
    result = await graph.ainvoke(initial_state(s, '第二爻原文是什么？', 'followup'), {'configurable': {'thread_id': 'exact'}})
    assert result['answer']['status'] == 'answered'
    assert result['session']['cast'] == s['cast']
    assert result['attempts'] == 1 and result['repairs'] == 1
    assert [e['status'] for e in result['trace'] if e['step'] == 'validate'] == ['failed', 'passed']


async def test_graph_can_answer_without_tool_or_citation_for_situation():
    async def model(messages, tools=None):
        if tools:
            return response({'ready': True})
        return response(dict(status='answered', summary='先看你明确提到的收入约束。',
                             paragraphs=[dict(kind='reflection', text='可以先核实现金储备和答复期限。', citations=[])], limitations=[]))
    graph = build_graph(InMemorySaver(), model)
    r = await graph.ainvoke(initial_state(session(), '我担心收入不稳定，如何考虑？', 'followup'),
                           {'configurable': {'thread_id': 'no-tool'}})
    assert r['answer']['status'] == 'answered' and r['attempts'] == 0
    assert r['intent'] == 'situation_followup'


async def test_graph_unknown_source_gets_one_feedback_and_global_search():
    routed = 0
    e = retrieve_exact(dict(hexagramId=2, sections=['line'], lineIndices=[1]))['evidence'][0]
    async def model(messages, tools=None):
        nonlocal routed
        if tools:
            routed += 1
            if routed == 2:
                return response(calls=[tool_call(name='search_knowledge', args={'query': '履霜坚冰至', 'topK': 4})])
            return response({'ready': True})
        return response(answer(e))
    graph = build_graph(InMemorySaver(), model)
    r = await graph.ainvoke(initial_state(session(), '履霜坚冰至出自哪里？', 'followup'),
                           {'configurable': {'thread_id': 'search'}})
    assert r['replans'] == 1 and r['attempts'] == 1
    assert r['answer']['paragraphs'][0]['citations'][0]['evidenceId'] == e['id']


async def test_duplicate_and_invalid_tools_are_bounded():
    async def model(messages, tools=None):
        if tools:
            return response(calls=[tool_call(h=2)])
        return response(dict(status='insufficient', summary='未取得资料。', paragraphs=[], limitations=['没有证据。']))
    graph = build_graph(InMemorySaver(), model)
    r = await graph.ainvoke(initial_state(session(), '补充解释当前卦象', 'followup'),
                           {'configurable': {'thread_id': 'bounded'}})
    assert r['attempts'] == 3
    codes = [e.get('code') for e in r['trace']]
    assert 'EVIDENCE_SCOPE_DENIED' in codes and 'REPEATED_TOOL_CALL' in codes


def test_api_contract_idempotency_legacy_delete_and_private_logs(tmp_path):
    app = create_app(tmp_path, generic_chat)
    sid, token, tid = [str(uuid4()) for _ in range(3)]
    headers = {'Authorization': 'Bearer ' + token}
    with TestClient(app) as client:
        assert client.get('/api/health').json()['orchestrator'] == 'langgraph'
        body = dict(requestId=sid, question='私密测试问题')
        created = client.post('/api/sessions', json=body, headers=headers)
        assert created.status_code == 201
        cast = created.json()['cast']
        assert client.post('/api/sessions', json=body, headers=headers).json()['cast'] == cast
        assert client.get('/api/sessions/' + sid).status_code == 404
        assert client.post('/api/sessions', json=dict(body, question='另一问题'), headers=headers).status_code == 409
        endpoint = f'/api/sessions/{sid}/turns'
        payload = dict(requestId=tid, question=body['question'])
        done = client.post(endpoint, json=payload, headers=headers).json()
        assert done['turns'][0]['status'] == 'completed'
        assert client.post(endpoint, json=payload, headers=headers).json() == done
        assert client.post(endpoint, json=dict(payload, question='冲突'), headers=headers).status_code == 409
        assert client.get('/api/sessions/' + sid, headers=headers).json()['cast'] == cast
        thread = done['turns'][0]['checkpointThreadId']
        assert '私密测试问题' not in (tmp_path / 'traces.jsonl').read_text()
        assert token not in (tmp_path / 'traces.jsonl').read_text()
        assert client.delete('/api/sessions/' + sid, headers=headers).status_code == 204
        assert client.get('/api/sessions/' + sid, headers=headers).status_code == 404
        import sqlite3
        with sqlite3.connect(tmp_path / 'checkpoints.sqlite') as db:
            assert db.execute('SELECT count(*) FROM checkpoints WHERE thread_id=?', (thread,)).fetchone()[0] == 0
        assert client.post('/api/sessions', content='x' * 17000, headers=headers).status_code == 400
        assert client.post('/api/sessions', json=body, headers=headers | {'Origin': 'https://evil.example'}).status_code == 403
    # Simulate an old session file without release ID. Its cast is never sampled anew.
    old = session()
    old.pop('releaseId')
    (tmp_path / f"{old['id']}.json").write_text(json.dumps(old | {'tokenHash': Store.token_hash(token)}))
    restored = Store(tmp_path).get(old['id'])
    assert restored['releaseId'] == LEGACY_RELEASE and restored['cast'] == old['cast']


@pytest.mark.parametrize('interruption', ['fail', 'crash', 'invalid'])
def test_checkpoint_resume_in_a_new_python_process(tmp_path, interruption):
    # First process persists prepare/route/tools, then fails during generation.
    # Second process must resume generation without repeating the tool or RNG.
    worker = ROOT / 'tests_python/restart_worker.py'
    for phase in [interruption, 'resume']:
        result = subprocess.run([sys.executable, str(worker), str(tmp_path), phase], cwd=ROOT, capture_output=True, text=True)
        assert result.returncode == (17 if phase == 'crash' else 0), result.stdout + result.stderr
    before = json.loads((tmp_path / 'failed.json').read_text())
    after = json.loads((tmp_path / 'resumed.json').read_text())
    assert before['cast'] == after['cast']
    assert len(after['turns']) == 1
    assert before['turns'][0]['id'] == after['turns'][0]['id']
    assert after['turns'][0]['status'] == 'completed'
    assert len([e for e in after['turns'][0]['trace'] if e['step'] == 'tool']) == 1
    if interruption == 'invalid':
        assert any(e['step'] == 'retry' for e in after['turns'][0]['trace'])


async def test_api_concurrent_replays_and_other_turns_are_locked(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0
    async def slow_chat(messages, tools=None):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return await generic_chat(messages, tools)
    app = create_app(tmp_path, slow_chat)
    sid, tid, token = [str(uuid4()) for _ in range(3)]
    headers = {'Authorization': 'Bearer ' + token}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            await client.post('/api/sessions', headers=headers, json=dict(requestId=sid, question='问题'))
            path = f'/api/sessions/{sid}/turns'
            payload = dict(requestId=tid, question='问题')
            pending = asyncio.create_task(client.post(path, json=payload, headers=headers))
            await asyncio.wait_for(entered.wait(), 5)
            try:
                assert (await client.post(path, json=payload, headers=headers)).status_code == 202
                assert (await client.post(path, json=dict(payload, requestId=str(uuid4())), headers=headers)).status_code == 409
                assert (await client.delete('/api/sessions/' + sid, headers=headers)).status_code == 409
            finally:
                release.set()
            assert (await pending).status_code == 200
            assert calls == 1


async def test_python_model_budget_blocks_before_call_and_accounts_failure():
    from backend.evaluate import BudgetedChat
    calls = 0
    async def failure(messages, tools=None):
        nonlocal calls
        calls += 1
        raise ModelError('MODEL_TIMEOUT_OR_NETWORK')
    budget = BudgetedChat(failure, max_calls=1, max_tokens=20000)
    with pytest.raises(ModelError, match='MODEL_TIMEOUT'):
        await budget([{'role': 'user', 'content': 'test'}])
    with pytest.raises(ModelError, match='EVALUATION_BUDGET'):
        await budget([{'role': 'user', 'content': 'test'}])
    assert calls == 1 and budget.unknown == 1 and budget.accounted > 3000
