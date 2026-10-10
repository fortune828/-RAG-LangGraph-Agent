"""Small paired prompt experiment. No production routing or schema changes.
Usage: .venv/bin/python evaluation/reports/prompt-subtraction-20261010/run.py --live --output <new directory>
"""
import argparse
import asyncio
import copy
import json
import subprocess
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from langgraph.checkpoint.memory import InMemorySaver
from backend import graph, prompts, knowledge
from backend.config import MODEL
from backend.evaluate import BudgetedChat
from backend.llm import deepseek_chat, request_payload
from backend.validation import validate_answer
from evaluation.paired import RecordedChat, Recorder, load_cases, smoke_chat, sha, write_json, visible
from evaluation.workflow import rule_route

BASELINE = '2456f97'
CASES = ('lunch-specific', 'haircut-kan', 'project-decision', 'life-correction', 'risk-medical', 'source-line')


def frozen(name):
    source = subprocess.check_output(['git', 'show', f'{BASELINE}:backend/{name}.py'], cwd=ROOT).decode()
    module = types.ModuleType('backend.frozen_' + name)
    exec(compile(source, f'{BASELINE}:{name}.py', 'exec'), module.__dict__)
    return module, source


def versions():
    if prompts.PROMPT_VERSION != 'evidence-agent-python-v16' or graph.GRAPH_VERSION != 'python-graph-v10':
        raise ValueError('FROZEN_PROMPT_CHANGED: this comparison requires graph v10 / prompt v16')
    old_graph, graph_source = frozen('graph')
    old_prompts, prompt_source = frozen('prompts')
    old_knowledge, knowledge_source = frozen('knowledge')
    for name in ('SYSTEM', 'RISK_SYSTEM', 'ROUTER', 'PROMPT_VERSION'):
        setattr(old_graph, name, getattr(old_prompts, name))
    old_graph.TOOLS = old_knowledge.TOOLS
    return {'v15': old_graph, 'v16': graph}, dict(graph=graph_source, prompts=prompt_source, knowledge=knowledge_source)


async def prepare(module, case):
    """Identical deterministic retrieval for generation comparison, no paid routing."""
    captured = []
    async def capture(messages, tools=None):
        assert tools is None
        captured.append(copy.deepcopy(messages))
        return await smoke_chat(messages)
    state = await module.build_graph(InMemorySaver(), capture, route_policy=rule_route).ainvoke(
        module.initial_state(copy.deepcopy(case['session']), case['question'], case['mode']),
        {'configurable': {'thread_id': case['caseId']}})
    return captured[0], state


def facts(messages):
    context = json.loads(messages[1]['content'])
    # Model-facing instruction differences are the treatment; case data are held fixed.
    return {k: context[k] for k in ('核心问题', '当前问题', '近期对话', '参考原文', '卦象', '本轮阶段') if k in context}


