"""Bounded, synthetic-only experiments. No runtime sessions or tuning labels enter prompts.

Each invocation has immutable inputs/config and a fresh output directory. The shared
ledger bounds all stages. `summarize` recomputes costs/counts from raw model events.
"""
import argparse
import asyncio
import copy
import json
import statistics
import subprocess
import time
import types
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from backend import graph
from backend.config import ROOT
from backend.domain import calculate_hexagram, now, sample_cast
from backend.evaluate import BudgetedChat
from backend.knowledge import CURRENT_RELEASE, load_release
from backend.llm import ChatConfig, configured_chat
from backend.prompts import PROMPT_VERSION
from backend.validation import validate_answer
from evaluation.paired import Recorder, RecordedChat, load_cases, sha, smoke_chat, visible, write_json
from evaluation.workflow import rule_route

BASE = ROOT / 'evaluation/reports/autonomous-20261010'

def baseline_graph():
    source=subprocess.check_output(['git','show','b4c73d4:backend/graph.py'],cwd=ROOT).decode()
    module=types.ModuleType('backend.autonomous_baseline_graph')
    exec(compile(source,'b4c73d4:backend/graph.py','exec'),module.__dict__)
    return module
CONFIGS = {
    'flash-off': ChatConfig(model='deepseek-flash', thinking='disabled', max_tokens=4096),
    'flash-low': ChatConfig(model='deepseek-flash', thinking='enabled', reasoning_effort='low', max_tokens=8192),
    'flash-high': ChatConfig(model='deepseek-flash', thinking='enabled', reasoning_effort='high', max_tokens=8192),
    'pro-off': ChatConfig(model='deepseek-v4-pro', thinking='disabled', max_tokens=4096),
    'pro-low': ChatConfig(model='deepseek-v4-pro', thinking='enabled', reasoning_effort='low', max_tokens=8192),
}


def fixture(case):
    if 'session' in case:
        return copy.deepcopy(case)
    cast = sample_cast() | calculate_hexagram(case['lineValues'])
    cast.update(id='cast-' + case['caseId'], createdAt='2026-10-10T00:00:00Z', algorithmVersion='evaluation-fixed-lines')
    cast.pop('coins', None)
    session = dict(id='session-' + case['caseId'], question=case['question'], cast=cast,
                   releaseId=CURRENT_RELEASE, turns=[], createdAt='2026-10-10T00:00:00Z')
    for i, turn in enumerate(case.get('history', [])):
        answer = turn.get('answer') or dict(status='answered', paragraphs=[dict(text=turn.get('assistant', ''), citations=[])], limitations=[])
        session['turns'].append(dict(id=f'fixture-{i}', question=turn.get('user', turn.get('question', '')),
            status='completed', mode='initial' if i == 0 else 'followup', answer=answer, evidence=turn.get('evidence', [])))
    # Deliberate whitelist: labels/gold never leave the fixture file.
    return dict(caseId=case['caseId'], session=session, question=case.get('currentQuestion', case['question']), mode=case.get('mode', 'initial'))


async def prepare(case, policy=None):
    captures = []
    async def capture(messages, tools=None):
        assert tools is None
        captures.append(copy.deepcopy(messages))
        return await smoke_chat(messages)
    state = await graph.build_graph(InMemorySaver(), capture, route_policy=policy or (lambda state: {'role':'assistant', 'content':'{"ready":true}'})).ainvoke(
        graph.initial_state(copy.deepcopy(case['session']), case['question'], case['mode']),
        {'configurable': {'thread_id': case['caseId']}})
    if not captures:
        raise ValueError('NO_GENERATION:' + case['caseId'])
    return captures[0], state


def budget_for(config, max_calls=90, *, production_repair=False):
    path = BASE / 'ledger.json'
    repair = ChatConfig(model=CONFIGS[config].model, thinking='disabled', max_tokens=8192) if production_repair else None
    budget = BudgetedChat(configured_chat(CONFIGS[config], repair_config=repair), max_calls=max_calls, max_tokens=900000)
    if path.exists():
        old = json.loads(path.read_text())
        budget.calls, budget.reported, budget.accounted, budget.unknown = (old[k] for k in ('calls','reportedTokens','accountedTokens','missingUsageCalls'))
    return budget


