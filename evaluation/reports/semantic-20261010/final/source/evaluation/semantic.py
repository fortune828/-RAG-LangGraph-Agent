"""Controlled semantic experiments over synthetic inputs; no production sessions.

Use existing graph preparation, evidence validation, bounded model adapter and
visible trace recorder. Candidate context transforms here are offline treatments.
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

from backend.config import ROOT
from backend.domain import now
from backend.evaluate import BudgetedChat
from backend.knowledge import CURRENT_RELEASE
from backend.llm import ChatConfig, configured_chat
from backend.validation import validate_answer
from evaluation.autonomous import fixture, prepare, answers
from evaluation.paired import Recorder, RecordedChat, sha, write_json

BASE = ROOT / 'evaluation/reports/semantic-20261010'
CONFIG = ChatConfig(model='deepseek-flash', thinking='enabled', reasoning_effort='low', max_tokens=8192)
CALL_CAP = 90
TOKEN_CAP = 700000


def budget(*, repair=False, config=CONFIG):
    result = BudgetedChat(configured_chat(config, repair_config=ChatConfig(model=config.model, thinking='disabled', max_tokens=8192) if repair else None), max_calls=CALL_CAP, max_tokens=TOKEN_CAP)
    if (BASE/'ledger.json').exists():
        previous = json.loads((BASE/'ledger.json').read_text())
        result.calls, result.reported, result.accounted, result.unknown = (
            previous[k] for k in ('calls','reportedTokens','accountedTokens','missingUsageCalls'))
    return result


def context_variant(messages, arm):
    messages = copy.deepcopy(messages)
    if arm == 'baseline':
        return messages
    context = json.loads(messages[1]['content'])
    current = context.pop('当前问题')
    history = context.pop('近期对话', [])
    context.pop('本轮阶段', None)
    if arm in ('grouped', 'core'):
        # Explicit source roles/anchors, without adding generated interpretations.
        by_id = {e['id']:e for e in __import__('backend.knowledge',fromlist=['load_release']).load_release()[0]}
        records = [by_id[e['id']] for e in context['参考原文']]
        if arm == 'core':
            original = records[0]['hexagramId'] if records else None
            records = [e for e in records if e['layer']=='classic' and e['hexagramId']==original]
        groups = {}
        for e in records:
            anchor = (e['hexagramId'],e['section'],e.get('line'))
            group = groups.setdefault(anchor, dict(卦号=e['hexagramId'],章节=e['section'],爻位=e.get('line'),原典=[],历代注解=[]))
            key='原典' if e['layer']=='classic' else '历代注解'
            group[key].append({k:e.get(k) for k in ('id','text','locator','workTitle','authorNames','layer')})
        context['参考原文'] = list(groups.values())
    out=[messages[0],dict(role='user',content=json.dumps({'本轮参考资料':context},ensure_ascii=False))]
    for turn in history:
        out.extend([dict(role='user',content=turn['user']),dict(role='assistant',content=turn['assistant'])])
    out.append(dict(role='user',content=current))
    return out


def effective_evidence(messages, state):
    if len(messages)==2:
        ids=[e['id'] for e in json.loads(messages[1]['content']).get('参考原文',[])]
    else:
        entries=json.loads(messages[1]['content'])['本轮参考资料'].get('参考原文',[])
        ids=[e['id'] for entry in entries for e in (entry.get('原典',[])+entry.get('历代注解',[]) if '原典' in entry else [entry])]
    return [e for e in state['evidence'] if e['id'] in ids]


def snapshot(out, cases, arms, stage):
    paths=list((ROOT/'backend').glob('*.py'))+[Path(__file__),ROOT/'evaluation/paired.py',ROOT/'evaluation/autonomous.py',ROOT/'requirements.txt']
    for p in paths:
        target=out/'source'/p.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(p.read_bytes())
    write_json(out/'inputs.json',cases)
    write_json(out/'manifest.json',dict(stage=stage,createdAt=now(),commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        graphVersion=__import__('backend.graph',fromlist=['GRAPH_VERSION']).GRAPH_VERSION,promptVersion='evidence-agent-python-v16',releaseId=CURRENT_RELEASE,caseHash=sha(cases),arms=arms,
        configuration=configured_chat(CONFIG).request_payload([]),callCap=CALL_CAP,tokenCap=TOKEN_CAP,
        files={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in paths},
        design=('Single generation per cell; no repair; alternate arm order.' if stage in ('context','rag','static') else 'End-to-end graph with at most one repair; alternate arm order.'),
        dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT)),
        labels='AI proposed semantic references never enter model messages; synthetic inputs only.'))


async def run(stage):
    out=BASE/stage;out.mkdir(parents=True,exist_ok=False)
    if stage=='context':
        old=ROOT/'evaluation/reports/autonomous-20261010/fixtures/development.json'
        cases=[fixture(c) for c in json.loads(old.read_text())['cases'][:3]]
        wedding=json.loads((ROOT/'evaluation/reports/autonomous-20261010/confirmation/inputs.json').read_text())[1]
        cases.append(wedding)
        arms=['baseline','roles','grouped','core']
    else:
        raise ValueError('unsupported stage')
    snapshot(out,cases,arms,stage);rows=[]
    for i,case in enumerate(cases):
        original,state=await prepare(case)
        for arm in (arms if i%2==0 else list(reversed(arms))):
            model=budget();rec=Recorder(out/f"{case['caseId']}-{arm}.jsonl",case['caseId'],arm)
            messages=context_variant(original,arm);evidence=effective_evidence(messages,state)
            row=dict(caseId=case['caseId'],arm=arm,config='flash-low',stage=stage,evidence=evidence,
                initialEvidenceIds=[e['id'] for e in state['evidence']],visibleEvidenceIds=[e['id'] for e in evidence],inputHash=sha(messages))
            start=time.monotonic()
            try:
                rec(dict(kind='model_phase',phase='generate'))
                response=await RecordedChat(model,rec)(messages)
                candidate=json.loads(response['message']['content'])
                answer,errors=validate_answer(candidate,evidence,state['targets'],state['require_citations'])
                row.update(status='completed' if answer else 'validation_failed',answer=candidate,errors=errors)
            except Exception as exc:
                row.update(status='failed',error=type(exc).__name__+':'+str(exc))
            row['durationMs']=round((time.monotonic()-start)*1000);rows.append(row)
            write_json(out/'rows.json',rows);write_json(BASE/'ledger.json',model.summary());answers(out,rows,cases)
            print(case['caseId'],arm,row['status'],row['durationMs'],flush=True)
    summarize(out)


def baseline_module():
    commit='f277be7f880c368cd549e0f956555be982975a34'
    modules={}
    for name in ('validation','knowledge','graph'):
        source=subprocess.check_output(['git','show',f'{commit}:backend/{name}.py'],cwd=ROOT).decode()
        module=types.ModuleType('backend.semantic_baseline_'+name)
        exec(compile(source,f'{commit}:backend/{name}.py','exec'),module.__dict__)
        modules[name]=module
    modules['graph'].validate_answer=modules['validation'].validate_answer
    modules['graph'].initial_evidence=modules['knowledge'].initial_evidence
    return modules['graph']


async def run_graph(stage):
    from backend import graph
    from langgraph.checkpoint.memory import InMemorySaver
    from evaluation.workflow import rule_route
    out=BASE/stage;out.mkdir(parents=True,exist_ok=False)
    if stage=='final':
        assert (BASE/'decision.json').exists(), 'FREEZE_CANDIDATE_FIRST'
        path=BASE/'fixtures/holdout.json'
        assert sha(path.read_bytes())=='bb94659e4cab0ebcaabe678d2e7a12f4fba9ba1457563e746f39e4d702608a8d'
        cases=[fixture(c) for c in json.loads(path.read_text())['cases']]
        arms=['baseline','candidate']
    else:
        data=json.loads((BASE/'fixtures/development.json').read_text())['cases']
        cases=[fixture(c) for c in data if c['caseId'] in ('semantic-responsibility','semantic-literal','semantic-fuzzy')]
        cases.append(fixture(json.loads((ROOT/'evaluation/reports/autonomous-20261010/fixtures/development.json').read_text())['cases'][2]))
        arms=['baseline','hybrid','C']
    snapshot(out,cases,arms,stage);rows=[];old=baseline_module()
    for name in ('knowledge','validation','graph'):
        (out/f'source/baseline-{name}.py').write_bytes(subprocess.check_output(['git','show',f'f277be7:backend/{name}.py'],cwd=ROOT))
    candidate=archived_candidate() if stage=='hybrid' else graph
    for i,case in enumerate(cases):
        for arm in (arms if i%2==0 else list(reversed(arms))):
            module=old if arm=='baseline' else candidate
            model=budget(repair=True);rec=Recorder(out/f"{case['caseId']}-{arm}.jsonl",case['caseId'],arm)
            row=dict(caseId=case['caseId'],arm=arm,config='flash-low',stage=stage,graphVersion=module.GRAPH_VERSION)
            start=time.monotonic();kwargs={'route_policy':rule_route} if arm=='C' else {'answer_with_tools':True} if arm=='hybrid' else {}
            try:
                result=await module.build_graph(InMemorySaver(),RecordedChat(model,rec),observe=rec,**kwargs).ainvoke(
                    module.initial_state(copy.deepcopy(case['session']),case['question'],case['mode']),
                    {'configurable':{'thread_id':case['caseId']+'-'+arm},'recursion_limit':30})
                row.update(status='completed' if not result.get('errors') else 'validation_failed',answer=result['answer'],errors=result.get('errors',[]),
                    evidence=result['evidence'],trace=result['trace'],repairs=result['repairs'],toolAttempts=result['attempts'],sameCast=result['session']['cast']==case['session']['cast'])
            except Exception as exc:
                row.update(status='failed',error=type(exc).__name__+':'+str(exc))
            prepared=next(e for e in rec.events if e['kind']=='prepare')
            row.update(initialEvidenceIds=[e['id'] for e in prepared['evidence']],durationMs=round((time.monotonic()-start)*1000))
            if 'evidence' not in row:row['evidence']=prepared['evidence']
            rows.append(row);write_json(out/'rows.json',rows);write_json(BASE/'ledger.json',model.summary());answers(out,rows,cases)
            print(case['caseId'],arm,row['status'],row['durationMs'],flush=True)
    summarize(out)


async def run_rag():
    from backend import graph
    from evaluation.workflow import rule_route
    out=BASE/'rag';out.mkdir(parents=True,exist_ok=False)
    cases=[fixture(c) for c in json.loads((BASE/'fixtures/rag-development.json').read_text())['cases']]
    arms=['memory','classic','full'];snapshot(out,cases,arms,'rag');rows=[]
    for i,case in enumerate(cases):
        original,state=await prepare(case,policy=rule_route)
        for arm in (arms if i%2==0 else list(reversed(arms))):
            model=budget();rec=Recorder(out/f"{case['caseId']}-{arm}.jsonl",case['caseId'],arm)
            messages=copy.deepcopy(original);evidence=graph.generation_evidence(state)
            targets=state['targets'];requires=state['require_citations']
            if arm=='classic':evidence=[e for e in evidence if e['layer']=='classic']
            if arm=='memory':
                evidence=[];targets=[];requires=False
                messages[0]['content']=messages[0]['content'].replace('文献解释依据本轮参考原文','文献解释可依据你已有的知识')+'\n本轮未提供可核验文献，citations 使用 []。'
            context=json.loads(messages[1]['content']);context['参考原文']=[{k:e.get(k) for k in ('id','text','locator','workTitle','authorNames','layer')} for e in evidence]
            if arm=='memory':context['资料说明']=[]
            messages[1]['content']=graph.dumps(context)
            row=dict(caseId=case['caseId'],arm=arm,config='flash-low',stage='rag',evidence=evidence,visibleEvidenceIds=[e['id'] for e in evidence])
            start=time.monotonic()
            try:
                rec(dict(kind='model_phase',phase='generate'));response=await RecordedChat(model,rec)(messages)
                candidate=json.loads(response['message']['content']);resolutions=[]
                answer,errors=validate_answer(candidate,evidence,targets,requires,quote_resolutions=resolutions)
                row.update(status='completed' if answer else 'validation_failed',answer=answer or candidate,errors=errors,quoteResolutions=resolutions)
            except Exception as exc:row.update(status='failed',error=type(exc).__name__+':'+str(exc))
            row['durationMs']=round((time.monotonic()-start)*1000);rows.append(row)
            write_json(out/'rows.json',rows);write_json(BASE/'ledger.json',model.summary());answers(out,rows,cases)
            print(case['caseId'],arm,row['status'],row['durationMs'],flush=True)
    summarize(out)


def archived_candidate():
    source_root=ROOT/'evaluation/reports/semantic-20261010/hybrid/source/backend'
    modules={}
    for name in ('knowledge','validation','graph'):
        module=types.ModuleType('backend.archived_semantic_'+name)
        source=(source_root/f'{name}.py').read_text()
        exec(compile(source,str(source_root/f'{name}.py'),'exec'),module.__dict__)
        modules[name]=module
    modules['graph'].validate_answer=modules['validation'].validate_answer
    modules['graph'].initial_evidence=modules['knowledge'].initial_evidence
    return modules['graph']


async def run_static():
    from backend import graph
    out=BASE/'static';out.mkdir(parents=True,exist_ok=False)
    source=next(c for c in json.loads((BASE/'fixtures/development.json').read_text())['cases'] if c['caseId']=='semantic-responsibility')
    source['currentQuestion']='我刚核对了记录，两次都是我把基础数据给晚了，他后面其实一直在赶。我不能再把责任推给搭档，怎么说明我的数据给晚了？'
    case=fixture(source);case['caseId']='static-word-overlap';cases=[case];arms=['word_overlap','anchored']
    original,state=await prepare(case);old=baseline_module();snapshot(out,cases,arms,'static');rows=[]
    for arm in arms:
        evidence=old.initial_evidence(case['session']['cast'],case['question']) if arm=='word_overlap' else state['evidence']
        messages=copy.deepcopy(original);context=json.loads(messages[1]['content'])
        context['参考原文']=[{k:e.get(k) for k in ('id','text','locator','workTitle','authorNames','layer')} for e in evidence]
        messages[1]['content']=graph.dumps(context)
        model=budget();rec=Recorder(out/f"{case['caseId']}-{arm}.jsonl",case['caseId'],arm)
        row=dict(caseId=case['caseId'],arm=arm,config='flash-low',stage='static',evidence=evidence,visibleEvidenceIds=[e['id'] for e in evidence]);start=time.monotonic()
        try:
            rec(dict(kind='model_phase',phase='generate'));result=await RecordedChat(model,rec)(messages)
            raw=json.loads(result['message']['content']);answer,errors=validate_answer(raw,evidence,state['targets'],state['require_citations'])
            row.update(status='completed' if answer else 'validation_failed',answer=answer or raw,errors=errors)
        except Exception as exc:row.update(status='failed',error=type(exc).__name__+':'+str(exc))
        row['durationMs']=round((time.monotonic()-start)*1000);rows.append(row)
        write_json(out/'rows.json',rows);write_json(BASE/'ledger.json',model.summary());answers(out,rows,cases)
        print(case['caseId'],arm,row['status'],row['durationMs'],flush=True)
    summarize(out)


async def run_repair():
    out=BASE/'recovery';out.mkdir(parents=True,exist_ok=False);rows=[];cases=[]
    selection=[('context','dev-project','baseline'),('hybrid','semantic-literal','baseline')]
    for stage,case_id,arm in selection:
        case=next(c for c in json.loads((BASE/stage/'inputs.json').read_text()) if c['caseId']==case_id);cases.append(case)
        events=[json.loads(line) for line in (BASE/stage/f'{case_id}-{arm}.jsonl').read_text().splitlines()]
        failure=next(e for e in events if e['kind']=='model_result' and e.get('error')=='MODEL_INCOMPLETE_RESPONSE')
        request=next(e for e in events if e['kind']=='model_request' and e['callId']==failure['callId'])
        messages=copy.deepcopy(request['payload']['messages']);context=json.loads(messages[1]['content'])
        ids={e['id'] for e in context['参考原文']}
        from backend.knowledge import load_release
        evidence=[e for e in load_release()[0] if e['id'] in ids]
        from backend.validation import requested_targets
        partial=failure['response']['message'].get('content')
        if partial:messages.append(dict(role='assistant',content=partial))
        messages.append(dict(role='user',content=json.dumps(dict(instruction='上一响应因长度上限截断，请返回完整 JSON。',errors=[dict(code='MODEL_INCOMPLETE_RESPONSE',path=[],reason='响应因长度上限截断，需要完整 JSON。')]),ensure_ascii=False)))
        model=budget(config=ChatConfig(model=CONFIG.model,thinking='disabled',max_tokens=8192));rec=Recorder(out/f'{case_id}-repair.jsonl',case_id,'repair');row=dict(caseId=case_id,arm='repair',config='flash-off-8192',stage='recovery',evidence=evidence,originalFailure=f'{stage}/{case_id}-{arm}.jsonl',originalCallId=failure['callId']);start=time.monotonic()
        try:
            rec(dict(kind='model_phase',phase='repair'));response=await RecordedChat(model,rec)(messages)
            raw=json.loads(response['message']['content']);resolutions=[]
            answer,errors=validate_answer(raw,evidence,requested_targets(case['question'],case['session']['cast']),True,quote_resolutions=resolutions)
            row.update(status='completed' if answer else 'validation_failed',answer=answer or raw,errors=errors,quoteResolutions=resolutions)
        except Exception as exc:row.update(status='failed',error=type(exc).__name__+':'+str(exc))
        row['durationMs']=round((time.monotonic()-start)*1000);rows.append(row)
        write_json(out/'rows.json',rows);write_json(BASE/'ledger.json',model.summary());answers(out,rows,cases)
        print(case_id,'repair',row['status'],row['durationMs'],flush=True)
    snapshot(out,cases,['repair'],'recovery');summarize(out)


def summarize(out):
    rows=json.loads((out/'rows.json').read_text());summary={}
    for row in rows:
        events=[json.loads(line) for line in (out/f"{row['caseId']}-{row['arm']}.jsonl").read_text().splitlines()]
        calls=[e for e in events if e['kind']=='model_result' and e.get('budgetCounted')]
        usage=[e.get('response',{}).get('usage') or {} for e in calls]
        row['auto']=dict(calls=len(calls),reportedTokens=sum(u.get('total_tokens') or 0 for u in usage),unknownUsage=sum(u.get('total_tokens') is None for u in usage))
    for arm in {r['arm'] for r in rows}:
        group=[r for r in rows if r['arm']==arm]
        summary[arm]=dict(n=len(group),completed=sum(r['status']=='completed' for r in group),medianMs=statistics.median(r['durationMs'] for r in group),
            **{k:sum(r['auto'][k] for r in group) for k in ('calls','reportedTokens','unknownUsage')})
    write_json(out/'rows.json',rows);write_json(out/'summary.json',summary);print(summary)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage');args=p.parse_args();asyncio.run(run(args.stage) if args.stage=='context' else run_rag() if args.stage=='rag' else run_static() if args.stage=='static' else run_repair() if args.stage=='recovery' else run_graph(args.stage))
