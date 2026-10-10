"""Evaluation seams must not alter production behavior or leak scoring labels."""
import asyncio
import json

from langgraph.checkpoint.memory import InMemorySaver

from backend import graph
from backend.evaluate import BudgetedChat
from backend.knowledge import retrieve_exact
from backend.llm import ModelError
from evaluation.formal_run import fixed_case, model_routing_graph, ready


def case():
    return fixed_case(dict(caseId='eval',groupId='g',split='development',category='lookup',
        question='想核对本卦的文字。',currentQuestion='请给出本卦第二爻原文。',mode='followup',
        lineValues=[7]*6,history=[],semanticReference='秘密评分标签不得进入生成',gold=['hidden']))


async def test_router_ablation_changes_only_shortcut_without_global_patch():
    old=graph.GRAPH_VERSION
    module,source=model_routing_graph()
    assert graph.GRAPH_VERSION==old
    assert source.count('evaluation-only exact-policy ablation')==1
    c=case();e=retrieve_exact(dict(hexagramId=1,sections=['line'],lineIndices=[2]))['evidence'][0]
    counts={}
    for name,mod in [('H',graph),('D',module)]:
        counts[name]=0
        async def model(messages,tools=None):
            assert '秘密评分标签' not in json.dumps(messages,ensure_ascii=False)
            if tools:
                counts[name]+=1
                message=dict(role='assistant',content=None,tool_calls=[dict(id='q',type='function',function=dict(
                    name='get_passages',arguments=json.dumps(dict(hexagramId=1,sections=['line'],lineIndices=[2]))))])
            else:
                message=dict(role='assistant',content=json.dumps(dict(status='answered',paragraphs=[dict(text=e['text'],citations=[dict(evidenceId=e['id'],quote=e['text'],claim=e['text'])])],limitations=[])))
            return dict(message=message,usage={'total_tokens':10})
        result=await mod.build_graph(InMemorySaver(),model).ainvoke(mod.initial_state(c['session'],c['question'],c['mode']),{'configurable':{'thread_id':name}})
        assert result['answer']['status']=='answered' and not result['errors']
    assert counts=={'H':0,'D':1}


async def test_fixed_arm_cannot_fetch_missing_target_or_hide_failure():
    c=case();requests=[]
    async def model(messages,tools=None):
        requests.append(tools)
        content=dict(status='insufficient',paragraphs=[dict(text='目前未取得指定原文。',citations=[])],limitations=[])
        return dict(message=dict(role='assistant',content=json.dumps(content)),usage={'total_tokens':10})
    result=await graph.build_graph(InMemorySaver(),model,route_policy=ready).ainvoke(graph.initial_state(c['session'],c['question'],c['mode']),{'configurable':{'thread_id':'b'}})
    assert all(t is None for t in requests)
    assert result['attempts']==0 and result['answer']['status']=='insufficient'


async def test_concurrent_budget_reserves_before_dispatch():
    gate=asyncio.Event();dispatched=[]
    async def transport(messages,tools=None):
        dispatched.append(True);await gate.wait()
        return dict(message={'content':'{}'},usage={'total_tokens':10})
    shared=BudgetedChat(transport,max_calls=1,max_tokens=100000)
    async def first():
        return await BudgetedChat(transport,parent=shared,max_tokens=100000)([])
    task=asyncio.create_task(first());await asyncio.sleep(0)
    try:
        await BudgetedChat(transport,parent=shared,max_tokens=100000)([])
        assert False,'budget should reject before second dispatch'
    except ModelError as exc:
        assert str(exc)=='EVALUATION_BUDGET_EXHAUSTED'
    gate.set();await task
    assert len(dispatched)==1 and shared.calls==1 and shared.reported==10


def test_memory_ablation_does_not_require_sources_it_deliberately_withheld():
    from evaluation.formal_run import memory_answer
    candidate = {'status': 'answered', 'paragraphs': [{'text': '朱熹解释了这一句。', 'citations': []}], 'limitations': []}
    answer, errors = memory_answer(candidate)
    assert answer == candidate and errors == []  # Accuracy belongs to independent semantic review.
    candidate['paragraphs'][0]['citations'] = [{'evidenceId': 'invented', 'quote': '潜龙勿用', 'claim': '朱熹解释了这一句'}]
    assert memory_answer(candidate) == (None, ['MEMORY_ABLATION_HAS_CITATIONS'])