def snapshot(out, cases, stage, arms):
    paths = list((ROOT/'backend').glob('*.py')) + [Path(__file__), ROOT/'evaluation/paired.py', ROOT/'evaluation/workflow.py', ROOT/'requirements.txt']
    for p in paths:
        dest=out/'source'/p.relative_to(ROOT); dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(p.read_bytes())
    write_json(out/'inputs.json', cases)
    write_json(out/'manifest.json', dict(stage=stage, createdAt=now(), commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(),
        dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT)), graphVersion=graph.GRAPH_VERSION,
        promptVersion=PROMPT_VERSION, releaseId=CURRENT_RELEASE, caseHash=sha(cases), arms=arms,
        modelOptions={k:configured_chat(v).request_payload([]) for k,v in CONFIGS.items()},
        fileHashes={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in paths},
        order='alternate/reverse arms by case index, serial; one sample per cell; no best-of',
        budget={'globalCalls':145 if stage=='confirmation' else 120 if stage=='final' else 96 if stage=='release' else 90,'globalReportedOrReservedTokens':900000},
        caveats=['Synthetic inputs only; no reasoning text exported','Provider alias/cache/time drift remain','Semantic quality requires review; mechanical validity is not entailment']))


def answers(out, rows, cases):
    lines=['# 完整回答与输入','', '每个单元一次生成，失败保留。分数另列，机械有效不表示语义正确。','']
    for case in cases:
        lines += ['## '+case['caseId'], '', '**核心问题：** '+case['session']['question'], '', '**当前问题：** '+case['question'], '',
                  '**卦象：** '+json.dumps(case['session']['cast'],ensure_ascii=False), '', '**历史：** '+json.dumps(graph.history(case['session']),ensure_ascii=False), '']
        for row in [r for r in rows if r['caseId']==case['caseId']]:
            lines += ['### '+row['arm'], '', '状态：'+row['status']+'；错误：'+json.dumps(row.get('errors', []),ensure_ascii=False), '']
            lines += [p['text']+'\n' for p in row.get('answer',{}).get('paragraphs', [])]
            if row.get('error'): lines += [row['error'], '']
    (out/'answers.md').write_text('\n'.join(lines))