async def run(output, live):
    output.mkdir(parents=True, exist_ok=False)
    modules, old_sources = versions()
    cases = [next(c for c in load_cases()['cases'] if c['caseId']==cid) for cid in CASES]
    prepared = {v: {} for v in modules}
    inputs = []
    for case in cases:
        for v, module in modules.items():
            prepared[v][case['caseId']] = await prepare(module, case)
        a, b = (prepared[v][case['caseId']] for v in modules)
        assert facts(a[0]) == facts(b[0])
        assert a[1]['evidence'] == b[1]['evidence'] and a[1]['targets'] == b[1]['targets']
        inputs.append(dict(case=case, factHash=sha(facts(a[0])), messages={v:prepared[v][case['caseId']][0] for v in modules}))
    write_json(output/'inputs.json', inputs)
    for name, source in old_sources.items():
        target=output/'source/v15'/f'{name}.py';target.parent.mkdir(parents=True,exist_ok=True);target.write_text(source)
    files=list((ROOT/'backend').glob('*.py')) + [ROOT/'evaluation/paired.py',ROOT/'evaluation/workflow.py',ROOT/'requirements.txt',Path(__file__)]
    for p in files:
        target=output/'source/v16'/p.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(p.read_bytes())
    write_json(output/'manifest.json', dict(mode='real_model' if live else 'scripted_mechanical_only',baseline=BASELINE,
        promptVersions={v:m.PROMPT_VERSION for v,m in modules.items()},graphVersion=graph.GRAPH_VERSION,
        releaseId=knowledge.CURRENT_RELEASE,model=MODEL,modelOptions={k:v for k,v in request_payload([]).items() if k!='messages'},
        sourceHashes={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in files},
        commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(),dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT)),
        note='Six fixed-data generation pairs: four conversation cases plus medical/source boundary cases. Deterministic rule retrieval prepares equal evidence. System and dynamic instructions differ. Single sample, alternating order, no repair/best-of. A separate source-line router pair tests the first tool decision only. Not end-to-end quality or held-out evaluation.'))
    budget=BudgetedChat(deepseek_chat if live else smoke_chat,max_calls=14,max_tokens=400000)
    rows=[]
    for index,case in enumerate(cases):
        for v in (('v15','v16') if index%2==0 else ('v16','v15')):
            messages,state=prepared[v][case['caseId']]
            chat=RecordedChat(budget,Recorder(output/f"{case['caseId']}-{v}.jsonl",case['caseId'],v))
            start=time.monotonic();row=dict(caseId=case['caseId'],version=v,factHash=sha(facts(messages)))
            try:
                response=await chat(messages)
                candidate=json.loads(response['message']['content'])
                # The schema, validation and tools are identical; only prompt text changed.
                evidence=graph.generation_evidence(state)
                answer,errors=validate_answer(candidate,evidence,state['targets'],state['require_citations'])
                row.update(status='completed',answer=candidate,validationErrors=errors,mechanicalValid=answer is not None,usage=response.get('usage'))
            except Exception as exc:
                row.update(status='failed',error=str(exc))
            row['durationMs']=round((time.monotonic()-start)*1000);rows.append(row)
            write_json(output/'rows.json',rows);write_json(output/'budget.json',budget.summary())
            print(case['caseId'],v,row['status'],flush=True)
    # First router choice, same source query / cast / evidence / history in each arm.
    class Captured(Exception): pass
    for v,module in modules.items():
        case=next(c for c in cases if c['caseId']=='source-line');record={}
        async def capture_route(messages, tools=None):
            record.update(messages=messages,tools=tools)
            raise Captured()
        try:
            await module.build_graph(InMemorySaver(),capture_route).ainvoke(
                module.initial_state(copy.deepcopy(case['session']),case['question'],case['mode']),
                {'configurable':{'thread_id':'router-'+v}})
        except Captured: pass
        chat=RecordedChat(budget,Recorder(output/f'router-{v}.jsonl','router-source-line',v))
        try:
            response=await chat(record['messages'],tools=record['tools'])
            write_json(output/f'router-{v}.json',visible(response))
        except Exception as exc: write_json(output/f'router-{v}.json',dict(error=str(exc)))
        write_json(output/'budget.json',budget.summary())
    lines=['# v15 / v16 完整生成对照','', '同模型、同事实输入（卦象/证据/历史），系统与动态指令为实验变量。四组对话、两组边界检查；每组一次，不挑选输出。','']
    for case in cases:
        lines+=['## '+case['caseId'],'',case['question'],'']
        for row in [r for r in rows if r['caseId']==case['caseId']]:
            lines+=['### '+row['version'],'']+[p['text']+'\n' for p in row.get('answer',{}).get('paragraphs',[])]
            if row.get('error'):lines+=[row['error']]
            lines += ['机械检查：'+json.dumps(row.get('validationErrors',[]),ensure_ascii=False),'']
    (output/'answers.md').write_text('\n'.join(lines))
    print(json.dumps(budget.summary()))


if __name__=='__main__':
    parser=argparse.ArgumentParser(__doc__);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--live',action='store_true')
    args=parser.parse_args();asyncio.run(run(args.output,args.live))
