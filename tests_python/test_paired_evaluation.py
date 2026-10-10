import copy
import json
import subprocess
import types

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from backend import llm
from backend.config import ROOT
from backend.evaluate import BudgetedChat
from backend.graph import build_graph, initial_state
from evaluation.paired import BASELINE_PATH, Recorder, RecordedChat, load_cases, smoke_chat, visible
from evaluation.scoring import score_row, aggregate
from evaluation.workflow import rule_route


def test_old_baseline_refuses_new_behavior(tmp_path):
    from evaluation.paired import manifest
    with pytest.raises(ValueError, match='FROZEN_BEHAVIOR_CHANGED'):
        manifest(types.SimpleNamespace(live=False), tmp_path)


def test_archived_schema_stays_separate_from_new_generation():
    from evaluation.scoring import schema_valid
    old = json.dumps(dict(status='answered', summary='旧答案', paragraphs=[dict(
        kind='reflection', text='旧正文', citations=[])], limitations=[]))
    assert schema_valid(old, legacy=True)
    assert not schema_valid(old)


@pytest.mark.parametrize('question,ids',[
    ('请给出本卦第二爻原文。',{'zy-1-line-2'}),
    ('请给出本卦第三爻和第四爻原文。',{'zy-1-line-3','zy-1-line-4'}),
    ('“履霜，坚冰至”出自哪里？',{'zy-2-line-1'}),
])
async def test_rule_policy_retrieves_without_model_decisions_or_gold(question,ids):
    case=next(c for c in load_cases()['cases'] if c['caseId']=='source-line')
    async def model(messages,tools=None):
        assert tools is None  # C never uses a fake paid route.
        assert 'GOLD_SENTINEL' not in json.dumps(messages)
        return await smoke_chat(messages,tools)
    result=await build_graph(InMemorySaver(),model,route_policy=rule_route).ainvoke(
        initial_state(copy.deepcopy(case['session']),question,'followup'),
        {'configurable':{'thread_id':question}})
    assert ids <= {e['id'] for e in result['evidence']}
    assert 1 <= result['attempts'] <= 3
    assert any(e['step']=='route' and e['code']=='RULE_POLICY' for e in result['trace'])


async def test_identical_preparation_and_no_rule_lookup_for_existing_material():
    case=next(c for c in load_cases()['cases'] if c['caseId']=='evidence-present')
    prepared=[]
    for policy in (rule_route,None):
        events=[]
        result=await build_graph(InMemorySaver(),smoke_chat,route_policy=policy,observe=events.append).ainvoke(
            initial_state(copy.deepcopy(case['session']),case['question'],case['mode']),
            {'configurable':{'thread_id':str(policy)}})
        prepared.append(next(e for e in events if e['kind']=='prepare')['evidence'])
        assert result['attempts']==0
    assert prepared[0]==prepared[1]


async def test_recording_distinguishes_rejection_execution_empty_and_budget(tmp_path):
    from tests_python.test_migration import response, tool_call
    case=next(c for c in load_cases()['cases'] if c['caseId']=='source-line')
    replies=0
    async def fake(messages,tools=None):
        nonlocal replies
        if tools and replies==0:
            replies+=1
            return response(calls=[tool_call(h=2), # denied scope, counts an attempt
                tool_call(args=dict(hexagramId=1,sections=['line'],authorIds=['jiao-xun'],layers=['commentary'])),
                tool_call(),tool_call()]) # fourth denied budget, no attempt consumed
        return await smoke_chat(messages,tools)
    events=[]
    await build_graph(InMemorySaver(),fake,observe=events.append).ainvoke(
        initial_state(case['session'],'请查询当前卦有哪些原文资料。','followup'),{'configurable':{'thread_id':'tool-events'}})
    actual=[e for e in events if e['kind']=='tool_result']
    assert [e['executionStatus'] for e in actual]==['rejected','empty','ok','rejected']
    assert [e['budgetCounted'] for e in actual]==[True,True,True,False]
    assert actual[0]['returnedResult']['error']=='EVIDENCE_SCOPE_DENIED'
    assert actual[2]['retrievedResult']['evidence']
    assert sum(e['kind']=='tool_request' for e in events)==4