async def run(stage, config, name):
    out=BASE/name;out.mkdir(parents=True,exist_ok=False)
    if stage in ('screen','finalists'):
        wanted=('lunch-specific','haircut-kan','project-decision','life-correction')
        cases=[fixture(next(c for c in load_cases()['cases'] if c['caseId']==cid)) for cid in wanted]
        arms=['flash-off','flash-low','flash-high','pro-off'] if stage=='screen' else ['flash-off','pro-low']
    else:
        path=BASE/'fixtures'/('confirmation.json' if stage=='confirmation' else 'holdout.json' if stage=='holdout' else 'development.json')
        if stage=='confirmation':
            assert sha(path.read_bytes()) == 'd422f65224bd4def42fbae9b50f9e88c92659c004b45937477dcd765fed1a954', 'CONFIRMATION_CHANGED'
            assert (BASE/'confirmation-decision.json').exists(), 'FREEZE_CONFIGURATION_BEFORE_CONFIRMATION'
        if stage=='holdout':
            assert sha(path.read_bytes()) == 'd5693980b70ebf4142389d571e94a51f5cd18cc63ac61e41219682fe2f01cf01', 'HOLDOUT_CHANGED'
            assert (BASE/'decision.json').exists(), 'FREEZE_DECISION_BEFORE_HOLDOUT'
        data=json.loads(path.read_text())
        data=data['cases'] if isinstance(data,dict) else data
        cases=[fixture(c) for c in data]
        if stage=='confirmation':
            arms=['flash-low','flash-high']
        elif stage=='rag':
            cases=cases[:3]
            arms=['memory','classic','full']
        elif stage in ('release','final'):
            selected_cases=('dev-lunch','dev-project-correction','dev-exact-gap') if stage=='final' else ('dev-project-correction','dev-exact-gap')
            cases=[c for c in cases if c['caseId'] in selected_cases]
            arms=['final-'+config] if stage=='final' else ['released-high']
        elif stage in ('agent','routing'):
            cases=cases[3:]
            arms=['B','C','D'] if stage=='agent' else ['D-v10','D-current']
        else:
            arms=['baseline-high','candidate']
    snapshot(out,cases,stage,arms)
    if stage in ('final','confirmation'):
        manifest=json.loads((out/'manifest.json').read_text())
        manifest['repairOptions']=configured_chat(ChatConfig(model=CONFIGS[config].model,thinking='disabled',max_tokens=8192)).request_payload([])
        manifest['purpose']='Development verification after diagnosed repair failure; not additional holdout or best-of selection.'
        write_json(out/'manifest.json',manifest)
    old_graph=baseline_graph() if stage in ('routing','holdout') else None
    if old_graph:
        (out/'source/baseline-graph.py').write_bytes(subprocess.check_output(['git','show','b4c73d4:backend/graph.py'],cwd=ROOT))
    rows=[]
    for i,case in enumerate(cases):
        original,state=await prepare(case)
        prepared_evidence=graph.generation_evidence(state)
        for arm in (arms if i%2==0 else list(reversed(arms))):
            selected=arm if stage in ('screen','finalists','confirmation') else 'flash-high' if arm=='baseline-high' else config
            budget=budget_for(selected, max_calls=145 if stage=='confirmation' else 120 if stage=='final' else 96 if stage=='release' else 90, production_repair=stage in ('final','confirmation'))
            rec=Recorder(out/f"{case['caseId']}-{arm}.jsonl",case['caseId'],arm)
            chat=RecordedChat(budget,rec)
            start=time.monotonic()
            row=dict(caseId=case['caseId'],arm=arm,config=selected,stage=stage,
                     initialEvidenceIds=[e['id'] for e in state['evidence']], inputHash=sha(original[1:]))
            try:
                if stage in ('agent','routing','holdout','release','final','confirmation'):
                    route=(lambda s: {'role':'assistant','content':'{"ready":true}'}) if arm=='B' else rule_route if arm=='C' else None
                    module=old_graph if arm in ('D-v10','baseline-high') else graph
                    row['graphVersion']=module.GRAPH_VERSION
                    result=await module.build_graph(InMemorySaver(),chat,route_policy=route,observe=rec).ainvoke(
                        module.initial_state(copy.deepcopy(case['session']),case['question'],case['mode']),
                        {'configurable':{'thread_id':case['caseId']+'-'+arm}})
                    row.update(status='completed' if not result.get('errors') else 'validation_failed', answer=result['answer'], errors=result.get('errors', []),
                        evidence=result['evidence'],finalEvidenceIds=[e['id'] for e in result['evidence']], trace=result['trace'],
                        repairs=result['repairs'],toolAttempts=result['attempts'],sameCast=result['session']['cast']==case['session']['cast'])
                else:
                    messages=copy.deepcopy(original);evidence=copy.deepcopy(prepared_evidence)
                    requires=state['require_citations'];targets=state['targets']
                    if stage=='rag':
                        if arm=='classic': evidence=[e for e in evidence if e['layer']=='classic']
                        if arm=='memory':
                            evidence=[];requires=False;targets=[]
                            messages[0]['content']=messages[0]['content'].replace('文献解释依据本轮参考原文','文献解释可依据你已有的知识')+'\n本轮未提供可核验文献，citations 使用 []。'
                        context=json.loads(messages[1]['content'])
                        context['参考原文']=[{k:e.get(k) for k in ('id','text','locator','workTitle','authorNames','layer')} for e in evidence]
                        if arm=='memory': context['资料说明']=[]
                        messages[1]['content']=graph.dumps(context)
                    row['visibleEvidenceIds']=[e['id'] for e in evidence]
                    row['actualInputHash']=sha(messages)
                    response=await chat(messages)
                    candidate=json.loads(response['message']['content'])
                    answer,errors=validate_answer(candidate,evidence,targets,requires)
                    row.update(status='completed' if answer else 'validation_failed',answer=candidate,errors=errors,usage=response.get('usage'),evidence=evidence)
            except Exception as exc:
                row.update(status='failed',error=type(exc).__name__+':'+str(exc))
            row['durationMs']=round((time.monotonic()-start)*1000)
            rows.append(row)
            write_json(out/'rows.json',rows);write_json(BASE/'ledger.json',budget.summary())
            answers(out,rows,cases)
            print(case['caseId'],arm,row['status'],row['durationMs'],flush=True)
    summarize(out)



async def conversation(name, config):
    """One real three-turn chain; later inputs include actual prior outputs."""
    out=BASE/name;out.mkdir(parents=True,exist_ok=False)
    seed=fixture(dict(caseId='conversation',question='我想下个月搬家，老房子通勤久，新房租金贵五百，但还没算搬家成本。该怎么权衡？',
        mode='initial',lineValues=[8,7,8,9,8,8],history=[]))
    session=seed['session']
    questions=[seed['question'],'纠正一下，新房不是每月贵五百，是每月便宜五百；我最在意的是搬家会占掉准备面试的周末。',
               '我决定先不搬。那这个周末只做一件事，你觉得先做什么？']
    cases=[];rows=[]
    snapshot(out,[seed],'continuous', ['candidate'])
    for i,question in enumerate(questions):
        case=dict(caseId=f'conversation-{i+1}',session=copy.deepcopy(session),question=question,mode='initial' if i==0 else 'followup')
        cases.append(case);write_json(out/'inputs.json',cases)
        budget=budget_for(config);rec=Recorder(out/f"{case['caseId']}-candidate.jsonl",case['caseId'],'candidate')
        start=time.monotonic();row=dict(caseId=case['caseId'],arm='candidate',config=config,stage='continuous')
        try:
            result=await graph.build_graph(InMemorySaver(),RecordedChat(budget,rec),observe=rec).ainvoke(
                graph.initial_state(copy.deepcopy(session),question,case['mode']),{'configurable':{'thread_id':case['caseId']}})
            row.update(status='completed' if not result.get('errors') else 'validation_failed',answer=result['answer'],errors=result.get('errors',[]),
                trace=result['trace'],evidence=result['evidence'],sameCast=result['session']['cast']==seed['session']['cast'],
                initialEvidenceIds=next(e['evidenceIds'] for e in result['trace'] if e['step']=='retrieve'),
                finalEvidenceIds=[e['id'] for e in result['evidence']])
            if row['status']=='completed':
                session['turns'].append(dict(id=case['caseId'],status='completed',question=question,mode=case['mode'],answer=result['answer'],evidence=result['evidence']))
        except Exception as exc:row.update(status='failed',error=type(exc).__name__+':'+str(exc))
        row['durationMs']=round((time.monotonic()-start)*1000);rows.append(row)
        write_json(out/'rows.json',rows);write_json(BASE/'ledger.json',budget.summary());answers(out,rows,cases)
        print(case['caseId'],row['status'],flush=True)
    manifest=json.loads((out/'manifest.json').read_text());manifest['caseHash']=sha(cases);manifest['historyMode']='actual own previous outputs; not a fixed-input comparison';write_json(out/'manifest.json',manifest)
    summarize(out)


def summarize(out):
    rows=json.loads((out/'rows.json').read_text());groups={}
    for row in rows:
        events=[json.loads(line) for line in (out/f"{row['caseId']}-{row['arm']}.jsonl").read_text().splitlines()]
        calls=[e for e in events if e['kind']=='model_result' and e.get('budgetCounted')]
        usage=[e.get('response',{}).get('usage') or {} for e in calls]
        def complete_sum(values):
            return sum(values) if all(v is not None for v in values) else None
        tool_results=[e for e in events if e['kind']=='tool_result']
        row['toolCounts']={status:sum(e.get('executionStatus')==status for e in tool_results) for status in ('ok','empty','rejected')}
        row['toolCounts']['budgetCounted']=sum(e.get('budgetCounted',False) for e in tool_results)
        row['auto']=dict(calls=len(calls),reportedTokens=sum(u.get('total_tokens') or 0 for u in usage),
            unknownUsage=sum(u.get('total_tokens') is None for u in usage),
            promptTokens=complete_sum([u.get('prompt_tokens') for u in usage]),completionTokens=complete_sum([u.get('completion_tokens') for u in usage]),
            reasoningTokens=complete_sum([(u.get('completion_tokens_details') or {}).get('reasoning_tokens') for u in usage]),
            toolRequests=sum(e['kind']=='tool_request' for e in events))
        groups.setdefault(row['arm'],[]).append(row)
    summary={arm:dict(n=len(rs),completed=sum(r['status']=='completed' for r in rs),
        medianMs=statistics.median(r['durationMs'] for r in rs),
        **{key:sum(r['auto'][key] for r in rs) if all(r['auto'][key] is not None for r in rs) else None for key in ('calls','reportedTokens','unknownUsage','promptTokens','completionTokens','reasoningTokens','toolRequests')}) for arm,rs in groups.items()}
    write_json(out/'rows.json',rows);write_json(out/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(__doc__);p.add_argument('stage',choices=['screen','finalists','rag','agent','routing','holdout','continuous','release','final','confirmation','summarize']);p.add_argument('--config',choices=list(CONFIGS),default='flash-off');p.add_argument('--name',required=True)
    a=p.parse_args()
    if a.stage=='summarize':summarize(BASE/a.name)
    elif a.stage=='continuous':asyncio.run(conversation(a.name,a.config))
    else:asyncio.run(run(a.stage,a.config,a.name))