async def test_unknown_usage_and_global_budget_denial_are_not_zero_cost(tmp_path):
    async def unknown(messages,tools=None):
        return {'message':{'role':'assistant','content':'{}'},'usage':{}}
    global_budget=BudgetedChat(unknown,max_calls=1,max_tokens=100000)
    local=BudgetedChat(unknown,max_calls=6,max_tokens=100000,parent=global_budget)
    recorder=Recorder(tmp_path/'events.jsonl','test','D')
    chat=RecordedChat(local,recorder)
    await chat([{'role':'user','content':'test'}])
    with pytest.raises(llm.ModelError,match='BUDGET'):
        await chat([{'role':'user','content':'test'}])
    assert local.calls==global_budget.calls==1 and local.unknown==1
    row=score_row(dict(caseId='test',variant='D',scenario='test',status='failed',durationMs=10),recorder.events)
    assert row['modelRequests']==2 and row['modelCalls']==1 and row['budgetRejectedRequests']==1
    assert row['usage']['total_tokens']['total'] is None
    assert aggregate([row])['usage']['total_tokens']['unknownCalls']==1


async def test_length_response_preserves_reported_usage_and_visible_candidate(monkeypatch,tmp_path):
    monkeypatch.setenv('DEEPSEEK_API_KEY','TEST_ONLY_CREDENTIAL')
    def handler(request):
        return httpx.Response(200,json={'id':'test-id','model':'reported-model',
            'usage':{'prompt_tokens':11,'completion_tokens':14,'total_tokens':25,'prompt_cache_hit_tokens':3},
            'choices':[{'finish_reason':'length','message':{'role':'assistant','content':'{"partial":',
                'reasoning_content':'INTERNAL_REASONING_SENTINEL'}}]})
    client=httpx.AsyncClient
    monkeypatch.setattr(llm.httpx,'AsyncClient',lambda **kw:client(transport=httpx.MockTransport(handler),**kw))
    budget=BudgetedChat(llm.deepseek_chat,max_calls=2,max_tokens=100000)
    recorder=Recorder(tmp_path/'events.jsonl','test','D')
    with pytest.raises(llm.ModelError,match='INCOMPLETE'):
        await RecordedChat(budget,recorder)([{'role':'user','content':'synthetic'}])
    assert budget.reported==25 and budget.accounted==25 and budget.unknown==0
    result=recorder.events[-1]
    assert result['response']['message']['content']=='{"partial":'
    assert result['response']['usage']['prompt_cache_hit_tokens']==3
    text=(tmp_path/'events.jsonl').read_text()
    assert 'TEST_ONLY_CREDENTIAL' not in text and 'INTERNAL_REASONING_SENTINEL' not in text
    assert 'reasoningOmitted' in text


async def test_repair_candidates_and_zero_model_error_usage_are_preserved(tmp_path):
    case=next(c for c in load_cases()['cases'] if c['caseId']=='lunch-choice')
    calls=0
    async def fake(messages,tools=None):
        nonlocal calls
        calls+=1
        if calls==1:
            return dict(message=dict(role='assistant',content='{"bad":true}'),usage={'total_tokens':9})
        return await smoke_chat(messages,tools)
    recorder=Recorder(tmp_path/'events.jsonl',case['caseId'],'C')
    budget=BudgetedChat(fake,max_calls=6,max_tokens=100000)
    result=await build_graph(InMemorySaver(),RecordedChat(budget,recorder),observe=recorder,route_policy=rule_route).ainvoke(
        initial_state(case['session'],case['question'],'initial'),{'configurable':{'thread_id':'repair-capture'}})
    vals=[e for e in recorder.events if e['kind']=='validation']
    assert vals[0]['candidate']=={'bad':True} and vals[0]['errors']
    assert not vals[1]['errors'] and result['repairs']==1
    row=score_row(dict(caseId=case['caseId'],variant='C',scenario='daily',status='completed',
        answer=result['answer'],durationMs=1),recorder.events)
    assert row['firstSchemaValid'] is False and row['finalMechanicalValid']
    assert row['repairs']==1 and row['usage']['prompt_tokens']['unknownCalls']==1


def test_interrupted_request_has_unknown_dispatch_and_usage():
    events=[dict(kind='model_request',callId=1,stage='route'),
            dict(kind='model_result',callId=1,stage='route',status='ok',budgetCounted=True,
                 response=dict(usage={'total_tokens':7})),
            dict(kind='model_request',callId=2,stage='generate')]
    row=score_row(dict(caseId='interrupted',variant='D',scenario='test',status='running'),events)
    assert row['modelRequests']==2 and row['modelCalls']==1
    assert row['unresolvedModelRequests']==1 and row['modelCallsTotal'] is None
    assert row['usage']['total_tokens']==dict(reportedSum=7,unknownCalls=1,total=None)
    totals=aggregate([row])
    assert totals['attempted']==0 and totals['statuses']['running']==1
    assert totals['modelCallsTotal'] is None
    assert totals['usage']['total_tokens']['total'] is None
